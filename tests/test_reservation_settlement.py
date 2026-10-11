"""Synthetic contract traces; no claim of independent Senior calibration."""
import copy
import itertools
import json
import random

import pytest
from fastapi import HTTPException
from backend import reservation_settlement as settlement, work_samples
from backend.item_generator import generate, grade_answer, public_item


def example(variant='late_payment'):
    item = settlement.generate('Senior', random.Random(9), variant)
    return item, json.loads(item['private']['reference_answer']), item['private']['condition']


@pytest.mark.parametrize('variant', settlement.VARIANTS)
def test_all_48_parameter_combinations_accept_reference_and_reject_defect(variant):
    item, plan, condition = example(variant)
    starter = json.loads(item['starter'])
    for quantity, available, deadline in itertools.product(range(1, 4), range(4), range(2, 6)):
        params = dict(condition, quantity=quantity, available=available, deadline=deadline)
        assert all(settlement.evaluate(plan, params).values()), params
        bad = settlement.evaluate(starter, params)
        assert not all(bad.values()), params
        assert not bad['settlement_deadline' if variant == 'late_payment' else 'settlement_dispatch']


def test_deadline_and_terminal_inventory_use_independent_expected_states():
    item, plan, condition = example()
    condition.update(quantity=2, available=0, deadline=3)
    early = settlement.execute(plan, condition, [settlement.event('payment', 2, 'early'), settlement.event('expiry', 3, 'expiry')])
    assert early[-1] == {'available': 0, 'orders': {'A': 'paid', 'B': 'held'},
                         'refund_intents': [], 'acks': ['early', 'expiry'], 'refunds': {}}
    late = settlement.execute(plan, condition, [settlement.event('payment', 3, 'late')])
    assert late[-1] == {'available': 2, 'orders': {'A': 'expired', 'B': 'held'},
                        'refund_intents': [('p-A', False)], 'acks': ['late'], 'refunds': {}}
    bad = json.loads(item['starter'])
    assert settlement.execute(bad, condition, [settlement.event('payment', 3, 'late')])[-1]['orders']['A'] == 'paid'


def test_serialization_rechecks_state_and_does_not_release_paid_inventory():
    item, plan, condition = example()
    d = condition['deadline']
    events = [{'kind': 'batch', 'events': [settlement.event('payment', d-1, 'paid'), settlement.event('expiry', d, 'expires')]}]
    correct = settlement.execute(plan, condition, events)
    assert correct[-1]['orders']['A'] == 'paid' and correct[-1]['available'] == condition['available']
    stale = settlement.execute(dict(plan, read='snapshot'), condition, events)
    assert stale[-1]['orders']['A'] == 'expired' and stale[-1]['available'] == condition['available'] + condition['quantity']


@pytest.mark.parametrize('crash', ['step_1', 'step_2', 'before_commit'])
def test_crash_before_commit_preserves_order_intent_and_broker_delivery(crash):
    item, plan, condition = example()
    state = settlement.initial(condition)
    incoming = settlement.event('payment', condition['deadline'], 'late')
    settlement.receive(state, plan, incoming, crash)
    assert settlement.observe(state) == settlement.observe(settlement.initial(condition))
    settlement.receive(state, plan, incoming)
    settlement.dispatch(state, plan)
    assert state['orders']['A']['state'] == 'expired'
    assert state['available'] == condition['available'] + condition['quantity']
    assert state['refunds'] == {'p-A': 1} and state['acks'] == {'late'}


def test_lost_provider_response_and_new_callback_delivery_cannot_duplicate_refund():
    item, plan, condition = example()
    state = settlement.initial(condition)
    settlement.receive(state, plan, settlement.event('payment', condition['deadline'], 'first'))
    settlement.dispatch(state, plan, 'between')
    assert state['refunds'] == {'p-A': 1} and not state['outbox']['p-A']['done']
    settlement.dispatch(state, plan)
    settlement.receive(state, plan, settlement.event('payment', condition['deadline']+1, 'redelivered'))
    settlement.dispatch(state, plan)
    assert state['refunds'] == {'p-A': 1} and len(state['outbox']) == 1
    bad = settlement.initial(condition)
    settlement.receive(bad, plan, settlement.event('payment', condition['deadline'], 'first'))
    settlement.dispatch(bad, dict(plan, dispatch_ack='before_effect'), 'between')
    settlement.dispatch(bad, dict(plan, dispatch_ack='before_effect'))
    assert bad['refunds'] == {} and bad['outbox']['p-A']['done']


@pytest.mark.parametrize('field,value,failed', [
    ('read', 'snapshot', 'settlement_serialization'),
    ('transaction', 'split', 'settlement_atomicity'),
    ('ack', 'before_commit', 'settlement_ack'),
    ('refund_key', 'delivery', 'settlement_replay'),
    ('refund_key', 'constant', 'settlement_independent_refunds'),
    ('dispatch_ack', 'before_effect', 'settlement_dispatch'),
])
def test_each_delivery_mutation_has_a_behavioral_counterexample(field,value,failed):
    item, plan, condition = example()
    result = grade_answer(item, json.dumps(dict(plan, **{field:value})))
    assert not result['all_checks_passed']
    assert not next(x['passed'] for x in result['checks'] if x['id'] == failed)


def test_equivalent_rules_and_atomic_step_order_are_accepted_without_reference():
    item, plan, condition = example()
    plan['payment'][1]['steps'].reverse()
    plan['payment'].reverse()  # Guards are disjoint; their textual order is irrelevant.
    item['private']['reference_answer'] = '{}'
    assert grade_answer(item, json.dumps(plan))['all_checks_passed']
    for index in range(3):
        incomplete = copy.deepcopy(plan)
        incomplete['payment'].pop(index)
        if incomplete['payment']:
            assert not grade_answer(item, json.dumps(incomplete))['all_checks_passed']


def test_all_three_staged_actions_are_checked_before_the_common_commit():
    item, plan, condition = example()
    # A transient paid marker is never committed or exposed before expiry.
    plan['payment'][1]['steps'] = ['pay','expire','refund']
    assert grade_answer(item, json.dumps(plan))['all_checks_passed']
    assert not grade_answer(item, json.dumps(dict(plan, transaction='split')))['all_checks_passed']


@pytest.mark.parametrize('answer', ['{}', '[]', '{"payment":[],"payment":[]}', 'eval("unsafe")', 'x'*5001])
def test_malformed_input_is_not_an_executed_program(answer):
    item, plan, condition = example()
    assert grade_answer(item, answer)['score'] == 0


def test_extra_fields_unknown_rules_and_versions_fail_closed():
    item, plan, condition = example()
    bad = dict(plan, checks=settlement.CHECKS)
    assert grade_answer(item, json.dumps(bad))['score'] == 0
    for field, value in [('states', ['held', 'held']), ('time', 'at_deadline'), ('steps', ['__import__']), ('steps', ['pay', 'pay'])]:
        bad = copy.deepcopy(plan);bad['payment'][0][field] = value
        assert grade_answer(item, json.dumps(bad))['score'] == 0
    for target in ['version', 'model_version']:
        bad_item = copy.deepcopy(item)
        if target == 'version':bad_item['version'] = 'future'
        else:bad_item['private']['condition']['model_version'] = 'future'
        for answer in [item['private']['reference_answer'], '{']:
            with pytest.raises(HTTPException) as error:grade_answer(bad_item, answer)
            assert error.value.status_code == 503


def test_senior_is_distinct_and_old_work_case_still_grades():
    cases = [work_samples.generate_case('backend','Senior',random.Random(seed)) for seed in range(30)]
    assert {x['family'] for x in cases} == {settlement.FAMILY}
    assert {x['private']['condition']['variant'] for x in cases} == set(settlement.VARIANTS)
    old = work_samples.generate_legacy_case('backend','Senior',random.Random(9))
    frozen = copy.deepcopy(old)
    assert old['family'] == 'backend:durable_idempotency'
    assert grade_answer(old, old['private']['reference_answer'])['all_checks_passed']
    assert old == frozen
    assert 'private' not in public_item(cases[0])
    for grade in ['Intern','Junior','Middle']:
        assert work_samples.generate_case('backend',grade,random.Random(9))['family'] != settlement.FAMILY


def test_signature_freezes_senior_conditions_versions_and_subject_rubric(monkeypatch):
    base = generate('backend','Senior',allow_neural=False)
    case, plan, condition = example()
    def issued(value):
        monkeypatch.setattr(work_samples,'generate_case',lambda *_:copy.deepcopy(value))
        return work_samples.add_journey(copy.deepcopy(base),'backend','Senior')
    first = issued(case)
    assert first['assessment_path']['rubric'] == settlement.rubric()
    assert first['items'][-1]['private']['rubric_criteria'] == settlement.rubric()
    changed_id = copy.deepcopy(case);changed_id['id'] = 'new-id'
    assert issued(changed_id)['condition_signature'] == first['condition_signature']
    for field in ['quantity','available','deadline','variant','model_version']:
        changed = copy.deepcopy(case);changed['private']['condition'][field] = 'changed'
        assert issued(changed)['condition_signature'] != first['condition_signature']
    for field in ['version','rubric_version']:
        changed = copy.deepcopy(case);changed[field] = 'changed'
        assert issued(changed)['condition_signature'] != first['condition_signature']
    changed = copy.deepcopy(case);changed['review_rubric'][0]['anchors'][2] = 'Новый якорь'
    assert issued(changed)['condition_signature'] != first['condition_signature']
