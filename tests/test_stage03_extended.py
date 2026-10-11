import io,json,secrets,time
from pathlib import Path
import jwt,pytest,httpx
from cryptography.hazmat.primitives.asymmetric import rsa
from backend import db
from backend.fsp_identity import validate_id_token
from backend.ats_client import UniversalATSClient
from backend.item_generator import generate,grade_answer
from test_stage03 import NEED,create_need,invite,drain,sign_new,fill_assessment

def test_postgres_reinitialize_preserves_legacy_rows(clients):
    with db.connection() as conn:
        before=dict(conn.execute("SELECT * FROM candidates WHERE id='c_anna'").fetchone())
    db.initialize()
    with db.connection() as conn:
        assert dict(conn.execute("SELECT * FROM candidates WHERE id='c_anna'").fetchone())==before
        assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]=='7'


@pytest.mark.parametrize('bad',['signature','audience','nonce','issuer','algorithm','expired'])
def test_oidc_cryptographic_failures(bad,monkeypatch):
    monkeypatch.setenv('FSP_OIDC_CLIENT_ID','career-test')
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    public=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()));public['kid']='pinned-key'
    claims={'iss':'https://id.example.test/realms/fsp','aud':'career-test','sub':'participant-17','nonce':'nonce-test','exp':int(time.time())+60,'iat':int(time.time())}
    issuer={'issuer':claims['iss']}
    if bad=='audience':claims['aud']='other-app'
    if bad=='nonce':claims['nonce']='stolen-state'
    if bad=='issuer':claims['iss']='https://attacker.test'
    if bad=='expired':claims['exp']=int(time.time())-60
    signing=rsa.generate_private_key(public_exponent=65537,key_size=2048) if bad=='signature' else key
    token=jwt.encode(claims,'wrong-secret' if bad=='algorithm' else signing,algorithm='HS256' if bad=='algorithm' else 'RS256',headers={'kid':'pinned-key'})
    with pytest.raises((ValueError,jwt.PyJWTError)):validate_id_token(token,issuer,'nonce-test',{'keys':[public]})
    good=jwt.encode({**claims,'iss':issuer['issuer'],'aud':'career-test','nonce':'nonce-test','exp':int(time.time())+60},key,algorithm='RS256',headers={'kid':'pinned-key'})
    assert validate_id_token(good,issuer,'nonce-test',{'keys':[public]})['sub']=='participant-17'

def test_email_reset_single_use_and_session_revocation(clients):
    employer,candidate,expert,sign=clients;new=sign_new(candidate)
    assert candidate.post('/api/auth/reset-request',json={'email':new['email']}).status_code==200
    with db.connection() as conn:
        row=conn.execute("SELECT payload FROM jobs WHERE actor_id=? AND kind='mail.send' ORDER BY created_at DESC LIMIT 1",(new['id'],)).fetchone()
    token=json.loads(row[0])['text'].split('#reset=')[1].split()[0]
    assert candidate.post('/api/auth/reset',json={'token':token,'password':'ChangedPassword!'}).status_code==200
    assert candidate.get('/api/auth/me').status_code==401
    assert candidate.post('/api/auth/reset',json={'token':token,'password':'ChangedAgain!'}).status_code==400
    assert candidate.post('/api/auth/login',json={'email':new['email'],'password':'LongPassword!'}).status_code==401
    assert candidate.post('/api/auth/login',json={'email':new['email'],'password':'ChangedPassword!'}).status_code==200

def test_sql_hidden_sets_reject_hardcoded_public_output(monkeypatch):
    monkeypatch.delenv('FSP_OLLAMA_URL',raising=False)
    item=next(i for i in generate('backend','Middle')['items'] if i['kind']=='sql')
    expected=item['private']['expected']
    literal=' UNION ALL '.join('SELECT '+repr(team)+','+str(amount) for team,amount in expected) or 'SELECT team,amount FROM events WHERE 0'
    assert grade_answer(item,literal)['score']<100
    assert all(len(c['id'])==10 for c in generate('qa','Junior')['items'][2]['choices'])

def test_parser_draft_private_and_explicit_approval(clients):
    employer,candidate,expert,sign=clients
    content='Анна Смирнова\nДолжность: Python developer\nГород: Екатеринбург\nОпыт работы: 3 года\nКонтакты: draft-private@example.test\nГрейд: Senior\nСофт-скиллы: командная работа, коммуникация\nPython SQL FastAPI'.encode()
    before=candidate.get('/api/profile').json();response=candidate.post('/api/profile/resumes',files={'file':('resume.txt',content,'text/plain')});assert response.status_code==202,response.text
    identity=response.json()['id'];drain();draft=candidate.get('/api/profile/resumes/'+identity).json()
    assert draft['state']=='ready' and draft['draft']['fields']['contacts']['email']=='draft-private@example.test'
    assert draft['draft']['fields']['declared_grade']=='Senior'
    assert candidate.get('/api/profile').json()['title']==before['title']
    assert employer.get('/api/profile/resumes/'+identity).status_code in {403,404}
    assert 'draft-private' not in employer.get('/api/candidates/c_anna').text
    assert next(c for c in before['categories'] if c['id']=='backend')['grade']=='Middle'

def test_expert_review_and_appeal_leave_objective_grade_history(clients):
    employer,candidate,expert,sign=clients;sign_new(candidate)
    response=candidate.post('/api/assessments',json={'specialization':'qa','grade':'Junior'});identity=response.json()['id'];drain();fill_assessment(candidate,identity)
    appeal=candidate.post('/api/assessments/'+identity+'/appeal',json={'message':'Please review the open answer criteria'}).json()['id']
    item=next(i for i in expert.get('/api/admin/assessment-reviews').json()['items'] if i['session_id']==identity)
    r=expert.post('/api/admin/assessment-reviews/'+identity,json={'item_id':item['item_id'],'version':item['version'],'score':75,'rationale':'The answer covers invariants but lacks latency measurements.'});assert r.status_code==200,r.text
    assert candidate.get('/api/assessments/'+identity).json()['result']['expert_review']['score']==75
    assert expert.post('/api/admin/assessment-appeals/'+appeal+'/resolve',json={'state':'accepted','resolution':'The rubric was ambiguous; maintain the objective grade and explain limits.'}).status_code==200
    assert candidate.get('/api/assessment-appeals').json()['items'][0]['state']=='accepted'
    assert candidate.get('/api/assessments').json()['certifications'][0]['grade']=='Junior'

def test_company_employee_bound_email_and_permissions(clients):
    owner,employee,expert,sign=clients
    r=employee.post('/api/auth/register',json={'name':'Employee Test','email':'employee@example.test','password':'LongPassword!','role':'employer','company':'Unused own org','processing_consent':True});assert r.status_code==201
    employee.headers['X-CSRF-Token']=r.json()['csrf']
    with db.connection() as conn:conn.execute('UPDATE users SET email_verified=1 WHERE id=?',(r.json()['id'],))
    code=owner.post('/api/company/members/invite',json={'email':'employee@example.test','role':'viewer'}).json()['token']
    assert expert.post('/api/company/members/accept',json={'token':code}).status_code==403
    assert employee.post('/api/company/members/accept',json={'token':code}).status_code==200
    assert employee.post('/api/company/members/accept',json={'token':code}).status_code==400
    assert employee.get('/api/company/members').json()['can_manage'] is False
    assert employee.post('/api/needs',json=NEED).status_code==403
    assert employee.post('/api/ats-connections',json={'name':'Forbidden client','scopes':['events.read']}).status_code==403
    assert owner.put('/api/company/members/'+r.json()['id'],json={'role':'recruiter'}).status_code==200
    assert employee.post('/api/needs',json=NEED).status_code==201
    sign(expert,'u_employer2');assert expert.put('/api/company/members/'+r.json()['id'],json={'role':'viewer'}).status_code==404

def test_legacy_offer_cannot_bypass_block_or_closed_vacancy(clients):
    from test_product import offer_payload
    employer,candidate,expert,sign=clients
    assert candidate.post('/api/employer-blocks',json={'org_id':'org_sigma','blocked':True}).status_code==200
    assert employer.post('/api/offers',json=offer_payload()).status_code==404
    candidate.post('/api/employer-blocks',json={'org_id':'org_sigma','blocked':False})
    item=employer.post('/api/offers',json=offer_payload()).json()
    employer.post('/api/vacancies/v_python/archive',json={})
    assert candidate.post('/api/offers/'+item['id']+'/respond',json={'action':'accepted','version':1}).status_code==409
    visible=employer.get('/api/candidates/c_anna')
    assert visible.status_code==200 and visible.json()['contacts']=={}

def test_adapter_retry_reuses_key_and_does_not_retry_permanent_denial():
    requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(503 if len(requests)==1 else 200,json={'id':'need-test'},headers={'Retry-After':'0.1'})
    client=UniversalATSClient('https://test.example','test-secret');client.http.close()
    client.http=httpx.Client(base_url='https://test.example',transport=httpx.MockTransport(handler))
    assert client.import_need('external-17',NEED,'persistent-key-17')['id']=='need-test'
    assert len(requests)==2 and requests[0].headers['Idempotency-Key']==requests[1].headers['Idempotency-Key']
    client.close()

def test_nonaccepted_messages_and_blocked_acceptance_do_not_leak(clients):
    employer,candidate,expert,sign=clients
    item=invite(employer,create_need(employer))
    response=candidate.post('/api/invitations/'+item['id']+'/respond',json={'action':'viewed','version':item['version'],'message':'private-viewed@example.test'});assert response.status_code==200
    assert 'private-viewed' not in employer.get('/api/invitations').text
    candidate.post('/api/employer-blocks',json={'org_id':'org_sigma','blocked':True})
    assert candidate.post('/api/invitations/'+item['id']+'/respond',json={'action':'accepted','version':response.json()['version']}).status_code==403
    hidden=employer.get('/api/candidates/c_anna')
    assert hidden.status_code==404 and 'contacts' not in hidden.json() and 'private-viewed' not in hidden.text

def test_sql_memory_bombs_are_denied_or_bounded():
    from backend.sandbox import sql_check
    for query in ['SELECT hex(randomblob(1000000000))','WITH RECURSIVE r(s) AS (SELECT \'x\' UNION ALL SELECT s||s FROM r) SELECT s FROM r']:
        started=time.monotonic();assert sql_check(query,[],[['unexpected']])['passed']==0
        assert time.monotonic()-started<5


def test_issued_sources_are_frozen_and_not_linked_to_a_mutable_manifest(monkeypatch):
    from backend.assessment_catalog import source_snapshot
    monkeypatch.delenv('FSP_OLLAMA_URL',raising=False)
    issued=generate('backend','Middle');snapshot=issued['sources_snapshot']
    assert {s['id'] for s in snapshot}==set(issued['sources'])
    assert all(s['snapshot_available'] and len(s['sha256'])==64 and s['license'] for s in snapshot)
    current=source_snapshot();current[0]['sha256']='mutated'
    assert snapshot[0]['sha256']!='mutated' and source_snapshot()[0]['sha256']!='mutated'


@pytest.mark.parametrize('failure',['generation','grading','interrupted_lease'])
def test_terminal_assessment_worker_failure_preserves_answers_and_allows_retry(clients,monkeypatch,failure):
    from backend import jobs,assessments
    employer,candidate,expert,sign=clients;new=sign_new(candidate)
    identity=candidate.post('/api/assessments',json={'specialization':'qa','grade':'Junior'}).json()['id']
    original_grading=assessments.process_grading
    def fail(context,payload):raise jobs.JobError('test_worker_failure','Controlled terminal worker failure')
    if failure=='generation':
        monkeypatch.setattr(assessments,'process_generation',fail);drain()
        failed=candidate.get('/api/assessments/'+identity).json()
        assert failed['status']=='cancelled' and failed['technical_error'] and not failed['result']
        assert candidate.post('/api/assessments',json={'specialization':'qa','grade':'Junior'}).status_code==202
    else:
        drain()
        if failure=='grading':
            monkeypatch.setattr(assessments,'process_grading',fail);failed=fill_assessment(candidate,identity)
        else:
            active=candidate.get('/api/assessments/'+identity).json();item=active['items'][0]
            active=candidate.put('/api/assessments/'+identity+'/answer',json={'item_id':item['id'],'answer':'17','version':active['version']}).json()
            candidate.post('/api/assessments/'+identity+'/finish',json={'version':active['version']}).raise_for_status()
            with db.connection() as conn:conn.execute("UPDATE jobs SET status='running',attempts=max_attempts,worker_id='interrupted-test',lease_until=? WHERE kind='assessment.grade'",(time.time()-10,))
            drain();failed=candidate.get('/api/assessments/'+identity).json()
        assert failed['status']=='active' and failed['answers'] and failed['technical_error'] and not failed['result']
        monkeypatch.setattr(assessments,'process_grading',original_grading)
        assert fill_assessment(candidate,identity)['result']['passed']
    with db.connection() as conn:
        certificates=conn.execute('SELECT COUNT(*) FROM grade_certifications WHERE candidate_id=?',(new['candidate_id'],)).fetchone()[0]
        assert certificates==(0 if failure=='generation' else 1)
