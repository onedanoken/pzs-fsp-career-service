"""Cross-role acceptance tests. Fixtures are synthetic, never human validation."""
import hashlib,io,json,re,secrets,time
import pytest
from pypdf import PdfReader
from backend import db
from backend.jobs import Worker
from backend.item_generator import generate,grade_answer
from backend.assessments import process_generation
from backend.sandbox import sql_check,python_check

NEED={'title':'Python API team','description':'Build API services and reliable integrations','team':'Product infrastructure team','specialization':'backend','grade':'Middle','skills':['python','sql'],'salary_min':120000,'salary_max':180000,'employer_contact':'hr@example.org'}

def create_need(client):
    r=client.post('/api/needs',json=NEED);assert r.status_code==201,r.text
    return r.json()['id']
def invite(client,need,key=None,candidate='c_anna'):
    r=client.post('/api/invitations',json={'need_id':need,'candidate_id':candidate,'message':'Please discuss our team objectives','idempotency_key':key or secrets.token_hex(12)})
    assert r.status_code==201,r.text
    return r.json()
def drain():
    worker=Worker()
    for _ in range(100):
        if not worker.run_once():return
    raise AssertionError('queue did not drain')
def sign_new(client,email='fresh@example.test'):
    r=client.post('/api/auth/register',json={'name':'New Candidate','email':email,'password':'LongPassword!','role':'candidate','processing_consent':True});assert r.status_code==201,r.text
    client.headers['X-CSRF-Token']=r.json()['csrf']
    with db.connection() as conn:
        jobs=conn.execute("SELECT payload FROM jobs WHERE actor_id=? AND kind='mail.send' ORDER BY created_at DESC",(r.json()['id'],)).fetchall()
    token=re.search(r'#verify=([^\s]+)',json.loads(jobs[0][0])['text'])[1]
    assert client.post('/api/auth/verify',json={'token':token}).status_code==200
    assert client.post('/api/auth/verify',json={'token':token}).status_code==400
    return r.json()

def test_invitation_without_vacancy_contact_gate_and_other_org(clients):
    employer,candidate,other,sign=clients
    need=create_need(employer)
    assert employer.get('/api/candidates/c_anna').json()['contacts']=={}
    item=invite(employer,need,'same-request-key-001')
    assert 'vacancy_id' not in item
    assert invite(employer,need,'same-request-key-001')['id']==item['id']
    sign(other,'u_employer2')
    assert other.get('/api/invitations').json()['items']==[]
    assert other.get('/api/need-passports/'+item['payload']['passport_id']).status_code==404
    assert candidate.get('/api/candidates/c_timur').status_code==403
    assert candidate.get('/api/candidates').status_code==403
    r=candidate.post('/api/invitations/'+item['id']+'/respond',json={'action':'viewed','version':item['version']});assert r.status_code==200
    item=r.json();assert employer.get('/api/candidates/c_anna').json()['contacts']=={}
    r=candidate.post('/api/invitations/'+item['id']+'/respond',json={'action':'accepted','version':item['version']});assert r.status_code==200
    assert employer.get('/api/candidates/c_anna').json()['contacts']['email']=='c_anna@example.org'
    assert other.get('/api/candidates/c_anna').json()['contacts']=={}
    assert candidate.post('/api/contacts/revoke',json={}).status_code==200
    assert employer.get('/api/candidates/c_anna').json()['contacts']=={}

def test_private_text_pdf_and_clarification_do_not_leak(clients):
    employer,candidate,expert,sign=clients
    with db.connection() as conn:
        row=conn.execute("SELECT payload FROM candidates WHERE id='c_anna'").fetchone();data=json.loads(row[0]);data['bio']='Contact private-leak@example.test +7 (999) 111-22-33 https://t.me/private_name'
        conn.execute("UPDATE candidates SET payload=? WHERE id='c_anna'",(db.dumps(data),))
    response=employer.get('/api/candidates/c_anna')
    assert 'private-leak' not in response.text and '999' not in response.json()['bio']
    pdf=employer.get('/api/candidates/c_anna/pdf');assert pdf.status_code==200
    text='\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)
    assert 'private-leak' not in text and 'c_anna@example.org' not in text
    own=candidate.get('/api/profile/pdf');assert own.status_code==200
    assert 'c_anna@example.org' in ''.join(p.extract_text() for p in PdfReader(io.BytesIO(own.content)).pages)
    item=invite(employer,create_need(employer))
    r=candidate.post('/api/invitations/'+item['id']+'/respond',json={'action':'clarification','version':item['version'],'message':'Email private-leak@example.test'});assert r.status_code==200
    assert 'private-leak' not in employer.get('/api/invitations').text

def test_search_refinement_preserves_prior_results_and_scope(clients):
    employer,candidate,other,sign=clients;need=create_need(employer)
    first=employer.post('/api/search-sessions',json={'need_id':need}).json()
    assert first['items']
    second=employer.post('/api/search-sessions',json={'need_id':need,'session_id':first['id'],'version':first['version'],'skills':['kubernetes']});assert second.status_code==201
    history=employer.get('/api/search-sessions/'+first['id']).json()['payload']['history']
    assert len(history)==2 and history[0]['candidate_ids'] and not history[1]['candidate_ids']
    sign(other,'u_employer2');assert other.get('/api/search-sessions/'+first['id']).status_code==404

def test_unverified_email_and_consent_are_required(clients):
    employer,candidate,expert,sign=clients
    body={'name':'New Person','email':'blocked@example.test','password':'LongPassword!','role':'candidate'}
    assert candidate.post('/api/auth/register',json=body).status_code==422
    r=candidate.post('/api/auth/register',json=body|{'processing_consent':True});assert r.status_code==201
    candidate.headers['X-CSRF-Token']=r.json()['csrf']
    assert candidate.post('/api/assessments',json={'specialization':'qa','grade':'Junior'}).status_code==403
    assert r.json()['candidate_id'] not in [c['id'] for c in employer.get('/api/candidates').json()['items']]

def fill_assessment(client,identity,correct=True):
    with db.connection() as conn:
        payload=json.loads(conn.execute('SELECT payload FROM assessment_sessions WHERE id=?',(identity,)).fetchone()[0])
    current=client.get('/api/assessments/'+identity).json()
    for item in payload['items']:
        expected=item['private'].get('expected')
        if item['kind']=='sql':
            threshold=re.search(r'amount ≥ (\d+)',item['prompt'])[1]
            answer=f"SELECT team,SUM(amount) FROM events WHERE status='ok' AND amount>={threshold} GROUP BY team"
        elif item['kind']=='multi':answer=json.dumps(expected)
        elif expected is not None:answer=str(expected)
        else:answer='Use retries and concurrency checks with observable invariants.'
        if not correct and item['kind']!='explanation':answer='invalid'
        r=client.put('/api/assessments/'+identity+'/answer',json={'item_id':item['id'],'answer':answer,'version':current['version']});assert r.status_code==200,r.text
        current=r.json()
    r=client.post('/api/assessments/'+identity+'/finish',json={'version':current['version']});assert r.status_code==202,r.text
    drain();return client.get('/api/assessments/'+identity).json()

def test_assessment_autosave_hidden_keys_initial_cycle_and_cooldown(clients):
    employer,candidate,expert,sign=clients;new=sign_new(candidate)
    r=candidate.post('/api/assessments',json={'specialization':'qa','grade':'Junior'});assert r.status_code==202;identity=r.json()['id'];drain()
    current=candidate.get('/api/assessments/'+identity).json()
    assert current['status']=='active' and 'private' not in json.dumps(current) and 'seed' not in current
    item=current['items'][0]
    r=candidate.put('/api/assessments/'+identity+'/answer',json={'item_id':item['id'],'answer':'1','version':current['version']});assert r.status_code==200
    assert candidate.get('/api/assessments/'+identity).json()['answers'][item['id']]=='1'
    assert candidate.put('/api/assessments/'+identity+'/answer',json={'item_id':item['id'],'answer':'2','version':current['version']}).status_code==409
    result=fill_assessment(candidate,identity)
    assert result['result']['score']==100 and result['result']['passed']
    assert result['result']['calibration']=='pilot_unvalidated'
    r=candidate.post('/api/assessments',json={'specialization':'qa','grade':'Middle'});assert r.status_code==202,r.text;drain()
    failed=fill_assessment(candidate,r.json()['id'],False);assert not failed['result']['passed']
    certs=candidate.get('/api/assessments').json()['certifications'];assert next(c for c in certs if c['active'])['grade']=='Junior'
    assert candidate.post('/api/assessments/onboarding/complete',json={}).status_code==200
    assert candidate.post('/api/assessments',json={'specialization':'qa','grade':'Senior'}).status_code==409
    assert candidate.post('/api/assessments',json={'specialization':'qa','grade':'Senior','mode':'practice'}).status_code==202

@pytest.mark.parametrize('query',["ATTACH DATABASE 'private.db' AS stolen","DROP TABLE events","SELECT load_extension('unsafe')","UPDATE events SET amount=0"])
def test_sql_runner_disallows_files_extensions_and_writes(query):
    assert sql_check(query,[['x',10,'ok']],[['x',10]])['passed']==0

def test_code_runner_fails_closed_without_sandbox(monkeypatch):
    monkeypatch.setenv('FSP_CODE_SANDBOX','disabled')
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:python_check('import os; os.remove("private")',[])
    assert error.value.status_code==503

def test_variants_vary_and_wrong_answers_fail(monkeypatch):
    monkeypatch.delenv('FSP_OLLAMA_URL',raising=False)
    conditions=set()
    for _ in range(25):
        data=generate('qa','Junior');conditions.add(data['items'][0]['prompt'])
        numeric=data['items'][0]
        assert grade_answer(numeric,str(numeric['private']['expected']))['score']==100
        assert grade_answer(numeric,str(numeric['private']['expected']+1))['score']==0
        assert len({x['kind'] for x in data['items']})>=5
    assert len(conditions)==25

def test_universal_ats_actual_contract_cycle_and_idempotency(clients):
    employer,candidate,other,sign=clients
    key=employer.post('/api/ats-connections',json={'name':'Reference ATS','scopes':['needs.write','profiles.read','stages.write','events.read']});assert key.status_code==201,key.text
    headers={'Authorization':'Bearer '+key.json()['token'],'Idempotency-Key':'import-need-request-001'}
    body={'external_id':'ATS-471','need':NEED}
    r=other.post('/api/ats/v1/needs',json=body,headers=headers);assert r.status_code==201,r.text;need=r.json()['id']
    assert other.post('/api/ats/v1/needs',json=body,headers=headers).json()['id']==need
    assert other.post('/api/ats/v1/needs',json=body|{'external_id':'ATS-472'},headers=headers).status_code==409
    assert other.get('/api/ats/v1/profiles').status_code==401
    item=invite(employer,need)
    assert other.get('/api/ats/v1/profiles',headers=headers).json()['items']==[]
    r=candidate.post('/api/invitations/'+item['id']+'/respond',json={'action':'accepted','version':item['version']});assert r.status_code==200
    assert other.get('/api/ats/v1/profiles',headers=headers).json()['items']==[]
    assert candidate.post('/api/invitations/'+item['id']+'/ats-consent',json={'granted':True,'fields':['name','contacts','categories']}).status_code==200
    profiles=other.get('/api/ats/v1/profiles',headers=headers).json()['items'];assert len(profiles)==1 and profiles[0]['profile']['contacts']
    assert 'answers' not in json.dumps(profiles) and 'bio' not in profiles[0]['profile']
    stage={'invitation_id':item['id'],'external_candidate_id':'C-871','stage':'Interview'}
    headers['Idempotency-Key']='stage-update-request-001'
    r=other.post('/api/ats/v1/stages',json=stage,headers=headers);assert r.status_code==200,r.text
    assert other.post('/api/ats/v1/stages',json=stage,headers=headers).json()==r.json()
    assert candidate.get('/api/invitations/'+item['id']+'/recruitment-stage').json()['stage']=='Interview'
    headers['Idempotency-Key']='close-need-request-001'
    assert other.delete('/api/ats/v1/needs/ATS-471',headers=headers).status_code==200
    assert employer.post('/api/invitations',json={'need_id':need,'candidate_id':'c_claims','message':'New invitation after closed need','idempotency_key':'closed-need-request-001'}).status_code==409
    assert candidate.post('/api/invitations/'+item['id']+'/ats-consent',json={'granted':False}).status_code==200
    assert other.get('/api/ats/v1/profiles',headers=headers).json()['items']==[]
    assert employer.post('/api/ats-connections/'+key.json()['id']+'/revoke',json={}).status_code==200
    assert other.get('/api/ats/v1/profiles',headers=headers).status_code==401

def test_ats_scopes_and_cross_organization_stage_protection(clients):
    employer,candidate,other,sign=clients
    key=employer.post('/api/ats-connections',json={'name':'Read only','scopes':['events.read']}).json()
    assert other.get('/api/ats/v1/profiles',headers={'Authorization':'Bearer '+key['token']}).status_code==403
    sign(other,'u_employer2');other_key=other.post('/api/ats-connections',json={'name':'Other organization','scopes':['stages.write']}).json()
    item=invite(employer,create_need(employer));candidate.post('/api/invitations/'+item['id']+'/respond',json={'action':'accepted','version':item['version']})
    r=other.post('/api/ats/v1/stages',json={'invitation_id':item['id'],'external_candidate_id':'foreign','stage':'Interview'},headers={'Authorization':'Bearer '+other_key['token'],'Idempotency-Key':'foreign-stage-request-001'})
    assert r.status_code==404

def test_risky_need_complaint_block_and_appeal(clients):
    employer,candidate,expert,sign=clients
    risky=employer.post('/api/needs',json=NEED|{'description':'Переведите деньги за доступ к вакансии'});assert risky.status_code==201
    assert risky.json()['state']=='pending'
    assert employer.post('/api/invitations',json={'need_id':risky.json()['id'],'candidate_id':'c_anna','message':'Please discuss our team','idempotency_key':'risky-need-request-001'}).status_code==409
    # A private pending need is not visible to this candidate; company reports remain available.
    assert candidate.post('/api/complaints',json={'org_id':'org_sigma','need_id':risky.json()['id'],'kind':'payment_request','message':'Потребность требует денежного перевода'}).status_code==404
    complaint=candidate.post('/api/complaints',json={'org_id':'org_sigma','kind':'payment_request','message':'Компания требует денежного перевода'});assert complaint.status_code==201
    assert expert.post('/api/admin/complaints/'+complaint.json()['id']+'/resolve',json={'state':'confirmed','resolution':'Проверено требование оплаты за доступ'}).status_code==200
    assert candidate.post('/api/employer-blocks',json={'org_id':'org_sigma','blocked':True}).status_code==200
    assert employer.post('/api/invitations',json={'need_id':create_need(employer),'candidate_id':'c_anna','message':'Please discuss our team','idempotency_key':'blocked-company-request-001'}).status_code==404
    assert employer.post('/api/complaints',json={'org_id':'org_sigma','kind':'appeal','message':'Просим пересмотреть ограничение по доказательствам'}).status_code==201

def test_external_identity_ownership_conflict_and_revocation(clients):
    employer,candidate,expert,sign=clients
    assert candidate.post('/api/fsp/link',json={'participant_id':'FSP-17'}).status_code==201
    proof=expert.post('/api/admin/fsp/proofs',json={'candidate_id':'c_anna','participant_id':'FSP-17','evidence':'Synthetic authorized ownership check'});assert proof.status_code==201
    token=proof.json()['proof']
    sign(employer,'u_timur');employer.post('/api/fsp/link',json={'participant_id':'FSP-17'})
    assert employer.post('/api/fsp/confirm',json={'token':token}).status_code==400
    linked=candidate.post('/api/fsp/confirm',json={'token':token});assert linked.status_code==200,linked.text
    assert linked.json()['status']=='demo'
    assert candidate.post('/api/fsp/confirm',json={'token':token}).status_code==400
    second=expert.post('/api/admin/fsp/proofs',json={'candidate_id':'c_timur','participant_id':'FSP-17','evidence':'Second synthetic ownership assertion'}).json()['proof']
    assert employer.post('/api/fsp/confirm',json={'token':second}).status_code==409
    assert candidate.post('/api/fsp/identities/'+linked.json()['id']+'/revoke',json={}).status_code==200
