import hashlib,json,time
import pytest
from backend import db
from backend.auth import password_hash

PASS='Administrator phrase 2026!'

def setup_owner(admin):
    with db.connection() as conn:
        conn.execute("UPDATE users SET username='owner.test',admin_access='owner',auth_kind='username',is_synthetic=0,password=? WHERE id='u_admin'",(password_hash(PASS),))
    response=admin.post('/api/auth/login',json={'username':'owner.test','password':PASS});assert response.status_code==200
    admin.headers['X-CSRF-Token']=response.json()['csrf']

def register(client,username='new.person'):
    r=client.post('/api/auth/register',json={'username':username,'password':'Candidate phrase 2026!','name':'Участник','role':'candidate','processing_consent':True})
    assert r.status_code==201,r.text
    client.headers['X-CSRF-Token']=r.json()['csrf'];return r.json()

def action(admin,identity,kind,**extra):
    version=admin.get('/api/admin/users/'+identity).json()['account_version']
    return admin.post('/api/admin/users/'+identity+'/actions',json={'action':kind,'version':version,'reason':'Проверка полномочий','current_password':PASS,**extra})

def test_username_registration_without_mail_and_recovery_once(clients,monkeypatch):
    employer,candidate,admin,_=clients
    monkeypatch.setenv('FSP_LEGACY_EMAIL_AUTH','0')
    user=register(candidate);assert user['email'] is None and user['auth_kind']=='username'
    with db.connection() as conn:
        assert not conn.execute("SELECT 1 FROM jobs WHERE actor_id=? AND kind='mail.send'",(user['id'],)).fetchone()
        stored=conn.execute('SELECT hash FROM account_recovery WHERE user_id=?',(user['id'],)).fetchone()[0]
        assert stored==hashlib.sha256(user['recovery_code'].encode()).hexdigest()
    body={'username':'NEW.PERSON','recovery_code':user['recovery_code'],'password':'New long phrase 2026!'}
    assert employer.post('/api/auth/recover',json=body).status_code==200
    assert candidate.get('/api/auth/me').status_code==401
    assert employer.post('/api/auth/recover',json=body).status_code==400
    assert employer.post('/api/auth/login',json={'username':'new.person','password':body['password']}).status_code==200
    assert employer.post('/api/auth/register',json={'username':'NEW.PERSON','password':body['password'],'name':'Другой участник','role':'candidate','processing_consent':True}).status_code==409

def test_owner_controls_block_revoke_and_role_with_version(clients):
    employer,candidate,admin,_=clients;setup_owner(admin);u=register(candidate)
    for path in ['/api/admin/users','/api/admin/audit','/api/admin/overview']:
        assert employer.get(path).status_code==403 and candidate.get(path).status_code==403
    assert action(admin,u['id'],'block').status_code==200
    assert candidate.get('/api/auth/me').status_code==401
    assert candidate.post('/api/auth/login',json={'username':u['username'],'password':'Candidate phrase 2026!'}).status_code==401
    assert action(admin,u['id'],'unblock').status_code==200
    assert action(admin,u['id'],'access',access='operator').status_code==200
    sign=candidate.post('/api/auth/login',json={'username':u['username'],'password':'Candidate phrase 2026!'});candidate.headers['X-CSRF-Token']=sign.json()['csrf']
    assert candidate.get('/api/admin/users').status_code==403
    assert candidate.get('/api/admin/complaints').status_code==200
    assert employer.get('/api/admin/complaints').status_code==403
    assert action(admin,u['id'],'access',access='none').status_code==200
    assert candidate.get('/api/auth/me').status_code==401
    assert action(admin,'u_admin','block').status_code==409
    assert action(admin,'u_anna','unblock').status_code==409

def test_owner_confirmation_conflict_csrf_and_audit_no_secrets(clients):
    employer,candidate,admin,_=clients;setup_owner(admin);u=register(candidate)
    body={'action':'block','version':1,'reason':'Проверка полномочий','current_password':'wrong'}
    assert admin.post('/api/admin/users/'+u['id']+'/actions',json=body).status_code==403
    assert admin.post('/api/admin/users/'+u['id']+'/actions',json=body|{'current_password':PASS,'version':99}).status_code==409
    csrf=admin.headers.pop('X-CSRF-Token');assert admin.post('/api/admin/users/'+u['id']+'/actions',json=body|{'current_password':PASS}).status_code==403;admin.headers['X-CSRF-Token']=csrf
    reset=action(admin,u['id'],'reset_password');assert reset.status_code==200
    assert candidate.post('/api/auth/login',json={'username':u['username'],'password':'Candidate phrase 2026!'}).status_code==401
    log=admin.get('/api/admin/audit').json();assert PASS not in json.dumps(log) and reset.json()['recovery_code'] not in json.dumps(log)
    assert candidate.get('/api/auth/me').status_code==401
    assert employer.post('/api/auth/recover',json={'username':u['username'],'recovery_code':reset.json()['recovery_code'],'password':'Recovered phrase 2026!'}).status_code==200

def test_registration_setting_and_demo_endpoint_default_closed(clients,monkeypatch):
    employer,candidate,admin,_=clients;setup_owner(admin)
    monkeypatch.delenv('FSP_TEST_LOGIN',raising=False)
    assert employer.post('/api/auth/demo',json={'account':'u_admin'}).status_code==404
    assert admin.put('/api/admin/settings',json={'registration_enabled':False,'current_password':PASS}).status_code==200
    assert candidate.get('/api/health').json()['registration_enabled'] is False
    assert candidate.post('/api/auth/register',json={'username':'closed.user','password':'Candidate phrase 2026!','name':'Участник','role':'candidate','processing_consent':True}).status_code==403
    assert admin.put('/api/admin/settings',json={'registration_enabled':True,'current_password':PASS}).status_code==200

def test_bootstrap_password_required_and_preserved_history(clients):
    employer,candidate,admin,_=clients
    from scripts.create_admin import create
    identity=create('new.owner','Владелец',PASS)
    login=employer.post('/api/auth/login',json={'username':'new.owner','password':PASS});assert login.status_code==200
    employer.headers['X-CSRF-Token']=login.json()['csrf'];assert login.json()['password_change_required']
    assert employer.get('/api/admin/users').status_code==403
    changed=employer.post('/api/auth/password',json={'current_password':PASS,'password':'My private new phrase!'});assert changed.status_code==200
    employer.headers['X-CSRF-Token']=changed.json()['csrf'];assert employer.get('/api/admin/users').status_code==200
    with db.connection() as conn:
        assert conn.execute("SELECT 1 FROM candidates WHERE id='c_anna'").fetchone()
        assert conn.execute("SELECT CAST(value AS INTEGER) FROM meta WHERE key='schema_version'").fetchone()[0]==7
    db.initialize()
    with db.connection() as conn:
        assert conn.execute('SELECT admin_access FROM users WHERE id=?',(identity,)).fetchone()[0]=='owner'

def test_username_evidence_eligibility_does_not_require_email(clients):
    employer,candidate,admin,_=clients
    user=register(candidate)
    from backend.privacy import eligible
    from backend.domain import list_candidates
    with db.connection() as conn:
        row=conn.execute('SELECT payload FROM candidates WHERE id=?',(user['candidate_id'],)).fetchone()
        data=json.loads(row[0]);data.update(assessment_onboarding_done=True,visible=True)
        conn.execute('UPDATE candidates SET payload=?,visible=1 WHERE id=?',(db.dumps(data),user['candidate_id']))
        conn.execute('INSERT INTO consent_events VALUES(?,?,?,?,?,?)',('publication-test',user['candidate_id'],'publication',1,'test',db.now()))
        # Active is a separate flag; use a status that the real issuer produces.
        conn.execute("INSERT INTO grade_certifications(id,candidate_id,specialization,grade,score,status,payload,created_at) VALUES(?,?,'backend','Junior',100,'provisional','{}',?)",('synthetic-cert-test',user['candidate_id'],db.now()))
        assert eligible(conn,dict(data,id=user['candidate_id']))
        conn.execute('UPDATE users SET active=0 WHERE id=?',(user['id'],))
        assert not eligible(conn,dict(data,id=user['candidate_id']))
    assert not any(x['id']==user['candidate_id'] for x in list_candidates())

def test_normal_admin_queues_hide_archived_test_organizations(clients,monkeypatch):
    employer,candidate,admin,_=clients;setup_owner(admin)
    with db.connection() as conn:
        before=conn.execute('SELECT COUNT(*) FROM company_profiles').fetchone()[0]
        conn.execute("INSERT INTO moderation_cases(id,actor_id,org_id,kind,message,state,created_at) VALUES('archived-case','u_anna','org_sigma','other','Synthetic archived case','open',?)",(db.now(),))
    monkeypatch.setattr(db,'DEMO',False)
    assert admin.get('/api/admin/companies').json()['items']==[]
    assert admin.get('/api/admin/needs').json()['items']==[]
    assert admin.get('/api/admin/complaints').json()['items']==[]
    assert admin.get('/api/admin/overview').json()['counts']['open_complaints']==0
    with db.connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM company_profiles').fetchone()[0]==before
        assert conn.execute("SELECT 1 FROM moderation_cases WHERE id='archived-case'").fetchone()
