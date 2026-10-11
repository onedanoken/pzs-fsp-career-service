"""Release predicates, transition invariants, recovery and frozen content."""
import copy
import itertools
import json
import random
from unittest.mock import Mock

from fastapi import HTTPException
import pytest

from backend import release_rollout as release
from backend.item_generator import generate, grade_answer, public_item
from backend.work_samples import add_journey, generate_case, generate_legacy_case


def item_for(grade='Junior', variant=None, replicas=2, headroom=1, build='build-17'):
    values = [build] + ([replicas, headroom] if grade == 'Middle' else [])
    return release.generate(grade, Mock(choice=Mock(side_effect=values)), variant or release.VARIANTS[grade][0])


def green(item):
    return {'tests': 'pass', 'scan': 'pass', 'artifact': item['private']['condition']['build']}


@pytest.mark.parametrize('grade,variant', [(g, v) for g, variants in release.VARIANTS.items() for v in variants])
def test_all_authored_parameters_have_solution_and_reject_starter(grade, variant):
    combinations = itertools.product([2, 3, 4], [1, 2], ['build-17', 'build-29']) if grade == 'Middle' else [(2, 1, 'build-17'), (2, 1, 'build-29')]
    for replicas, headroom, build in combinations:
        item = item_for(grade, variant, replicas, headroom, build)
        assert grade_answer(item, item['private']['reference_answer'])['all_checks_passed']
        assert not grade_answer(item, item['starter'])['all_checks_passed']
        assert len(item['private']['reference_answer']) <= 5000


def test_golden_gate_contract_rejects_fail_skipped_and_foreign_artifact():
    item = item_for()
    plan = json.loads(item['private']['reference_answer'])
    for tests, scan, artifact in itertools.product(['pass', 'fail', 'skipped'], ['pass', 'fail', 'skipped'], ['build-17', 'other']):
        event = {'tests': tests, 'scan': scan, 'artifact': artifact}
        expected = (tests, scan, artifact) == ('pass', 'pass', 'build-17')
        assert release.gate_model(plan, event, 'build-17') == release.gate_oracle(event, 'build-17') == expected
    wrong = copy.deepcopy(plan)
    wrong['gates'] = ['tests']
    assert release.gate_model(wrong, {'tests': 'pass', 'scan': 'fail', 'artifact': 'build-17'}, 'build-17')
    wrong = copy.deepcopy(plan)
    wrong['artifact'] = 'any'
    assert release.gate_model(wrong, {'tests': 'pass', 'scan': 'pass', 'artifact': 'other'}, 'build-17')


def test_rollout_has_golden_resource_and_readiness_states():
    item = item_for('Middle')
    plan = json.loads(item['private']['reference_answer'])
    trace = release.execute(plan, item['private']['condition'], green(item))
    assert [(x['old'], x['new'], x['pending'], x['available'], x['total']) for x in trace[:5]] == [
        (2, 0, 0, 2, 2), (2, 0, 0, 2, 2), (2, 0, 1, 2, 3), (2, 1, 0, 3, 3), (1, 1, 0, 2, 2)]
    assert trace[-1] == {'old': 0, 'new': 2, 'pending': 0, 'schema': 'expanded', 'status': 'deployed', 'available': 2, 'total': 2}


def test_terminal_success_does_not_hide_transient_unavailability():
    item = item_for('Middle', 'premature_drain')
    condition = item['private']['condition']
    good = release.execute(json.loads(item['private']['reference_answer']), condition, green(item))
    bad = release.execute(json.loads(item['starter']), condition, green(item))
    assert good[-1] == bad[-1]
    assert min(x['available'] for x in bad) == 1
    assert not release.rollout_oracle(bad, condition, False)['availability']
    # The observer derives availability from counts/schema, not the simulator's
    # reported metric or reference plan.
    for state in bad:
        state['available'] = 1000
    assert not release.rollout_oracle(bad, condition, False)['availability']
    assert grade_answer(item, item['starter'])['score'] == 87.5


def test_each_precompletion_failure_preserves_schema_budget_and_availability():
    for replicas, headroom in itertools.product([2, 3, 4], [1, 2]):
        item = item_for('Middle', replicas=replicas, headroom=headroom)
        plan, condition = json.loads(item['private']['reference_answer']), item['private']['condition']
        for index in range(len(plan['steps']) - 1):
            trace = release.execute(plan, condition, green(item), index)
            assert all(release.rollout_oracle(trace, condition, True).values())
            assert trace[-1]['schema'] == 'expanded' and trace[-1]['status'] == 'rolled_back'
            assert trace[-1]['old'] == replicas and trace[-1]['new'] == trace[-1]['pending'] == 0


def test_destructive_schema_rollback_breaks_restored_old_version():
    item = item_for('Middle', 'destructive_rollback')
    condition = item['private']['condition']
    trace = release.execute(json.loads(item['starter']), condition, green(item), 0)
    assert trace[-1]['old'] == 2 and trace[-1]['schema'] == 'new_only' and trace[-1]['available'] == 0
    assert not release.rollout_oracle(trace, condition, True)['compatibility']
    assert not release.rollout_oracle(trace, condition, True)['terminal']


def test_blocked_pipeline_has_no_migration_or_rollout_side_effects():
    item = item_for('Middle')
    plan, condition = json.loads(item['private']['reference_answer']), item['private']['condition']
    for event in [{'tests': 'pass', 'scan': 'skipped', 'artifact': 'build-17'}, {'tests': 'pass', 'scan': 'pass', 'artifact': 'other'}]:
        trace = release.execute(plan, condition, event)
        assert len(trace) == 2
        assert trace[-1] == {'old': 2, 'new': 0, 'pending': 0, 'schema': 'old', 'status': 'blocked', 'available': 2, 'total': 2}


def test_batched_equivalent_rollout_is_accepted_with_two_spare_slots():
    item = item_for('Middle', headroom=2)
    plan = json.loads(item['private']['reference_answer'])
    plan['max_surge'] = 2
    plan['steps'] = ['expand_schema', 'create_new', 'create_new', 'await_ready', 'remove_old', 'remove_old', 'verify', 'complete']
    assert grade_answer(item, json.dumps(plan))['all_checks_passed']


@pytest.mark.parametrize('grade', ['Junior', 'Middle'])
def test_schema_mutants_and_reference_independence(grade):
    item = item_for(grade)
    plan = json.loads(item['private']['reference_answer'])
    item['private']['reference_answer'] = '{}'
    plan['gates'].reverse()
    assert grade_answer(item, json.dumps(plan))['all_checks_passed']
    mutants = [('gates', ['tests']), ('gates', ['scan']), ('failure', 'continue'), ('artifact', 'any')]
    if grade == 'Middle':
        mutants += [('max_surge', 0), ('max_surge', 2), ('on_failure', 'leave'), ('schema_on_rollback', 'contract'),
                    ('steps', [x for x in plan['steps'] if x != 'verify']),
                    ('steps', [x for x in plan['steps'] if x != 'expand_schema'])]
    for key, value in mutants:
        changed = copy.deepcopy(plan)
        changed[key] = value
        assert not grade_answer(item, json.dumps(changed))['all_checks_passed']
    for malformed in ['{}', '[]', '{"gates":[],"gates":[]}', 'eval(1)', 'x' * 5001]:
        assert grade_answer(item, malformed)['score'] == 0
    for key, value in [('gates', ['tests', 'tests']), ('gates', ['__proto__']), ('checks', ['green'])]:
        changed = copy.deepcopy(plan)
        changed[key] = value
        assert grade_answer(item, json.dumps(changed))['score'] == 0
    if grade == 'Middle':
        for key, value in [('max_surge', True), ('steps', ['complete', 'complete']), ('steps', ['shell'])]:
            changed = copy.deepcopy(plan)
            changed[key] = value
            assert grade_answer(item, json.dumps(changed))['score'] == 0


def test_new_family_is_additional_and_original_graph_case_remains_evaluable():
    for grade in ['Junior', 'Middle']:
        cases = [generate_case('devops', grade, random.Random(seed)) for seed in range(100)]
        assert {x['family'] for x in cases} == {release.FAMILY, 'devops:pipeline_graph_repair'}
        assert {x['private']['condition']['variant'] for x in cases if x['family'] == release.FAMILY} == set(release.VARIANTS[grade])
        old = generate_legacy_case('devops', grade, random.Random(1))
        assert old['version'] == 'work-cases-2026-v1'
        assert grade_answer(old, old['private']['reference_answer'])['all_checks_passed']
    for grade in ['Intern', 'Senior']:
        assert generate_case('devops', grade, random.Random(1))['family'] == 'devops:pipeline_graph_repair'


@pytest.mark.parametrize('grade', ['Junior', 'Middle'])
def test_signature_covers_parameters_and_versions_and_public_items_hide_keys(monkeypatch, grade):
    from backend import work_samples
    base = generate('devops', grade, allow_neural=False)
    item = item_for(grade)
    def issued(value):
        monkeypatch.setattr(work_samples, 'generate_case', lambda *_: copy.deepcopy(value))
        return add_journey(copy.deepcopy(base), 'devops', grade)
    original = issued(item)
    assert original['assessment_path']['rubric'] == release.rubric(grade)
    assert original['items'][-1]['private']['rubric_criteria'] == release.rubric(grade)
    public = public_item(original['items'][-2])
    assert 'private' not in public and 'reference_answer' not in json.dumps(public)
    fields = ['build', 'variant', 'model_version'] + (['replicas', 'slots'] if grade == 'Middle' else [])
    for field in fields:
        changed = copy.deepcopy(item)
        changed['private']['condition'][field] = 'changed-condition'
        assert issued(changed)['condition_signature'] != original['condition_signature']
    for field in ['version', 'rubric_version']:
        changed = copy.deepcopy(item)
        changed[field] = 'new-version'
        assert issued(changed)['condition_signature'] != original['condition_signature']
    changed = copy.deepcopy(item)
    changed['review_rubric'][0]['anchors'][2] += ' New observation.'
    assert issued(changed)['condition_signature'] != original['condition_signature']
    changed = copy.deepcopy(item)
    changed['id'] = 'another-id'
    assert issued(changed)['condition_signature'] == original['condition_signature']


def test_unsupported_version_is_technical_even_with_invalid_json():
    for field in ['version', 'model_version']:
        item = item_for()
        (item if field == 'version' else item['private']['condition'])[field] = 'future-version'
        with pytest.raises(HTTPException) as error:
            grade_answer(item, '{')
        assert error.value.status_code == 503
