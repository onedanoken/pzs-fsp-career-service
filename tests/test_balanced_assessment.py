import json,random
import pytest
from backend.assessment_catalog import GRADES
from backend.assessment_forms import CORE,screening,signature,PATH_VERSION
from backend.item_generator import generate,grade_answer,public_item
from backend.work_samples import add_journey
from backend.work_cases_v2 import FAMILIES,generate_case,evaluate
from backend.work_transfers import generate as transfer
from backend.work_projects_v3 import generate_case as project,evaluate as project_evaluate
from backend.work_reference_v3 import api_reference,react_reference
from backend.assessment_journey import result_for

def test_empty_legacy_solution_stays_valid_but_new_repair_starter_is_meaningful():
    from backend.work_samples import broken_starter
    original=generate_case('security','Middle',random.Random(4),'network_rules')
    frozen=json.dumps(original,sort_keys=True)
    assert evaluate(original,original['starter'])['all_checks_passed']
    repaired=broken_starter(original)
    assert not evaluate(repaired,repaired['starter'])['all_checks_passed']
    assert evaluate(repaired,repaired['private']['reference_answer'])['all_checks_passed']
    assert json.dumps(original,sort_keys=True)==frozen

@pytest.mark.parametrize('spec',CORE)
@pytest.mark.parametrize('grade',GRADES)
def test_balanced_forms_freeze_quotas_and_no_keys_are_public(spec,grade):
    for seed in range(6):
        items,plan=screening(spec,grade,seed)
        assert len(items)==9 and len({signature(i) for i in items})==9
        assert {c:sum(i['competency']==c for i in items) for c in CORE[spec]}==plan['quota']=={c:3 for c in CORE[spec]}
        assert all(i['difficulty_level']==GRADES.index(grade)+1 for i in items)
        assert all('private' not in public_item(i) for i in items)

@pytest.mark.parametrize('spec',FAMILIES)
@pytest.mark.parametrize('grade',GRADES)
def test_old_work_models_have_explicit_transfer_and_frozen_reference(spec,grade):
    for family in FAMILIES[spec]:
        for seed in range(6):
            original=generate_case(spec,grade,random.Random(seed),family);frozen=json.dumps(original,sort_keys=True)
            changed=transfer(original)
            assert evaluate(changed,changed['private']['reference_answer'])['all_checks_passed']
            assert not evaluate(changed,original['private']['reference_answer'])['all_checks_passed']
            assert json.dumps(original,sort_keys=True)==frozen and evaluate(original,original['private']['reference_answer'])['all_checks_passed']

@pytest.mark.parametrize('spec',['backend','frontend','qa'])
@pytest.mark.parametrize('grade',GRADES)
def test_project_replays_accept_alternatives_and_reject_negative_solutions(spec,grade):
    for seed in range(12):
        item=project(spec,grade,random.Random(seed));changed=transfer(item)
        for current in [item,changed]:
            for name in ['reference_answer','alternative_answer']:assert project_evaluate(current,current['private'][name])['all_checks_passed']
            for value in [current['starter'],'{}','[]','{"x":1,"x":2}','exec("unsafe")','x'*5001]:assert not project_evaluate(current,value)['all_checks_passed']
        assert not project_evaluate(changed,item['private']['reference_answer'])['all_checks_passed']

def test_independent_project_oracles_match_manually_derived_boundary_examples():
    case=project('backend','Senior',random.Random(4))['private']['condition']
    assert api_reference(case)==[201,200,409,201,403,403,403]
    assert api_reference(case,'transactional_outbox')==[202,200,409,202,403,403,403]
    c={'events':[{'channel':'A','type':'issue','id':2},{'channel':'A','type':'response','id':1,'status':200,'items':[1]},{'channel':'B','type':'issue','id':1},{'channel':'B','type':'response','id':1,'status':503,'items':[1]}]}
    assert react_reference(c)=={'A':{'request':2,'status':'loading','items':[]},'B':{'request':1,'status':'error','items':[]}}

def test_core_gap_and_unrepaired_transfer_cannot_be_compensated_by_average():
    data=add_journey(generate('qa','Middle',allow_neural=False,qa_professional=True,seed=12),'qa','Middle',PATH_VERSION)
    results=[{'item_id':i['id'],'competency':i['competency'],'score':100 if i['kind']!='explanation' else None,'all_checks_passed':i['kind']=='work_sample','checks':[]} for i in data['items']]
    assert result_for(data,results)['passed']
    weak=[dict(r) for r in results]
    for r in weak:
        if r['competency']=='debugging':r['score']=40
    answer=result_for(data,weak)
    assert answer['score']>=70 and not answer['passed'] and not answer['assessment_path']['core_passed']
    weak=[dict(r) for r in results]
    next(r for r in weak if r['item_id']==next(i['id'] for i in data['items'] if i['stage']=='transfer'))['all_checks_passed']=False
    assert not result_for(data,weak)['passed']
