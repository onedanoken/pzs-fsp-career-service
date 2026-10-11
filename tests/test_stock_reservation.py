"""Finite-domain checks, independent counterexamples and frozen item contracts."""
import copy
import itertools
import json
import random
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from backend import stock_reservation as reservation
from backend.item_generator import generate, grade_answer, public_item
from backend.work_samples import add_journey, generate_case, generate_legacy_case


@pytest.mark.parametrize('grade,variant', [
    ('Junior', 'quantity_boundary'), ('Junior', 'write_before_check'),
    ('Middle', 'lost_update'), ('Middle', 'partial_commit'),
])
def test_all_authored_parameters_have_a_solution_and_detect_the_defect(grade, variant):
    for limit, stock in itertools.product(range(2, 9), range(3, 13)):
        item = reservation.generate(grade, Mock(randint=Mock(side_effect=[limit, stock])), variant)
        assert item['private']['condition']['limit'] == limit
        assert item['private']['condition']['stock'] == stock
        assert grade_answer(item, item['private']['reference_answer'])['all_checks_passed']
        assert not grade_answer(item, item['starter'])['all_checks_passed']


@pytest.mark.parametrize('grade,variant,stock,quantities,options,expected', [
    ('Junior', 'quantity_boundary', 3, [0], {}, {'stock': 3, 'reservations': [0], 'statuses': [201]}),
    ('Junior', 'write_before_check', 1, [2], {}, {'stock': -1, 'reservations': [], 'statuses': [409]}),
    ('Middle', 'lost_update', 3, [2, 2], {'concurrent': True}, {'stock': 1, 'reservations': [2, 2], 'statuses': [201, 201]}),
    ('Middle', 'partial_commit', 3, [1], {'crash_after': 'decrement'}, {'stock': 2, 'reservations': [], 'statuses': ['interrupted']}),
])
def test_concrete_counterexamples_are_state_changes_not_named_test_flags(grade, variant, stock, quantities, options, expected):
    item = reservation.generate(grade, random.Random(9), variant)
    assert reservation.execute(json.loads(item['starter']), stock, quantities, **options) == expected
    assert grade_answer(item, item['starter'])['score'] < 100


@pytest.mark.parametrize('grade', ['Junior', 'Middle'])
def test_checker_does_not_trust_reference_answer_or_allow_extra_fields(grade):
    item = reservation.generate(grade, random.Random(9))
    answer = item['private']['reference_answer']
    plan = json.loads(answer)
    # Golden outcomes are written independently of both generator and oracle.
    assert reservation.execute(plan, 3, [2, 2]) == {'stock': 1, 'reservations': [2], 'statuses': [201, 409]}
    assert reservation.execute(plan, 3, [True, 1.5, 1.0, 0, '1', None]) == {'stock': 3, 'reservations': [], 'statuses': [422] * 6}
    if grade == 'Middle':
        assert reservation.execute(plan, 3, [2, 2], concurrent=True) == {'stock': 1, 'reservations': [2], 'statuses': [201, 409]}
        for step in reservation.STEPS:
            assert reservation.execute(plan, 3, [1], crash_after=step) == {'stock': 3, 'reservations': [], 'statuses': ['interrupted']}
    item['private']['reference_answer'] = '{}'
    assert grade_answer(item, answer)['all_checks_passed']
    bad = json.loads(answer)
    bad['checks'] = reservation.CHECKS + reservation.MIDDLE_CHECKS
    assert grade_answer(item, json.dumps(bad))['score'] == 0
    for malformed in ['{}', '[]', '{"steps":[],"steps":[]}', 'eval("anything")', 'x' * 5001]:
        assert grade_answer(item, malformed)['score'] == 0
    for field, value in [('steps', ['validate', 'validate']), ('steps', ['__import__']), ('quantity', {'type': 'integer', 'min': True, 'max': 5})]:
        bad = json.loads(answer)
        bad[field] = value
        assert grade_answer(item, json.dumps(bad))['score'] == 0


@pytest.mark.parametrize('grade', ['Junior', 'Middle'])
def test_incomplete_and_wrong_plans_cannot_pass(grade):
    item = reservation.generate(grade, random.Random(9))
    valid = json.loads(item['private']['reference_answer'])
    mutants = []
    for step in reservation.STEPS:
        mutant = copy.deepcopy(valid)
        mutant['steps'].remove(step)
        mutants.append(mutant)
    for field, value in [('type', 'number'), ('min', 0), ('max', valid['quantity']['max'] + 1)]:
        mutant = copy.deepcopy(valid)
        mutant['quantity'][field] = value
        mutants.append(mutant)
    for mutant in mutants:
        assert not grade_answer(item, json.dumps(mutant))['all_checks_passed']
    # Atomic staging permits recording before decrement: the checker accepts
    # equivalent repairs instead of requiring a literal copy of the authored key.
    valid['steps'] = ['validate', 'check_stock', 'record', 'decrement']
    assert grade_answer(item, json.dumps(valid))['all_checks_passed']


def test_new_family_is_additional_and_is_limited_to_junior_middle():
    for grade in ['Junior', 'Middle']:
        cases = [generate_case('backend', grade, random.Random(seed)) for seed in range(100)]
        assert {i['family'] for i in cases} == {reservation.FAMILY, 'backend:durable_idempotency'}
        assert {i['private']['condition']['variant'] for i in cases if i['family'] == reservation.FAMILY} == set(reservation.VARIANTS[grade])
    assert generate_case('backend', 'Intern', random.Random(9))['family'] == 'backend:durable_idempotency'
    assert generate_case('backend', 'Senior', random.Random(9))['family'] != reservation.FAMILY


@pytest.mark.parametrize('grade', ['Junior', 'Middle'])
def test_issued_signature_covers_conditions_versions_and_subject_rubric(monkeypatch, grade):
    import backend.work_samples as samples
    base = generate('backend', grade, allow_neural=False)
    case = reservation.generate(grade, random.Random(9))

    def issued(value):
        monkeypatch.setattr(samples, 'generate_case', lambda *_: copy.deepcopy(value))
        return add_journey(copy.deepcopy(base), 'backend', grade)

    original = issued(case)
    assert original['assessment_path']['rubric'] == original['items'][-1]['private']['rubric_criteria'] == reservation.rubric(grade)
    assert 'private' not in public_item(original['items'][-2])
    assert 'reference_answer' not in json.dumps(public_item(original['items'][-2]))
    for key in ['limit', 'stock', 'variant']:
        changed = copy.deepcopy(case)
        changed['private']['condition'][key] = 'changed-condition'
        assert issued(changed)['condition_signature'] != original['condition_signature']
    for key in ['version', 'rubric_version']:
        changed = copy.deepcopy(case)
        changed[key] = 'next-version'
        assert issued(changed)['condition_signature'] != original['condition_signature']
    changed = copy.deepcopy(case)
    changed['review_rubric'][0]['anchors'][2] = 'New subject observation'
    assert issued(changed)['condition_signature'] != original['condition_signature']
    changed = copy.deepcopy(case)
    changed['id'] = 'a-random-id'
    assert issued(changed)['condition_signature'] == original['condition_signature']


def test_frozen_legacy_case_keeps_its_previous_key_and_rubric(monkeypatch):
    import backend.work_samples as samples
    case = generate_legacy_case('backend', 'Middle', random.Random(9))
    base = generate('backend', 'Middle', allow_neural=False)
    base['blueprint_version'] = 'competencies-2026-10-v5'
    monkeypatch.setattr(samples, 'generate_case', lambda *_: copy.deepcopy(case))
    frozen = json.loads(json.dumps(add_journey(base, 'backend', 'Middle')))
    monkeypatch.setattr(samples, 'generate_case', lambda *_: reservation.generate('Middle', random.Random(9)))
    monkeypatch.setattr(samples, 'RUBRIC', [])
    assert grade_answer(frozen['items'][-2], case['private']['reference_answer'])['all_checks_passed']
    assert frozen['items'][-2]['version'] == frozen['items'][-1]['version'] == 'work-cases-2026-v1'
    assert len(frozen['assessment_path']['rubric']) == 4
    assert frozen['blueprint_version'] == 'competencies-2026-10-v5'


@pytest.mark.parametrize('grade', ['Junior', 'Middle'])
def test_stock_v1_keeps_its_content_and_check_after_v2_is_issued(grade):
    old = reservation.generate_v1(grade, random.Random(9))
    frozen = json.loads(json.dumps(old))
    new = reservation.generate(grade, random.Random(9))
    assert old == frozen
    assert old['version'] == reservation.V1_VERSION and new['version'] == reservation.VERSION
    assert old['rubric_version'] == reservation.V1_RUBRIC_VERSION
    assert 'других неотрицательных остатках' not in old['prompt']
    assert 'других неотрицательных остатках' in new['prompt']
    assert old['review_rubric'] != new['review_rubric']
    assert old['private']['condition'] == new['private']['condition']
    assert grade_answer(old, old['private']['reference_answer']) == grade_answer(new, new['private']['reference_answer'])
    assert grade_answer(old, old['starter']) == grade_answer(new, new['starter'])
    for item in [old, new]:
        item['version'] = 'stock-reservation-unknown'
        for answer in [item['private']['reference_answer'], '{']:
            with pytest.raises(HTTPException) as error:
                grade_answer(item, answer)
            assert error.value.status_code == 503


def test_atomic_staging_is_not_confused_with_durable_write_order():
    item = reservation.generate('Middle', random.Random(9))
    plan = json.loads(item['private']['reference_answer'])
    plan['steps'] = ['record', 'validate', 'check_stock', 'decrement']
    assert grade_answer(item, json.dumps(plan))['all_checks_passed']
    assert 'Черновая запись' in item['review_rubric'][0]['anchors'][2]
