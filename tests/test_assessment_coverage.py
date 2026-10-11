from backend.assessment_coverage import coverage
from backend.assessment_catalog import SPECIALIZATIONS

def test_questions_are_not_full_specialization_evidence_or_expert_review():
    items=[{'id':'one','kind':'debug','competency':'state'},{'id':'open','kind':'explanation','competency':'tradeoffs'}]
    report=coverage('frontend','Junior',items)
    assert 'accessibility' in report['not_observed'] and 'debugging' in report['not_observed']
    assert report['expert_reviewed_competencies']==[] and report['phase']=='task_content'
    assert report['validation']=='prototype_team_reference'

def test_frozen_blueprint_survives_catalog_change(monkeypatch):
    frozen={'blueprint_competencies':['sql','debugging'],'level_criteria':['Frozen criterion']}
    monkeypatch.setitem(SPECIALIZATIONS,'backend',{'competencies':['new-competency']})
    report=coverage('backend','Junior',[{'id':'sql','kind':'sql','competency':'sql'}],{'score':100},frozen)
    assert report['blueprint_competencies']==['sql','debugging']
    assert report['not_observed']==['debugging'] and report['level_criteria']==['Frozen criterion']

def test_expert_review_is_distinct_from_objective_measurement():
    items=[{'id':'data','kind':'number','competency':'data_processing'},{'id':'open','kind':'explanation','competency':'tradeoffs'}]
    result={'score':100,'expert_review':{'item_id':'open','score':60}}
    report=coverage('backend','Middle',items,result)
    assert report['objective_observations']==['data_processing']
    assert report['expert_reviewed_competencies']==['tradeoffs']
    assert 'tradeoffs' not in report['not_observed'] and 'sql' in report['not_observed']
