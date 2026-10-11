import base64
import hashlib
import re
from urllib.parse import parse_qs, urlparse

import jwt
from fastapi.testclient import TestClient
from scripts.mock_fsp_id import mock_app


def flow():
    issuer='http://127.0.0.1:17771/realms/fsp'
    callback='http://localhost:8000/api/fsp/oidc/callback'
    client=TestClient(mock_app(issuer,'Isolated-mock-password-123',callback))
    verifier='V'*64
    query=dict(client_id='fsp-career-mock', redirect_uri=callback, state='S'*32, nonce='N'*32,
               code_challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('='),
               code_challenge_method='S256', response_type='code', scope='openid profile')
    return client, issuer, callback, verifier, query


def test_mock_login_pkce_and_single_use_code():
    client,issuer,callback,verifier,query=flow()
    config=client.get('/realms/fsp/.well-known/openid-configuration').json()
    assert config['fsp_mock'] and config['issuer']==issuer
    page=client.get('/realms/fsp/protocol/openid-connect/auth',params=query)
    assert page.status_code==200 and 'не принадлежат Федерации' in page.text
    request_id=re.search('name="request_id" value="([^"]+)"',page.text)[1]
    assert client.post('/realms/fsp/login',data=dict(request_id=request_id,username='fsp-test-001',password='bad')).status_code==401
    response=client.post('/realms/fsp/login',data=dict(request_id=request_id,username='fsp-test-001',password='Isolated-mock-password-123'),follow_redirects=False)
    assert response.status_code==303
    qs=parse_qs(urlparse(response.headers['location']).query)
    assert qs['state']==['S'*32]
    body=dict(grant_type='authorization_code',client_id=query['client_id'],redirect_uri=callback,code=qs['code'][0],code_verifier=verifier)
    token=client.post('/realms/fsp/protocol/openid-connect/token',data=body)
    assert token.status_code==200
    keys=client.get('/realms/fsp/protocol/openid-connect/certs').json()
    claims=jwt.decode(token.json()['id_token'],jwt.PyJWK.from_dict(keys['keys'][0]).key,algorithms=['RS256'],issuer=issuer,audience=query['client_id'])
    assert claims['nonce']=='N'*32 and claims['fsp_mock'] and claims['participant_id'].startswith('MOCK-')
    assert client.post('/realms/fsp/protocol/openid-connect/token',data=body).status_code==400
    assert client.post('/realms/fsp/login',data=dict(request_id=request_id,username='fsp-test-001',password='Isolated-mock-password-123')).status_code==400


def test_unregistered_redirect_and_missing_pkce_rejected():
    client,_,_,_,query=flow()
    for field,value in [('redirect_uri','https://attacker.invalid/callback'),('code_challenge_method','plain'),('client_id','other'),('scope','profile')]:
        assert client.get('/realms/fsp/protocol/openid-connect/auth',params=dict(query,**{field:value})).status_code==400


def test_backchannel_cannot_be_used_for_production_issuer(monkeypatch):
    from backend.fsp_identity import provider_url
    from fastapi import HTTPException
    import pytest
    monkeypatch.setenv('FSP_OIDC_BACKCHANNEL_BASE','http://fsp-id-mock:17771')
    monkeypatch.setenv('FSP_OIDC_DEMO_ISSUER','0')
    with pytest.raises(HTTPException):provider_url('https://id.example/realms/fsp/token','https://id.example/realms/fsp')
    monkeypatch.setenv('FSP_OIDC_DEMO_ISSUER','1')
    assert provider_url('http://127.0.0.1:17771/realms/fsp/token','http://127.0.0.1:17771/realms/fsp')=='http://fsp-id-mock:17771/realms/fsp/token'
