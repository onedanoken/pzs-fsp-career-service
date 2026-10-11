"""Candidate protection: scoped reports, retries, moderation and private history."""
import pytest
from backend import db

BODY={'org_id':'org_sigma','kind':'payment_request','message':'Компания требует оплату перед собеседованием.'}

def test_complaint_retry_history_and_moderator_decision_are_private(clients):
    employer,candidate,expert,sign=clients
    created=candidate.post('/api/complaints',json=BODY);assert created.status_code==201,created.text
    identity=created.json()['id']
    retry=candidate.post('/api/complaints',json=BODY);assert retry.json()['id']==identity
    assert len(candidate.get('/api/complaints').json()['items'])==1
    assert employer.get('/api/complaints').json()['items']==[]
    sign(employer,'u_timur');assert employer.get('/api/complaints').json()['items']==[]
    item=expert.get('/api/complaints').json()['items'][0]
    assert item['company']=='СигмаТех' and item['state']=='open'
    decision={'state':'confirmed','resolution':'По предоставленным обстоятельствам подтверждено требование денежного перевода.'}
    assert expert.post('/api/admin/complaints/'+identity+'/resolve',json=decision|{'resolution':'          '}).status_code==422
    assert candidate.post('/api/admin/complaints/'+identity+'/resolve',json=decision).status_code==403
    assert expert.post('/api/admin/complaints/'+identity+'/resolve',json=decision).status_code==200
    own=candidate.get('/api/complaints').json()['items'][0]
    assert own['state']=='confirmed' and own['resolution']==decision['resolution'] and own['resolved_at']
    assert any(x['kind']=='complaint' for x in candidate.get('/api/notifications').json()['items'])
    assert expert.post('/api/admin/complaints/'+identity+'/resolve',json={'state':'dismissed','resolution':'Новое решение не должно перезаписать сохранённое.'}).status_code==409
    assert candidate.get('/api/complaints').json()['items'][0]['state']=='confirmed'

def test_private_need_and_foreign_company_are_not_reportable_by_guessing(clients):
    employer,candidate,expert,sign=clients
    targets=candidate.get('/api/complaint-targets').json()['items']
    assert any(x['org_id']=='org_sigma' for x in targets)
    assert all(set(x)=={'org_id','company'} for x in targets)
    with db.connection() as conn:
        conn.execute("INSERT INTO organizations(id,name) VALUES('private_org','Private organization')")
        conn.execute("INSERT INTO employer_needs(id,org_id,payload,state,expires,created_at) VALUES('private_need','org_sigma',?,'pending',0,?)",(db.dumps({'title':'Internal recruitment strategy'}),db.now()))
    assert candidate.post('/api/complaints',json=BODY|{'org_id':'private_org'}).status_code==404
    assert candidate.post('/api/complaints',json=BODY|{'need_id':'private_need'}).status_code==404
    assert 'Internal recruitment strategy' not in candidate.get('/api/complaint-targets').text
    assert employer.get('/api/complaint-targets').status_code==403
    assert employer.post('/api/complaints',json=BODY|{'org_id':'org_contour','kind':'appeal'}).status_code==403

@pytest.mark.parametrize('extra',[{'message':'          '},{'kind':'arbitrary_kind'},{'message':'a'*2001}])
def test_invalid_complaints_do_not_create_cases(clients,extra):
    employer,candidate,expert,sign=clients
    assert candidate.post('/api/complaints',json=BODY|extra).status_code==422
    assert candidate.get('/api/complaints').json()['items']==[]

def test_candidate_can_report_their_own_invitation_after_contact_withdrawal(clients):
    employer,candidate,expert,sign=clients
    need={'title':'Reportable invitation','description':'Develop reliable APIs and infrastructure','team':'Platform development team','specialization':'backend','grade':'Middle','skills':['python','sql'],'salary_min':120000,'salary_max':180000,'employer_contact':'hr@example.org'}
    created=employer.post('/api/needs',json=need).json()
    invite=employer.post('/api/invitations',json={'need_id':created['id'],'candidate_id':'c_anna','message':'Обсудим задачи нашей команды','idempotency_key':'complaint-own-invitation-001'});assert invite.status_code==201,invite.text
    item=invite.json()
    assert employer.post('/api/invitations/'+item['id']+'/respond',json={'action':'withdrawn','version':item['version']}).status_code==200
    report=candidate.post('/api/complaints',json=BODY|{'need_id':created['id']});assert report.status_code==201,report.text
    assert candidate.get('/api/complaints').json()['items'][0]['need_title']==need['title']
    assert expert.post('/api/admin/complaints/'+report.json()['id']+'/resolve',json={'state':'dismissed','resolution':'Проверены условия: нарушение не подтверждено.'}).status_code==200
    with db.connection() as conn:assert conn.execute('SELECT state FROM employer_needs WHERE id=?',(created['id'],)).fetchone()[0]=='active'

def test_complaint_daily_quota_and_csrf_do_not_silently_discard_reports(clients):
    employer,candidate,expert,sign=clients
    response=candidate.post('/api/complaints',json=BODY,headers={'X-CSRF-Token':''});assert response.status_code==403
    with db.connection() as conn:
        for i in range(20):
            conn.execute("INSERT INTO moderation_cases VALUES(?,?,?,?,?,?,'dismissed',?,?,?)",('quota_'+str(i),'u_anna','org_sigma',None,'other','Synthetic report '+str(i),'Reviewed synthetic example',db.now(),db.now()))
    assert candidate.post('/api/complaints',json=BODY).status_code==429
    assert len(candidate.get('/api/complaints').json()['items'])==20

def test_ats_sandbox_cannot_discover_or_report_other_companies(clients):
    employer,candidate,expert,sign=clients
    started=employer.post('/api/demo/ats/start',json={}).json();employer.headers['X-CSRF-Token']=started['user']['csrf']
    employer.post('/api/demo/ats/action',json={'action':'import'})
    employer.post('/api/demo/ats/action',json={'action':'invite'})
    adopted=employer.post('/api/demo/ats/role',json={'role':'candidate'}).json();employer.headers['X-CSRF-Token']=adopted['user']['csrf']
    targets=employer.get('/api/complaint-targets').json()['items']
    assert len(targets)==1 and targets[0]['org_id']!='org_sigma'
    assert employer.post('/api/complaints',json=BODY).status_code==404
    assert targets[0]['org_id'] not in candidate.get('/api/complaint-targets').text
