import ast,json,random
from datetime import datetime,timezone,timedelta
from pathlib import Path
import pytest
from backend.recruiting import match_need
from backend.evidence_ranking import select_certificate
from scripts.measure_ranking_v3 import corpus,DEVELOPMENT,EVALUATION

def test_scenario_families_are_distinct_and_contain_registered_control_features():
    data=corpus();assert not set(DEVELOPMENT)&set(EVALUATION)
    shapes={}
    for case in data['cases']:
        shapes.setdefault(case['family'],set()).add(tuple(sorted(p['id'] for p in case['profiles'])))
    assert len({next(iter(shape)) for shape in shapes.values()})==len(shapes)
    assert any(p['id']=='relocation_allowed' for c in data['cases'] for p in c['profiles'])
    assert any(len(p['certificates'])==2 for c in data['cases'] for p in c['profiles'])

@pytest.mark.parametrize('split',['development','evaluation'])
def test_no_scoring_component_can_compensate_explicit_condition_conflicts(split):
    data=corpus()
    for case in data['cases']:
        if case['split']!=split:continue
        need={'payload':json.dumps(case['need'])}
        for p in case['profiles']:
            result=match_need(None,p['candidate'],need,case['filters'],p['certificates'],policy='category-evidence-v2',reference_time=data['anchor'])
            if p['reference_relevance']==0:assert result is None
            if result:
                assert result['algorithm']=='category-evidence-v2'
                assert result['score']==round(max(0,min(100,sum(result['score_components'].values()))),2)
                assert 0<=result['fsp_bonus']<=2 and 'contacts' not in result

def test_exact_certificate_is_preferred_to_adjacent_and_future_or_unknown_dates_are_disclosed():
    data=corpus();case=next(c for c in data['cases'] if c['family']=='unknown_metadata');need={'payload':json.dumps(case['need'])}
    p=next(p for p in case['profiles'] if p['id']=='double_cert')
    result=match_need(None,p['candidate'],need,case['filters'],p['certificates'],policy='category-evidence-v2',reference_time=data['anchor'])
    assert result['certification']['grade']==case['need']['grade']
    p=next(p for p in case['profiles'] if p['id']=='missing_date_full')
    for date in ['unknown',(datetime.fromtimestamp(data['anchor'],timezone.utc)+timedelta(days=30)).isoformat()]:
        p['certificates'][0]['created_at']=date
        result=match_need(None,p['candidate'],need,case['filters'],p['certificates'],policy='category-evidence-v2',reference_time=data['anchor'])
        assert 'Дата проверки' in result['unknown'] and result['evidence_summary']['age_days'] is None

def test_omitted_self_description_does_not_hide_an_observed_skill_from_explicit_filter():
    data=corpus();case=data['cases'][0];p=next(p for p in case['profiles'] if p['id']=='full');need={'payload':json.dumps(case['need'])}
    p['candidate']['all_skills']=[]
    filters=dict(case['filters'],skills=case['need']['skills'])
    assert match_need(None,p['candidate'],need,filters,p['certificates'],policy='category-evidence-v1') is None
    result=match_need(None,p['candidate'],need,filters,p['certificates'],policy='category-evidence-v2',reference_time=data['anchor'])
    assert result and all(row['status']=='tested_in_session' for row in result['matrix'])
    assert result['score_components']['declared_stack']==20
