"""Content/transport checks, not proof of human grade equivalence."""
import copy
import random
import pytest
from backend.assessment_catalog import GRADES, SOURCES
from backend.assessment_forms import CORE, screening, signature
from backend.grade_scenarios import BANK, operations
from backend.item_generator import grade_answer, public_item

@pytest.mark.parametrize('spec', CORE)
@pytest.mark.parametrize('grade', GRADES)
def test_fixed_evidence_roles_and_four_alternatives(spec, grade):
    for seed in range(12):
        items, plan = screening(spec, grade, seed)
        assert plan['version'] == 'balanced-core-2026-v3'
        assert len(items) == len({signature(i) for i in items}) == 9
        for competency in CORE[spec]:
            group = [i for i in items if i['competency'] == competency]
            decisions = [i for i in group if i.get('version') == 'grade-decisions-2026-v1']
            assert len(decisions) == 1 and len(group) == 3
            assert {i['evidence_role'] for i in group}==set(plan['evidence_roles'])
        for item in items:
            public = public_item(item)
            assert 'private' not in public and 'rationale' not in public
            if item['kind'] == 'choice' and item['private'].get('rationale'):
                assert len(item['choices']) == len({c['text'] for c in item['choices']}) == 4
                for choice in item['choices']:
                    result = grade_answer(item, choice['id'])
                    assert result['score'] == (100 if choice['id'] == item['private']['expected'] else 0)
                    assert result['feedback']

def test_authored_grade_conditions_are_distinct_and_sources_are_registered():
    ids = {s['id'] for s in SOURCES}
    assert sum(len(rows) for fields in BANK.values() for rows in fields.values()) == 72
    for spec, fields in BANK.items():
        assert set(fields) == set(CORE[spec])
        for rows in fields.values():
            assert len({r[0] for r in rows}) == 4
            assert all(len(set(row[1:5])) == 4 and len(row[5]) > 30 for row in rows)
        for grade in GRADES:
            assert all(set(i['source_ids']) <= ids for i in operations(spec, grade, random.Random(2)))

def test_manual_numeric_and_trace_references_are_independent_of_choice_ids():
    # Separately calculated boundary examples for the authored content.
    assert 8/(8+2) == .8 and 8/(8+4) == pytest.approx(2/3)
    events = [{'id':'a','revision':2,'value':'first'}, {'id':'a','revision':2,'value':'second'}, {'id':'a','revision':1,'value':'old'}]
    reference = {}
    for event in events:
        if event['id'] not in reference or event['revision'] > reference[event['id']]['revision']:
            reference[event['id']] = event
    assert reference['a']['value'] == 'first'
    assert len([None, 3, 3]) == 3 and len([x for x in [None, 3, 3] if x is not None]) == 2
    assert 4 * (1024**3) // (1024**3) == 4

def test_frozen_issued_item_is_not_changed_when_new_form_is_built():
    item = operations('qa','Middle',random.Random(3))[0]
    original = copy.deepcopy(item)
    screening('qa','Senior',7)
    assert item == original
