import copy
import itertools
import pytest
from backend.assessment_forms import screening,PATH_VERSION
from backend.item_generator import generate
from backend.work_samples import add_journey
from backend.assessment_journey import result_for
from scripts.measure_assessment_policy import decision,exact_pass,measure

@pytest.mark.parametrize('count',[9,12])
def test_exact_policy_matches_real_result_for_for_every_binary_screening_pattern(count):
    data=add_journey(generate('qa','Middle',allow_neural=False,qa_professional=True,seed=4),'qa','Middle',PATH_VERSION)
    items,plan=screening('qa','Middle',4,count)
    data['items']=items+[i for i in data['items'] if i['stage']!='screening']
    data['assembly_plan']=plan
    for bits in itertools.product([0,1],repeat=count):
        results=[{'item_id':item['id'],'score':100*value} for item,value in zip(items,bits)]
        results.extend({'item_id':i['id'],'score':100,'all_checks_passed':True} for i in data['items'] if i['kind']=='work_sample')
        counts=[sum(v for item,v in zip(items,bits) if item['competency']==c) for c in plan['quota']]
        assert result_for(data,results)['passed']==decision(counts,count//3)

def test_length_change_is_rejected_when_weak_core_is_admitted_more_often():
    report=measure()
    assert report['longer_policy_accepted'] is False and report['production_length']==9
    rows={(r['profile'],r['length']):r for r in report['rows']}
    assert rows['one_weak_core',12]['pass_rate']>rows['one_weak_core',9]['pass_rate']
    assert rows['strong',12]['pass_rate']>rows['strong',9]['pass_rate']
    assert exact_pass([1,1,1],3)==1 and exact_pass([0,0,0],3)==0
