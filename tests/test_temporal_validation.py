"""Frozen preparation, availability of labels and truthful validation costs."""
import copy
import itertools
import json
import random
from unittest.mock import Mock

from fastapi import HTTPException
import pytest

from backend import temporal_validation as ml
from backend.item_generator import generate, grade_answer, public_item
from backend.work_samples import add_journey, generate_case, generate_legacy_case


def item_for(grade='Junior', variant=None, scale=2, cutoff=40, costs=(1, 9)):
    values = [scale] + ([cutoff, costs] if grade == 'Middle' else [])
    return ml.generate(grade, Mock(choice=Mock(side_effect=values)), variant or ml.VARIANTS[grade][0])


@pytest.mark.parametrize('grade,variant', [(g, v) for g, variants in ml.VARIANTS.items() for v in variants])
def test_all_authored_parameters_have_solution_and_detect_defect(grade, variant):
    choices = itertools.product([2, 4, 6], [40, 70], [(1, 9), (9, 1), (1, 1)]) if grade == 'Middle' else [(x, 40, (1, 9)) for x in [2, 4, 6]]
    for scale, cutoff, costs in choices:
        item = item_for(grade, variant, scale, cutoff, costs)
        assert len(item['private']['reference_answer']) <= 5000
        assert grade_answer(item, item['private']['reference_answer'])['all_checks_passed']
        assert not grade_answer(item, item['starter'])['all_checks_passed']


def test_junior_imputation_has_golden_mean_and_holdout_counterexample():
    item = item_for(variant='fit_holdout')
    condition = item['private']['condition']
    good = json.loads(item['private']['reference_answer'])
    prepared = ml.execute_preparation(good, condition['rows'])
    assert prepared['mean'] == 8
    assert prepared['filled']['r3'] == prepared['filled']['r6'] == 8
    assert ml.preparation_oracle(condition, ['r1', 'r2', 'r3', 'r4', 'r5', 'r6']) == prepared
    wrong = json.loads(item['starter'])
    assert ml.execute_preparation(wrong, condition['rows'])['mean'] != 8
    wrong['fill_value'] = 8
    assert not grade_answer(item, json.dumps(wrong))['all_checks_passed']


@pytest.mark.parametrize('grade', ['Junior', 'Middle'])
def test_test_changes_do_not_change_training_statistic(grade):
    item = item_for(grade)
    condition = copy.deepcopy(item['private']['condition'])
    plan = json.loads(item['private']['reference_answer'])
    for row in condition['rows']:
        if row['id'] in plan['test']:
            row['amount'] *= 3
    original = ml.execute_preparation(plan, item['private']['condition']['rows'])
    changed = ml.execute_preparation(plan, condition['rows'])
    assert changed['mean'] == original['mean']
    assert changed['filled']['r3'] == original['filled']['r3']
    assert all(ml.evaluate(plan, condition).values())


def test_middle_split_excludes_late_label_before_training_snapshot():
    item = item_for('Middle', 'late_label')
    condition = item['private']['condition']
    assert ml.split_oracle(condition) == {'train': ['r1', 'r2', 'r3'], 'validation': ['r5', 'r6'], 'test': ['r7', 'r8'], 'purge': ['r4']}
    prepared = ml.execute_preparation(json.loads(item['private']['reference_answer']), condition['rows'])
    assert prepared['mean'] == 4 and prepared['filled']['r3'] == 4
    bad = json.loads(item['starter'])
    assert not ml.evaluate(bad, condition)['ml_label_cutoff']
    assert not ml.evaluate(bad, condition)['ml_partitions']


def test_cutoff_boundaries_distinguish_forecast_time_and_label_time():
    condition = copy.deepcopy(item_for('Middle')['private']['condition'])
    condition['rows'] = [
        {'id': 'before', 'prediction_at': 39, 'label_at': 40},
        {'id': 'late', 'prediction_at': 39, 'label_at': 41},
        {'id': 'at', 'prediction_at': 40, 'label_at': 60},
        {'id': 'late_validation', 'prediction_at': 40, 'label_at': 61},
        {'id': 'test', 'prediction_at': 60, 'label_at': 62}]
    assert ml.split_oracle(condition) == {'train': ['before'], 'validation': ['at'], 'test': ['test'], 'purge': ['late', 'late_validation']}


@pytest.mark.parametrize('costs,expected_threshold,expected_cost', [((1, 9), 0.3, 1), ((9, 1), 0.8, 1), ((1, 1), 0.8, 1)])
def test_threshold_has_independent_weighted_cost_and_tie_rule(costs, expected_threshold, expected_cost):
    condition = item_for('Middle', costs=costs)['private']['condition']
    assert ml.threshold_oracle(condition, ['r5', 'r6']) == {'threshold': expected_threshold, 'cost': expected_cost}
    scores = [ml.classification_cost(condition['rows'], ['r5', 'r6'], t, *costs) for t in [0.3, 0.5, 0.8]]
    assert [(x['fp'], x['fn']) for x in scores] == [(1, 0), (1, 1), (0, 1)]
    assert min(x['cost'] for x in scores) == expected_cost


def test_zero_test_error_does_not_validate_tuning_on_test():
    item = item_for('Middle', 'test_threshold')
    condition = item['private']['condition']
    wrong = json.loads(item['starter'])
    assert wrong['threshold'] == 0.8 and wrong['expected_cost'] == 0
    assert ml.classification_cost(condition['rows'], wrong['threshold_rows'], 0.8, 1, 9) == {'fp': 0, 'fn': 0, 'cost': 0}
    assert ml.classification_cost(condition['rows'], ['r5', 'r6'], 0.8, 1, 9)['cost'] == 9
    checks = ml.evaluate(wrong, condition)
    assert not checks['ml_threshold_scope'] and not checks['ml_validation_cost']
    assert grade_answer(item, item['starter'])['score'] == 71.43


def test_test_probabilities_and_targets_do_not_choose_validation_threshold():
    condition = copy.deepcopy(item_for('Middle')['private']['condition'])
    before = ml.threshold_oracle(condition, ['r5', 'r6'])
    for row in condition['rows']:
        if row['id'] in ('r7', 'r8'):
            row.update(target=1 - row['target'], probability=1 - row['probability'])
    assert ml.threshold_oracle(condition, ['r5', 'r6']) == before


@pytest.mark.parametrize('grade', ['Junior', 'Middle'])
def test_equivalent_fit_and_ordering_and_reference_independence(grade):
    item = item_for(grade)
    plan = json.loads(item['private']['reference_answer'])
    item['private']['reference_answer'] = '{}'
    rows = item['private']['condition']['rows']
    plan['fit_rows'] = [x['id'] for x in rows if x['id'] in plan['train'] and x['amount'] is not None]
    for field, values in plan.items():
        if isinstance(values, list):
            values.reverse()
    plan['features'] = ['amount']
    assert grade_answer(item, json.dumps(plan))['all_checks_passed']


@pytest.mark.parametrize('grade', ['Junior', 'Middle'])
def test_malformed_and_plausible_wrong_plans_do_not_pass(grade):
    item = item_for(grade)
    plan = json.loads(item['private']['reference_answer'])
    for key, value in [('fit_rows', plan['fit_rows'][1:]), ('fit_rows', plan['train'] + plan['test']), ('features', ['amount', 'resolved_amount']), ('features', ['channel']), ('fill_value', plan['fill_value'] + 1)]:
        wrong = copy.deepcopy(plan); wrong[key] = value
        assert not grade_answer(item, json.dumps(wrong))['all_checks_passed']
    if grade == 'Middle':
        for key, value in [('threshold_rows', plan['test']), ('threshold', 0.5), ('expected_cost', 0), ('purge', [])]:
            wrong = copy.deepcopy(plan); wrong[key] = value
            assert not grade_answer(item, json.dumps(wrong))['all_checks_passed']
    for malformed in ['{}', '[]', '{"train":[],"train":[]}', 'eval(1)', 'x' * 5001]:
        assert grade_answer(item, malformed)['score'] == 0
    for key, value in [('fit_rows', ['r1', 'r1']), ('features', ['__proto__']), ('fill_value', True), ('fill_value', float('nan')), ('checks', ml.CHECKS)]:
        wrong = copy.deepcopy(plan); wrong[key] = value
        assert grade_answer(item, json.dumps(wrong))['score'] == 0


def test_new_family_is_additional_and_old_group_split_keeps_its_key():
    for grade in ['Junior', 'Middle']:
        cases = [generate_case('ml', grade, random.Random(seed)) for seed in range(100)]
        assert {x['family'] for x in cases} == {ml.FAMILY, 'ml:split_leakage_repair'}
        assert {x['private']['condition']['variant'] for x in cases if x['family'] == ml.FAMILY} == set(ml.VARIANTS[grade])
        old = generate_legacy_case('ml', grade, random.Random(1))
        assert old['version'] == 'work-cases-2026-v1'
        assert grade_answer(old, old['private']['reference_answer'])['all_checks_passed']
    for grade in ['Intern', 'Senior']:
        assert generate_case('ml', grade, random.Random(1))['family'] == 'ml:split_leakage_repair'


@pytest.mark.parametrize('grade', ['Junior', 'Middle'])
def test_signature_and_public_dataset_preserve_actual_parameters(monkeypatch, grade):
    from backend import work_samples
    base = generate('ml', grade, allow_neural=False)
    item = item_for(grade)
    def issued(value):
        monkeypatch.setattr(work_samples, 'generate_case', lambda *_: copy.deepcopy(value))
        return add_journey(copy.deepcopy(base), 'ml', grade)
    original = issued(item)
    assert original['assessment_path']['rubric'] == ml.rubric(grade)
    assert original['items'][-1]['private']['rubric_criteria'] == ml.rubric(grade)
    public = public_item(original['items'][-2])
    assert len(public['example_table']['rows']) == 8
    assert 'private' not in public and 'reference_answer' not in json.dumps(public)
    for field in ['rows', 'feature_timing', 'variant', 'model_version'] + (['cutoff', 'validation_end', 'fp_cost', 'fn_cost', 'thresholds'] if grade == 'Middle' else ['holdout']):
        changed = copy.deepcopy(item); changed['private']['condition'][field] = 'changed-condition'
        assert issued(changed)['condition_signature'] != original['condition_signature']
    for field in ['version', 'rubric_version']:
        changed = copy.deepcopy(item); changed[field] = 'next-version'
        assert issued(changed)['condition_signature'] != original['condition_signature']
    changed = copy.deepcopy(item); changed['review_rubric'][0]['anchors'][2] += ' New observation.'
    assert issued(changed)['condition_signature'] != original['condition_signature']
    changed = copy.deepcopy(item); changed['id'] = 'another-id'
    assert issued(changed)['condition_signature'] == original['condition_signature']


def test_unknown_model_version_is_technical_before_answer_format():
    for field in ['version', 'model_version']:
        item = item_for()
        (item if field == 'version' else item['private']['condition'])[field] = 'future-version'
        with pytest.raises(HTTPException) as error:
            grade_answer(item, '{')
        assert error.value.status_code == 503
