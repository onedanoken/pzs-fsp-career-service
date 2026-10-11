"""Integration with current balanced auto policy and authoritative passport access."""
import json,random
import pytest
from backend import db
from backend.item_generator import generate,grade_answer
from backend.work_samples import add_journey
from backend.certification_evidence import summary,evidence
from test_balanced_lifecycle import issue

@pytest.mark.parametrize('spec',['backend','frontend','qa','devops','ml','security'])
def test_additional_case_is_reachable_at_same_level_with_independent_transfer(spec):
    seen=False
    for seed in range(40):
        base=generate(spec,'Middle',seed=seed,allow_neural=False)
        result=add_journey(base,spec,'Middle','work_sample_v3_balanced')
        case,transfer=result['items'][9:11]
        if case['private'].get('family') not in {'stock_reservation','resource_cache','reservation_test_suite','release_rollout','temporal_validation','object_access'}:continue
        seen=True
        assert result['assembly_plan']['count']==9 and len(result['items'])==12
        assert not result['assessment_path']['review_required']
        assert grade_answer(case,case['private']['reference_answer'])['all_checks_passed']
        assert not grade_answer(case,case['starter']).get('all_checks_passed',False)
        assert grade_answer(transfer,transfer['private']['reference_answer'])['all_checks_passed']
        assert not grade_answer(transfer,case['private']['reference_answer']).get('all_checks_passed',False)
        assert result['items'][-1]['private']['rubric_criteria']==case['review_rubric']
        break
    assert seen,'Additional family must be reachable'

@pytest.mark.parametrize('path',['work_sample_v1','work_sample_v2_auto','work_sample_v3_balanced'])
def test_stage_semantics_covers_historical_and_automatic_paths(path):
    cert={'session_id':'one','status':'confirmed','payload':{'assessment_path':{'version':path}}}
    assert summary(cert)['score_semantics']=='screening_score'


def test_automatic_passport_details_are_revocable_and_not_stored_in_unowned_snapshots(clients,monkeypatch):
    monkeypatch.setenv("FSP_ASSESSMENT_PATH","work_sample_v3_balanced")
    employer,candidate,_,_=clients
    state=issue(candidate,'qa')
    passport=state['skill_passport'];pid=passport['id']
    with db.connection() as conn:
        cert=conn.execute('SELECT * FROM grade_certifications WHERE session_id=? AND active=1',(state['id'],)).fetchone()
        employer_user=dict(conn.execute("SELECT * FROM users WHERE id='u_employer'").fetchone())
        assert evidence(conn,cert,employer_user)['stages'] is None
        assert evidence(conn,cert,None)['stages'] is None
    assert candidate.put('/api/skill-passports/'+pid+'/visibility',json={'published':True}).status_code==200
    with db.connection() as conn:
        cert=conn.execute('SELECT * FROM grade_certifications WHERE session_id=? AND active=1',(state['id'],)).fetchone()
        detail=evidence(conn,cert,employer_user)
        assert detail['stages']['transfer']['all_checks_passed']
        assert detail['passport']['category_confirmed_by_this_revision']
        assert 'answers' not in json.dumps(detail) and 'reference_answer' not in json.dumps(detail)
    assert candidate.put('/api/skill-passports/'+pid+'/visibility',json={'published':False}).status_code==200
    with db.connection() as conn:
        cert=conn.execute('SELECT * FROM grade_certifications WHERE session_id=? AND active=1',(state['id'],)).fetchone()
        assert evidence(conn,cert,employer_user)['stages'] is None
