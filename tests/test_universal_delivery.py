from fastapi.testclient import TestClient
import hashlib,json,time,socket,threading
from contextlib import contextmanager
import pytest,uvicorn
from backend import db
from backend.jobs import Worker
from backend.delivery_gateway import signature
from scripts.reference_integrations import reference_app

@contextmanager
def provider(monkeypatch):
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128);url='http://127.0.0.1:'+str(sock.getsockname()[1]);key='reference-test-key-not-for-production-0001'
    app=reference_app(url,key);server=uvicorn.Server(uvicorn.Config(app,log_level='error'));thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    for _ in range(100):
        if server.started:break
        time.sleep(.05)
    assert server.started
    monkeypatch.setenv('FSP_DELIVERY_API_URL',url);monkeypatch.setenv('FSP_DELIVERY_API_KEY',key)
    try:yield app,key,url
    finally:server.should_exit=True;thread.join(timeout=5)

def event(client,key,job,delivery,state='delivered',event_id='event-001'):
    data={'event_id':event_id,'message_id':job,'delivery_id':delivery,'state':state};raw=json.dumps(data,separators=(',',':')).encode();stamp=str(int(time.time()))
    return client.post('/api/integration-events/notifications',content=raw,headers={'Content-Type':'application/json','X-FSP-Timestamp':stamp,'X-FSP-Signature':signature(key,stamp,raw)})

def test_delivery_verification_reset_and_signed_receipts(clients,monkeypatch):
    employer,candidate,admin,_=clients
    external=TestClient(__import__('backend.main',fromlist=['app']).app)
    with provider(monkeypatch) as (app,key,url):
        with db.connection() as conn:
            conn.execute("UPDATE users SET email_verified=0 WHERE id='u_anna'")
        assert candidate.post('/api/auth/resend',json={}).status_code==200
        Worker().run_once();assert len(app.state.messages)==1
        identity,record=next(iter(app.state.messages.items()));text=record['payload']['text'];token=text.split('#verify=')[1].split('\n')[0]
        assert candidate.post('/api/auth/verify',json={'token':token}).status_code==200
        assert candidate.post('/api/auth/verify',json={'token':token}).status_code==400
        assert event(external,key,identity,record['delivery_id']).status_code==200
        assert event(external,key,identity,record['delivery_id']).json()['duplicate']
        assert event(external,key,identity,record['delivery_id'],'accepted','event-002').status_code==409
        assert event(external,'wrong-key',identity,record['delivery_id']).status_code==401
        assert candidate.get('/api/admin/integration-status').status_code==403
        status=admin.get('/api/admin/integration-status').json();assert status['notification_api_configured'] and status['notification_receipts']==1
        assert key not in json.dumps(status) and token not in json.dumps(status)
        with db.connection() as conn:email=conn.execute("SELECT email FROM users WHERE id='u_anna'").fetchone()[0]
        assert employer.post('/api/auth/reset-request',json={'email':email}).status_code==200
        Worker().run_once()
        reset=next((r for r in app.state.messages.values() if '#reset=' in r['payload']['text']),None)
        assert reset is not None
        value=reset['payload']['text'].split('#reset=')[1].split('\n')[0]
        assert employer.post('/api/auth/reset',json={'token':value,'password':'Permitted-test-password1'}).status_code==200
        assert employer.post('/api/auth/reset',json={'token':value,'password':'Permitted-test-password2'}).status_code==400

def test_retry_keeps_notification_and_provider_idempotency(clients,monkeypatch):
    _,candidate,admin,_=clients
    with provider(monkeypatch) as (app,key,url):
        app.state.reject_once=True
        from backend.identity import notify
        with db.connection() as conn:notify(conn,'u_anna','reference_test','test-entity','Откройте кабинет: контакты и токены в уведомлении отсутствуют.')
        Worker().run_once()
        with db.connection() as conn:
            row=conn.execute("SELECT * FROM jobs WHERE kind='mail.send' ORDER BY created_at DESC LIMIT 1").fetchone();assert row['status']=='retry'
            assert conn.execute("SELECT COUNT(*) FROM notifications WHERE entity_id='test-entity'").fetchone()[0]==1
            conn.execute('UPDATE jobs SET available_at=0 WHERE id=?',(row['id'],))
        Worker().run_once();assert len(app.state.messages)==1
        assert candidate.get('/api/notifications').status_code==200

def test_public_contract_has_no_configuration_secrets(clients):
    _,candidate,_,_=clients;data=candidate.get('/api/integration-contracts').json()
    assert 'notifications' in data and 'identity' in data and 'generator' in data
    assert 'password' not in json.dumps(data).lower()

def test_malformed_timestamp_is_rejected_not_server_error(clients,monkeypatch):
    _,candidate,_,_=clients
    monkeypatch.setenv('FSP_DELIVERY_API_KEY','synthetic-reference-key-at-least-32-characters')
    for stamp in ['not-a-time','9'*400]:
        response=candidate.post('/api/integration-events/notifications',content=b'{}',headers={'X-FSP-Timestamp':stamp,'X-FSP-Signature':'bad'})
        assert response.status_code==401
