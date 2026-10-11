from fastapi.testclient import TestClient
from backend.main import app
from backend import db


def test_email_is_required_and_username_cannot_bypass_confirmation(clients,monkeypatch):
    monkeypatch.setenv('FSP_EMAIL_REQUIRED','1')
    monkeypatch.setenv('FSP_LEGACY_EMAIL_AUTH','0')
    client=TestClient(app)
    assert client.get('/api/health').json()['login_method']=='username_or_email'
    body=dict(username='new.confirmed',name='Новый кандидат',password='New-account-password-123',role='candidate',processing_consent=True)
    assert client.post('/api/auth/register',json=body).status_code==422
    response=client.post('/api/auth/register',json=dict(body,email='new-candidate@example.test'))
    assert response.status_code==201,response.text
    user=response.json();assert user['auth_kind']=='email' and not user['email_verified']
    client.headers['X-CSRF-Token']=user['csrf']
    assert client.post('/api/assessments',json={'specialization':'backend','grade':'Junior'}).status_code==403
    with db.connection() as conn:
        row=conn.execute("SELECT payload FROM jobs WHERE kind='mail.send' AND actor_id=?",(user['id'],)).fetchone()
        import json
        token=json.loads(row[0])['text'].split('#verify=')[1].split('\n')[0]
    assert client.post('/api/auth/verify',json={'token':token}).status_code==200
    assert client.post('/api/auth/verify',json={'token':token}).status_code==400
    client.post('/api/auth/logout',json={})
    assert client.post('/api/auth/login',json={'email':'new-candidate@example.test','password':body['password']}).status_code==200


def test_email_reset_is_single_use_and_revokes_existing_session(clients,monkeypatch):
    monkeypatch.setenv('FSP_EMAIL_REQUIRED','1')
    c=TestClient(app)
    body=dict(username='recovery.email',email='recovery@example.test',name='Проверка восстановления',password='Existing-email-password-123',role='candidate',processing_consent=True)
    r=c.post('/api/auth/register',json=body);assert r.status_code==201
    user=r.json();c.headers['X-CSRF-Token']=user['csrf']
    assert c.post('/api/auth/reset-request',json={'email':body['email']}).status_code==200
    with db.connection() as conn:
        import json
        jobs=[json.loads(r[0]) for r in conn.execute("SELECT payload FROM jobs WHERE actor_id=? AND kind='mail.send'",(user['id'],))]
        token=next(j['text'].split('#reset=')[1].split('\n')[0] for j in jobs if '#reset=' in j['text'])
    assert c.post('/api/auth/reset',json={'token':token,'password':'New-email-password-456'}).status_code==200
    assert c.get('/api/auth/me').status_code==401
    assert c.post('/api/auth/reset',json={'token':token,'password':'Other-email-password-456'}).status_code==400
    assert c.post('/api/auth/login',json={'email':body['email'],'password':'New-email-password-456'}).status_code==200


def test_submission_download_allowlist(clients):
    c=TestClient(app)
    response=c.get('/delivery/FSP_CAREER_GUIDE.pdf')
    assert response.status_code==200 and response.headers['content-type']=='application/pdf'
    assert c.get('/delivery/private.env').status_code==404
    assert c.get('/delivery/subfolder/private.env').status_code==404
