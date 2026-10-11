"""QA suites must have truthful expectations and detect stateful mutations."""
import copy
import itertools
import json
import random
from unittest.mock import Mock

from fastapi import HTTPException
import pytest

from backend import reservation_test_suite as qa
from backend.item_generator import generate, grade_answer, public_item
from backend.work_samples import add_journey, generate_case, generate_legacy_case


def item_for(grade='Junior', variant=None, limit=4):
    return qa.generate(grade, Mock(choice=Mock(return_value=limit)), variant or qa.VARIANTS[grade][0])


@pytest.mark.parametrize('grade,variant', [(g, v) for g, variants in qa.VARIANTS.items() for v in variants])
def test_every_authored_limit_has_a_truthful_suite_and_rejects_starter(grade, variant):
    for limit in [2, 4, 6]:
        item = item_for(grade, variant, limit)
        assert len(item['private']['reference_answer']) <= 5000
        assert grade_answer(item, item['private']['reference_answer'])['all_checks_passed']
        assert not grade_answer(item, item['starter'])['all_checks_passed']


def test_correct_app_matches_independent_contract_over_finite_single_request_domain():
    for limit in [2, 4, 6]:
        quantities = list(range(-1, limit + 2)) + [True, False, 1.0, 1.5, '1', None]
        for stock, quantity in itertools.product(range(2 * limit + 1), quantities):
            case = {'stock': stock, 'quantity': quantity}
            assert qa.model(case, limit, 'Junior') == qa.oracle(case, limit, 'Junior')


@pytest.mark.parametrize('schedule', ['serial', 'parallel'])
def test_correct_app_matches_contract_for_repeats_conflicts_and_races(schedule):
    for limit in [2, 4, 6]:
        for stock, first, second, second_key in itertools.product(range(2 * limit + 1), [0, 1, limit, limit + 1], [0, 1, limit, limit + 1], ['a', 'b']):
            case = {'stock': stock, 'requests': [{'key': 'a', 'quantity': first}, {'key': second_key, 'quantity': second}], 'schedule': schedule, 'fault_at': 'none'}
            assert qa.model(case, limit, 'Middle') == qa.oracle(case, limit, 'Middle')


def test_golden_replay_conflict_and_race_states_are_not_only_http_codes():
    case = {'stock': 3, 'requests': [{'key': 'a', 'quantity': 2}, {'key': 'a', 'quantity': 2}, {'key': 'a', 'quantity': 1}], 'schedule': 'serial', 'fault_at': 'none'}
    expected = {'statuses': [201, 200, 409], 'stock': 1, 'reserved': {'a': 2}}
    assert qa.oracle(case, 4, 'Middle') == qa.model(case, 4, 'Middle') == expected
    race = {'stock': 3, 'requests': [{'key': 'a', 'quantity': 2}, {'key': 'b', 'quantity': 2}], 'schedule': 'parallel', 'fault_at': 'none'}
    assert qa.model(race, 4, 'Middle') == {'statuses': [201, 409], 'stock': 1, 'reserved': {'a': 2}}
    assert qa.model(race, 4, 'Middle', 'lost_update') == {'statuses': [201, 201], 'stock': 1, 'reserved': {'a': 2, 'b': 2}}


@pytest.mark.parametrize('fault', ['after_debit', 'after_record'])
def test_restart_uses_only_committed_state_and_the_retry_is_new(fault):
    case = {'stock': 3, 'requests': [{'key': 'a', 'quantity': 1}, {'key': 'a', 'quantity': 1}], 'schedule': 'serial', 'fault_at': fault}
    expected = {'statuses': ['interrupted', 201], 'stock': 2, 'reserved': {'a': 1}}
    assert qa.oracle(case, 4, 'Middle') == qa.model(case, 4, 'Middle') == expected
    assert qa.model(case, 4, 'Middle', 'non_atomic') != expected
    invalid_first = copy.deepcopy(case)
    invalid_first['requests'][0]['quantity'] = 0
    assert qa.model(invalid_first, 4, 'Middle') == {'statuses': [422, 201], 'stock': 2, 'reserved': {'a': 1}}
    assert qa.oracle(invalid_first, 4, 'Middle') == qa.model(invalid_first, 4, 'Middle')


def test_copied_wrong_oracle_cannot_count_as_killing_a_mutant():
    item = item_for(variant='copied_expectations')
    suite = json.loads(item['starter'])
    wrong = suite['tests'][1]
    assert wrong['expected'] == {'status': 201, 'stock': 4, 'reserved': 0}
    assert qa.oracle(wrong, 4, 'Junior') == {'status': 422, 'stock': 4, 'reserved': 0}
    suite['tests'] = [wrong]
    checks = qa.evaluate(suite, item['private']['condition'])
    assert not checks['suite_oracle'] and not checks['suite_mutations']
    assert not checks['suite_boundary_types'] and not checks['suite_positive_negative']


@pytest.mark.parametrize('grade', ['Junior', 'Middle'])
def test_duplicate_positive_tests_do_not_replace_coverage(grade):
    item = item_for(grade)
    positive = json.loads(item['private']['reference_answer'])['tests'][0]
    if grade == 'Middle':
        positive['requests'] = positive['requests'][:1]
        positive['expected'] = qa.oracle(positive, 4, grade)
    checks = qa.evaluate({'tests': [positive] * 20}, item['private']['condition'])
    assert checks['suite_oracle'] and not checks['suite_boundary_types']
    assert not checks['suite_positive_negative'] and not checks['suite_mutations']


def test_missing_race_or_one_crash_point_cannot_be_hidden_by_average():
    item = item_for('Middle')
    reference = json.loads(item['private']['reference_answer'])
    for removed, group in [('parallel', 'race_detection'), ('after_debit', 'recovery_detection'), ('after_record', 'recovery_detection')]:
        suite = {'tests': [x for x in reference['tests'] if x['schedule'] != removed and x['fault_at'] != removed]}
        checks = qa.evaluate(suite, item['private']['condition'])
        assert checks['suite_oracle'] and not checks[group]
        assert not grade_answer(item, json.dumps(suite))['all_checks_passed']


@pytest.mark.parametrize('grade', ['Junior', 'Middle'])
def test_schema_and_reference_independence(grade):
    item = item_for(grade)
    answer = item['private']['reference_answer']
    item['private']['reference_answer'] = '{}'
    assert grade_answer(item, answer)['all_checks_passed']
    for malformed in ['{}', '[]', '{"tests":[],"tests":[]}', 'eval(1)', 'x' * 5001]:
        assert grade_answer(item, malformed)['score'] == 0
    suite = json.loads(answer)
    for value in [True, -1, '4']:
        wrong = copy.deepcopy(suite)
        wrong['tests'][0]['stock'] = value
        assert grade_answer(item, json.dumps(wrong))['score'] == 0
    for bad in [{'checks': qa.CHECKS}, {'tests': suite['tests'] * 3}, {'tests': [{'stock': 1, 'expected': {'status': 201}}]}]:
        assert grade_answer(item, json.dumps(bad))['score'] == 0
    wrong = copy.deepcopy(suite)
    wrong['tests'][0]['expected'].pop('reserved')
    assert grade_answer(item, json.dumps(wrong))['score'] == 0
    if grade == 'Middle':
        wrong = copy.deepcopy(suite)
        wrong['tests'][0]['schedule'] = 'parallel'
        wrong['tests'][0]['fault_at'] = 'after_debit'
        assert grade_answer(item, json.dumps(wrong))['score'] == 0


def test_new_family_is_additional_and_preserves_frozen_boundary_case():
    for grade in ['Junior', 'Middle']:
        items = [generate_case('qa', grade, random.Random(seed)) for seed in range(100)]
        assert {x['family'] for x in items} == {qa.FAMILY, 'qa:boundary_test_suite'}
        assert {x['private']['condition']['variant'] for x in items if x['family'] == qa.FAMILY} == set(qa.VARIANTS[grade])
        old = generate_legacy_case('qa', grade, random.Random(1))
        assert old['version'] == 'work-cases-2026-v1'
        assert grade_answer(old, old['private']['reference_answer'])['all_checks_passed']
    for grade in ['Intern', 'Senior']:
        assert generate_case('qa', grade, random.Random(1))['family'] == 'qa:boundary_test_suite'


@pytest.mark.parametrize('grade', ['Junior', 'Middle'])
def test_signature_freezes_parameters_versions_and_rubric_without_exposing_keys(monkeypatch, grade):
    from backend import work_samples
    base = generate('qa', grade, allow_neural=False)
    item = item_for(grade)
    def issued(value):
        monkeypatch.setattr(work_samples, 'generate_case', lambda *_: copy.deepcopy(value))
        return add_journey(copy.deepcopy(base), 'qa', grade)
    original = issued(item)
    assert original['assessment_path']['rubric'] == qa.rubric(grade)
    assert original['items'][-1]['private']['rubric_criteria'] == qa.rubric(grade)
    public = public_item(original['items'][-2])
    assert 'private' not in public and 'reference_answer' not in json.dumps(public)
    for field in ['max_quantity', 'variant', 'model_version']:
        changed = copy.deepcopy(item)
        changed['private']['condition'][field] = 'different-condition'
        assert issued(changed)['condition_signature'] != original['condition_signature']
    for field in ['version', 'rubric_version']:
        changed = copy.deepcopy(item)
        changed[field] = 'new-version'
        assert issued(changed)['condition_signature'] != original['condition_signature']
    changed = copy.deepcopy(item)
    changed['review_rubric'][0]['anchors'][2] += ' New observation.'
    assert issued(changed)['condition_signature'] != original['condition_signature']
    changed = copy.deepcopy(item)
    changed['id'] = 'different-id'
    assert issued(changed)['condition_signature'] == original['condition_signature']


def test_unsupported_version_is_technical_before_parsing_answer():
    for field in ['version', 'model_version']:
        item = item_for()
        (item if field == 'version' else item['private']['condition'])[field] = 'future-version'
        with pytest.raises(HTTPException) as error:
            grade_answer(item, '{')
        assert error.value.status_code == 503
