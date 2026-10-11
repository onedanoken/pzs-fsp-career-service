"""Golden UI/cache transitions, mutants, trace coverage and frozen content."""
import copy
import itertools
import json
import random
from unittest.mock import Mock

from fastapi import HTTPException
import pytest

from backend import resource_cache as cache
from backend.item_generator import generate, grade_answer, public_item
from backend.work_samples import add_journey, generate_case, generate_legacy_case


def item_for(grade='Junior', variant=None, ttl=10, filters=None):
    return cache.generate(grade, Mock(choice=Mock(return_value=ttl), sample=Mock(return_value=filters or ['open', 'closed'])), variant or cache.VARIANTS[grade][0])


@pytest.mark.parametrize('grade,variant', [(g, v) for g, variants in cache.VARIANTS.items() for v in variants])
def test_all_authored_parameter_combinations_have_solutions_and_detect_defects(grade, variant):
    for ttl, filters in itertools.product([10, 30, 60], itertools.combinations(['open', 'closed', 'archived'], 2)):
        item = item_for(grade, variant, ttl, list(filters))
        assert grade_answer(item, item['private']['reference_answer'])['all_checks_passed']
        assert not grade_answer(item, item['starter'])['all_checks_passed']


def test_page_collision_has_a_concrete_visible_counterexample():
    item = item_for(variant='page_collision')
    events = [cache.load('a', 'main', 'open'), cache.response('a', ['first']), cache.load('b', 'main', 'open', 2, 2)]
    expected = {'views': {'main': {'filter': 'open', 'page': 2, 'status': 'loading', 'items': []}},
                'cache': [{'filter': 'open', 'page': 1, 'time': 1, 'items': ['first'], 'generation': 0}], 'network': ['a', 'b']}
    assert cache.execute(json.loads(item['private']['reference_answer']), events)[-1] == expected
    assert cache.oracle(events, 10)[-1] == expected
    wrong = cache.execute(json.loads(item['starter']), events)[-1]
    assert wrong['views']['main']['items'] == ['first'] and wrong['network'] == ['a']


def test_error_is_not_cached_but_empty_success_is_cached():
    item = item_for(variant='cached_failure')
    events = [cache.load('a', 'main', 'open'), cache.response('a', status=500), cache.load('b', 'main', 'open', time=2)]
    plan = json.loads(item['private']['reference_answer'])
    states = cache.execute(plan, events)
    assert states[1]['views']['main']['status'] == 'error' and states[1]['cache'] == []
    assert states[-1]['views']['main']['status'] == 'loading' and states[-1]['network'] == ['a', 'b']
    wrong = cache.execute(json.loads(item['starter']), events)[-1]
    assert wrong['views']['main']['status'] == 'ready' and wrong['network'] == ['a']
    states = cache.execute(plan, events + [cache.response('b', [], time=3), cache.load('c', 'main', 'open', time=4)])
    assert states[-1]['views']['main']['status'] == 'ready' and states[-1]['views']['main']['items'] == []
    assert states[-1]['network'] == ['a', 'b']


@pytest.mark.parametrize('age,expected_status', [(9, 'ready'), (10, 'loading'), (11, 'loading')])
def test_ttl_starts_at_response_and_expires_exactly_on_boundary(age, expected_status):
    item = item_for()
    events = [cache.load('a', 'main', 'open'), cache.response('a', time=7), cache.load('b', 'main', 'open', time=7 + age)]
    state = cache.execute(json.loads(item['private']['reference_answer']), events)[-1]
    assert state['views']['main']['status'] == expected_status
    assert state['network'] == (['a'] if age < 10 else ['a', 'b'])


def test_two_views_do_not_share_the_current_request_id():
    item = item_for('Middle', 'global_stream')
    events = [cache.load('a', 'left', 'open'), cache.load('b', 'right', 'closed', 2), cache.response('a', ['left'])]
    expected = cache.execute(json.loads(item['private']['reference_answer']), events)[-1]
    assert expected['views']['left']['status'] == 'ready' and expected['views']['right']['status'] == 'loading'
    assert cache.oracle(events, 10)[-1] == expected
    wrong = cache.execute(json.loads(item['starter']), events)[-1]
    assert wrong['views']['left']['status'] == 'loading'


def test_transient_obsolete_data_is_rejected_even_when_final_state_recovers(monkeypatch):
    item = item_for('Middle', 'obsolete_generation')
    events = [cache.load('a', 'main', 'open'), {'type': 'invalidate', 'filter': 'open'},
              cache.response('a', ['obsolete'], time=2), cache.load('b', 'main', 'open', time=20), cache.response('b', ['current'], time=21)]
    wrong = json.loads(item['starter'])
    actual, expected = cache.execute(wrong, events), cache.oracle(events, 10)
    assert actual[-1] == expected[-1]
    assert actual[2]['views']['main']['items'] == ['obsolete']
    assert expected[2]['views']['main']['status'] == 'stale' and expected[2]['cache'] == []
    monkeypatch.setattr(cache, 'scenarios', lambda _: {'invalidation_epoch': [events]})
    assert cache.evaluate(wrong, item['private']['condition']) == {'invalidation_epoch': False}


def test_cancelled_reply_cannot_pollute_cache_and_other_view_continues():
    item = item_for('Middle')
    events = [cache.load('a', 'left', 'open'), cache.load('b', 'right', 'closed', 2),
              {'type': 'cancel', 'view': 'left'}, cache.response('a', ['obsolete']), cache.response('b', ['keep'], time=2)]
    state = cache.execute(json.loads(item['private']['reference_answer']), events)[-1]
    assert state['views']['left']['status'] == 'idle' and state['views']['left']['items'] == []
    assert state['views']['right']['items'] == ['keep']
    assert state['cache'] == [{'filter': 'closed', 'page': 2, 'time': 2, 'items': ['keep'], 'generation': 0}]
    assert cache.oracle(events, 10)[-1] == state


@pytest.mark.parametrize('grade', ['Junior', 'Middle'])
def test_checker_accepts_equivalent_keys_and_is_independent_of_saved_reference(grade):
    item = item_for(grade)
    plan = json.loads(item['private']['reference_answer'])
    plan['key'].reverse()
    item['private']['reference_answer'] = '{}'
    assert grade_answer(item, json.dumps(plan))['all_checks_passed']
    mutants = [('key', ['filter']), ('key', ['page']), ('cache_statuses', [200, 500]),
               ('cache_statuses', [500]), ('reuse', 'any'), ('ttl_seconds', 9), ('ttl_seconds', 11)]
    if grade == 'Middle':
        mutants += [('streams', 'global'), ('invalidated', 'accept'), ('cancelled', 'accept')]
    for field, value in mutants:
        wrong = copy.deepcopy(plan)
        wrong[field] = value
        assert not grade_answer(item, json.dumps(wrong))['all_checks_passed']
    for malformed in ['{}', '[]', '{"key":[],"key":[]}', 'eval(1)', 'x' * 5001]:
        assert grade_answer(item, malformed)['score'] == 0
    for field, value in [('key', ['filter', 'filter']), ('key', ['__proto__']), ('cache_statuses', [200.0]), ('ttl_seconds', True), ('checks', cache.CHECKS)]:
        wrong = copy.deepcopy(plan)
        wrong[field] = value
        assert grade_answer(item, json.dumps(wrong))['score'] == 0


def test_new_family_is_additional_and_old_frontend_remains_evaluable():
    for grade in ['Junior', 'Middle']:
        cases = [generate_case('frontend', grade, random.Random(seed)) for seed in range(100)]
        assert {x['family'] for x in cases} == {cache.FAMILY, 'frontend:request_state_repair'}
        assert {x['private']['condition']['variant'] for x in cases if x['family'] == cache.FAMILY} == set(cache.VARIANTS[grade])
        old = generate_legacy_case('frontend', grade, random.Random(1))
        assert grade_answer(old, old['private']['reference_answer'])['all_checks_passed']
        assert old['version'] == 'work-cases-2026-v1'
    for grade in ['Intern', 'Senior']:
        assert generate_case('frontend', grade, random.Random(1))['family'] == 'frontend:request_state_repair'


@pytest.mark.parametrize('grade', ['Junior', 'Middle'])
def test_signature_freezes_conditions_and_subject_rubric_without_exposing_keys(monkeypatch, grade):
    from backend import work_samples
    base = generate('frontend', grade, allow_neural=False)
    item = item_for(grade)
    def issued(value):
        monkeypatch.setattr(work_samples, 'generate_case', lambda *_: copy.deepcopy(value))
        return add_journey(copy.deepcopy(base), 'frontend', grade)
    original = issued(item)
    assert original['assessment_path']['rubric'] == cache.rubric(grade)
    assert original['items'][-1]['private']['rubric_criteria'] == cache.rubric(grade)
    public = public_item(original['items'][-2])
    assert 'private' not in public and 'reference_answer' not in json.dumps(public)
    for field in ['ttl_seconds', 'filters', 'pages', 'variant', 'model_version']:
        changed = copy.deepcopy(item)
        changed['private']['condition'][field] = 'changed-condition'
        assert issued(changed)['condition_signature'] != original['condition_signature']
    for field in ['version', 'rubric_version']:
        changed = copy.deepcopy(item)
        changed[field] = 'next-version'
        assert issued(changed)['condition_signature'] != original['condition_signature']
    changed = copy.deepcopy(item)
    changed['review_rubric'][0]['anchors'][2] += ' New observation.'
    assert issued(changed)['condition_signature'] != original['condition_signature']
    changed = copy.deepcopy(item)
    changed['id'] = 'other-id'
    assert issued(changed)['condition_signature'] == original['condition_signature']


def test_unsupported_version_is_technical_even_with_malformed_answer():
    for field in ['version', 'model_version']:
        item = item_for()
        (item if field == 'version' else item['private']['condition'])[field] = 'future-version'
        with pytest.raises(HTTPException) as error:
            grade_answer(item, '{')
        assert error.value.status_code == 503
