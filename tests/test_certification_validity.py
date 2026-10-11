import copy
import json
from datetime import datetime, timedelta, timezone
import pytest
from backend.certification_validity import usable_certificate
from backend.evidence_ranking import select_certificate
from backend import db
from test_career_contact import NEED

BASE = {'id':'cert','candidate_id':'c','specialization':'backend','grade':'Middle','score':90,
        'status':'provisional','active':1,'created_at':'2020-01-01T00:00:00+00:00','payload':'{}'}

@pytest.mark.parametrize('patch',[{'active':0},{'status':'revoked'},{'status':'expired'},
    {'status':'failed'},{'status':'other'},{'grade':'Principal'},{'specialization':'other'},
    {'score':float('nan')},{'score':True},{'score':101},{'payload':'[]'},{'payload':'broken'},
    {'payload':'{"tested_skills":null}'},{'payload':'{"tested_skills":"python"}'},
    {'payload':'{"expires_at":"2020-01-01T00:00:00+00:00"}'}, {'payload':'{"expires_at":"2030-01-01"}'}])
def test_invalid_certificate_is_rejected_consistently(patch):
    row = dict(BASE, **patch)
    assert not usable_certificate(row)
    assert not select_certificate([row],NEED,{})

def test_age_is_disclosed_but_not_automatic_revocation_and_future_expiry_is_frozen():
    assert usable_certificate(BASE)
    row = dict(BASE,payload=json.dumps({'expires_at':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()}))
    assert usable_certificate(row)
    assert not usable_certificate(row,datetime.now(timezone.utc).timestamp()+86401)

@pytest.mark.parametrize('state',['revoked','expired','invalid','onboarding_incomplete','not_looking','usable'])
def test_complete_search_and_profile_path_excludes_before_pagination(clients,state):
    employer,candidate,_,_=clients
    with db.connection() as conn:
        conn.execute("UPDATE grade_certifications SET active=0 WHERE candidate_id!='c_anna'")
        row=conn.execute("SELECT * FROM candidates WHERE id='c_anna'").fetchone()
        payload=json.loads(row['payload']);payload.update(assessment_onboarding_done=state!='onboarding_incomplete',looking=state!='not_looking')
        conn.execute("UPDATE candidates SET payload=? WHERE id='c_anna'",(db.dumps(payload),))
        if state in {'revoked','expired','invalid'}:conn.execute("UPDATE grade_certifications SET status=? WHERE candidate_id='c_anna'",(state,))
    response=employer.post('/api/needs',json=NEED);assert response.status_code==201,response.text
    need=response.json()
    response=employer.post('/api/search-sessions',json={'need_id':need['id'],'limit':1});assert response.status_code==201,response.text
    data=response.json();expected=1 if state=='usable' else 0
    assert len(data['items'])==data['page']['total_at_snapshot']==expected
    assert data['algorithm']=='category-evidence-v2'
    detail=employer.get('/api/candidates/c_anna')
    assert detail.status_code==(200 if state=='usable' else 404)
    if expected:
        assert data['items'][0]['candidate']['contacts']=={}
    assert candidate.get('/api/assessments').status_code==200

def test_team_can_supply_real_profile_without_inventing_legal_requisites(clients):
    employer,_,admin,_=clients
    current=employer.get('/api/company').json()
    body={'entity_type':'team','legal_name':'Изолированная команда проверки',
          'description':'Разработка и проверка программных продуктов на тестовом стенде',
          'industry':'IT','contact':'isolated@example.org','team':'Команда разработчиков и аналитиков','version':current['version']}
    response=employer.put('/api/company',json=body);assert response.status_code==200,response.text
    profile=response.json();assert profile['verification']=='pending'
    assert profile['payload']['tax_id']==profile['payload']['domain']==''
    response=admin.post('/api/admin/companies/'+profile['org_id']+'/review',json={
        'version':profile['version'],'state':'verified','organization_evidence':'Изолированная фикстура команды, не реальная организация',
        'authority_evidence':'Разрешённый представитель тестовой команды'})
    assert response.status_code==200,response.text
    assert employer.get('/api/company').json()['payload']['verification_checks']['scope']=='team_representative'
    body.update(version=employer.get('/api/company').json()['version'],tax_id='1234567890')
    assert employer.put('/api/company',json=body).status_code==422

def test_optional_company_requisites_do_not_accept_malformed_values():
    from backend.trust import Company
    from pydantic import ValidationError
    body={'legal_name':'Организация','description':'Разработка программных продуктов',
          'industry':'IT','contact':'hr@example.org','team':'Команда продукта','version':0}
    assert Company(**body).tax_id==''
    for patch in [{'domain':'https://invalid.local/x'},{'tax_id':'123'}]:
        with pytest.raises(ValidationError):Company(**dict(body,**patch))
