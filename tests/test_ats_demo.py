"""Interactive emulator drives real HTTP routes with isolated synthetic actors."""
import json
import pytest
from backend import db

def adopt(client,result):
    assert result.status_code==200,result.text
    data=result.json()
    if 'user' in data:client.headers['X-CSRF-Token']=data['user']['csrf']
    return data

def start(client):return adopt(client,client.post('/api/demo/ats/start',json={}))
def role(client,value):return adopt(client,client.post('/api/demo/ats/role',json={'role':value}))
def act(client,value,**extra):return adopt(client,client.post('/api/demo/ats/action',json={'action':value,**extra}))['state']

def test_mock_end_to_end_uses_two_consents_retry_stage_and_physical_synthetic_removal(clients):
    employer,candidate,other,sign=clients
    created=start(employer);assert created['state']['role']=='employer'
    state=act(employer,'import');assert state['need']['state']=='active'
    act(employer,'invite');state=act(employer,'sync');assert state['record'] is None
    role(employer,'candidate');act(employer,'accept')
    role(employer,'employer');assert act(employer,'sync')['record'] is None
    role(employer,'candidate');act(employer,'consent')
    role(employer,'employer');state=act(employer,'sync');assert state['record']['profile']['contacts']['email']=='candidate-ats@example.invalid';identity=state['record']['id']
    assert act(employer,'stage')['ats_stage']=='Собеседование'
    failure=act(employer,'fail');assert failure['last_exchange']['status']=='failed' and failure['failure_seen']
    retry=act(employer,'sync');assert retry['last_exchange']['status']=='completed' and retry['record']['id']==identity
    role(employer,'candidate');state=act(employer,'withdraw');assert state['awaiting_ack']==1 and state['record']
    role(employer,'employer');state=act(employer,'sync');assert state['record'] is None and state['awaiting_ack']==0 and state['removed']
    assert act(employer,'close')['need']['state']=='closed'
    original=role(employer,'original');assert original['user']['id']=='u_employer'

def test_sandboxes_do_not_share_records_candidates_or_actions(clients):
    first,second,other,sign=clients
    sign(second,'u_employer')
    left=start(first);right=start(second);assert left['state']['id']!=right['state']['id']
    assert first.get('/api/candidates/c_anna').status_code==404
    assert 'Анна Смирнова' not in json.dumps(first.get('/api/candidates').json(),ensure_ascii=False)
    act(first,'import');act(first,'invite')
    role(first,'candidate');act(first,'accept');act(first,'consent');role(first,'employer');act(first,'sync')
    assert second.get('/api/demo/ats').json()['state']['record'] is None
    assert first.post('/api/demo/ats/role',json={'role':'invalid'}).status_code==422
    assert first.post('/api/demo/ats/action',json={'action':'accept'}).status_code==403
    token=first.cookies.get('fsp_ats_demo');second.cookies.delete('fsp_ats_demo');second.cookies.set('fsp_ats_demo',token)
    assert second.get('/api/demo/ats').status_code==403
    assert second.post('/api/demo/ats/action',json={'action':'sync'}).status_code==403
    role(first,'original')
    listed=first.get('/api/candidates').json()
    assert 'Тестовый кандидат ATS' not in json.dumps(listed,ensure_ascii=False)

def test_scenario_candidate_stays_synthetic_and_restart_does_not_add_certificates(clients):
    employer,candidate,other,sign=clients;start(employer);role(employer,'candidate')
    profile=employer.get('/api/profile').json()
    body={key:profile[key] for key in ['name','title','role','city','skills','bio','visible','work_modes','version']}
    body['name']='User supplied real details'
    assert employer.put('/api/profile',json=body).status_code==403
    assert employer.post('/api/profile/resumes',files={'file':('test.txt',b'User supplied personal details','text/plain')}).status_code==403
    with db.connection() as conn:before=conn.execute('SELECT COUNT(*) FROM grade_certifications WHERE candidate_id=?',(profile['id'],)).fetchone()[0]
    from backend.demo_stage03 import seed_domain
    seed_domain()
    with db.connection() as conn:assert conn.execute('SELECT COUNT(*) FROM grade_certifications WHERE candidate_id=?',(profile['id'],)).fetchone()[0]==before

def test_mock_disabled_and_real_candidate_cannot_seed_synthetic_run(clients,monkeypatch):
    employer,candidate,other,sign=clients
    monkeypatch.setattr(db,'DEMO',False)
    assert employer.post('/api/demo/ats/start',json={}).status_code==401  # Synthetic session is rejected before routing.
    monkeypatch.setattr(db,'DEMO',True)
    with db.connection() as conn:
        row=conn.execute("SELECT payload FROM candidates WHERE id='c_anna'").fetchone();data=json.loads(row[0]);data['demo']=False;conn.execute("UPDATE candidates SET payload=? WHERE id='c_anna'",(db.dumps(data),))
    assert candidate.post('/api/demo/ats/start',json={}).status_code==403

def test_reset_keeps_original_data_and_retires_only_owned_synthetic_scenario(clients):
    employer,candidate,other,sign=clients
    with db.connection() as conn:baseline=conn.execute("SELECT payload FROM candidates WHERE id='c_anna'").fetchone()[0]
    created=start(employer);act(employer,'import')
    updated=adopt(employer,employer.post('/api/demo/ats/start',json={'reset':True}));assert created['state']['id']!=updated['state']['id']
    with db.connection() as conn:
        assert conn.execute("SELECT payload FROM candidates WHERE id='c_anna'").fetchone()[0]==baseline
        old=json.loads(conn.execute('SELECT value FROM meta WHERE key=?',('ats_demo:'+created['state']['id'],)).fetchone()[0])
        assert old['expires']==0
        assert conn.execute('SELECT visible FROM candidates WHERE id=?',(old['candidate_id'],)).fetchone()[0]==0

def test_expired_scenario_allows_return_to_original_account_but_no_new_actions(clients):
    employer,candidate,other,sign=clients;created=start(employer)
    with db.connection() as conn:
        row=conn.execute('SELECT value FROM meta WHERE key=?',('ats_demo:'+created['state']['id'],)).fetchone();state=json.loads(row[0]);state['expires']=0;conn.execute('UPDATE meta SET value=? WHERE key=?',(db.dumps(state),'ats_demo:'+state['id']))
    assert employer.post('/api/demo/ats/action',json={'action':'import'}).status_code==410
    assert role(employer,'original')['user']['id']=='u_employer'
