"""Opt-in isolated FSP ID simulator. Never an official identity provider."""
import base64
import hashlib
import hmac
import html
import os
import re
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlparse

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse


def mock_app(issuer, password, redirect_uri, client_id='fsp-career-mock'):
    if len(password) < 12:
        raise ValueError('Set an explicit mock account password of at least 12 characters')
    destination=urlparse(redirect_uri)
    if destination.scheme not in {'http','https'} or not re.fullmatch(r'[A-Za-z0-9.:_-]+',destination.netloc):raise ValueError('Invalid registered redirect origin')
    callback_origin=destination.scheme+'://'+destination.netloc
    app = FastAPI(title='Тестовый ФСП ID', docs_url=None, redoc_url=None)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = key.public_key().public_numbers()
    encode = lambda n: base64.urlsafe_b64encode(n.to_bytes((n.bit_length()+7)//8, 'big')).decode().rstrip('=')
    kid = secrets.token_hex(8)
    jwk = dict(kty='RSA', use='sig', alg='RS256', kid=kid, n=encode(numbers.n), e=encode(numbers.e))
    pending, codes = {}, {}
    prefix = '/realms/fsp'

    def prune():
        for store in (pending, codes):
            for identity in list(store):
                if store[identity]['expires'] < time.time():
                    del store[identity]
        if len(pending) + len(codes) >= 1000:
            raise HTTPException(429, 'Слишком много тестовых запросов')

    @app.get(prefix+'/.well-known/openid-configuration')
    def discovery():
        return dict(issuer=issuer, authorization_endpoint=issuer+'/protocol/openid-connect/auth',
                    token_endpoint=issuer+'/protocol/openid-connect/token',
                    jwks_uri=issuer+'/protocol/openid-connect/certs',
                    response_types_supported=['code'], grant_types_supported=['authorization_code'],
                    subject_types_supported=['public'], id_token_signing_alg_values_supported=['RS256'],
                    code_challenge_methods_supported=['S256'], fsp_mock=True)

    @app.get(prefix+'/protocol/openid-connect/certs')
    def certs():
        return {'keys': [jwk]}

    @app.get(prefix+'/protocol/openid-connect/auth')
    def authorize(client_id: str, redirect_uri: str, state: str, nonce: str,
                  code_challenge: str, code_challenge_method: str, response_type: str,
                  scope: str = 'openid profile'):
        prune()
        if (client_id != registered_client or redirect_uri != registered_redirect or
                response_type != 'code' or code_challenge_method != 'S256' or
                not re.fullmatch(r'[A-Za-z0-9_-]{43}', code_challenge) or
                not 20 <= len(state) <= 200 or not 20 <= len(nonce) <= 200 or
                'openid' not in scope.split()):
            raise HTTPException(400, 'Незарегистрированный клиент или неверный запрос')
        request_id = secrets.token_urlsafe(32)
        pending[request_id] = dict(state=state, nonce=nonce, challenge=code_challenge,
                                   expires=time.time()+300, failures=0)
        return HTMLResponse('<!doctype html><html lang="ru"><meta charset="utf-8">'
                            '<meta name="viewport" content="width=device-width,initial-scale=1">'
                            '<title>Тестовый ФСП ID</title><main><h1>Тестовый ФСП ID</h1>'
                            '<p>Изолированная имитация. Эти аккаунты не принадлежат Федерации.</p>'
                            '<p>Логины: fsp-test-001, fsp-test-002. Пароль задаёт оператор стенда.</p>'
                            '<form method="post" action="'+prefix+'/login">'
                            '<input type="hidden" name="request_id" value="'+html.escape(request_id)+'">'
                            '<label>Тестовый логин <input name="username" required autocomplete="username"></label><br>'
                            '<label>Пароль <input name="password" type="password" required autocomplete="current-password"></label><br>'
                            '<button>Подтвердить тестовую привязку</button></form></main></html>',
                            headers={'Cache-Control':'no-store', 'Content-Security-Policy':"default-src 'none'; form-action 'self' "+callback_origin+"; base-uri 'none'; frame-ancestors 'none'"})

    @app.post(prefix+'/login')
    async def login(request: Request):
        raw = await request.body()
        if len(raw) > 4096:
            raise HTTPException(413, 'Слишком большой запрос')
        form = parse_qs(raw.decode('utf-8'), max_num_fields=5)
        get = lambda k: form.get(k, [''])[0]
        value = pending.get(get('request_id'))
        if not value or value['expires'] < time.time():
            raise HTTPException(400, 'Запрос привязки истёк')
        if get('username') not in ('fsp-test-001', 'fsp-test-002') or not hmac.compare_digest(get('password'), password):
            value['failures'] += 1
            if value['failures'] >= 5:
                pending.pop(get('request_id'), None)
            raise HTTPException(401, 'Неверный тестовый логин или пароль')
        pending.pop(get('request_id'))
        code = secrets.token_urlsafe(32)
        codes[hashlib.sha256(code.encode()).hexdigest()] = dict(value, subject=get('username'), expires=time.time()+60)
        return RedirectResponse(registered_redirect+'?'+urlencode(dict(state=value['state'], code=code)), status_code=303)

    @app.post(prefix+'/protocol/openid-connect/token')
    async def token(request: Request):
        raw = await request.body()
        if len(raw) > 4096:
            raise HTTPException(413, 'Слишком большой запрос')
        form = parse_qs(raw.decode('utf-8'), max_num_fields=8)
        get = lambda k: form.get(k, [''])[0]
        value = codes.pop(hashlib.sha256(get('code').encode()).hexdigest(), None)
        verifier = get('code_verifier')
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        if (not value or value['expires'] < time.time() or get('grant_type') != 'authorization_code' or
                get('client_id') != registered_client or get('redirect_uri') != registered_redirect or
                not re.fullmatch(r'[A-Za-z0-9._~-]{43,128}', verifier) or
                not hmac.compare_digest(value['challenge'], challenge)):
            raise HTTPException(400, 'Код или PKCE недействителен')
        stamp = int(time.time())
        claims = dict(iss=issuer, aud=registered_client, sub=value['subject'], iat=stamp, exp=stamp+120,
                      nonce=value['nonce'], participant_id='MOCK-'+value['subject'].upper(), fsp_mock=True)
        return {'access_token':secrets.token_urlsafe(32), 'scope':'openid profile',
                'id_token': jwt.encode(claims, key, algorithm='RS256', headers={'kid':kid}),
                'token_type':'Bearer', 'expires_in':120, 'mock':True}

    registered_client, registered_redirect = client_id, redirect_uri
    return app


def create_app():
    return mock_app(os.environ.get('FSP_MOCK_ISSUER','http://127.0.0.1:17771/realms/fsp'),
                    os.environ.get('FSP_MOCK_ACCOUNT_PASSWORD',''),
                    os.environ.get('FSP_MOCK_REDIRECT_URI','http://localhost:8000/api/fsp/oidc/callback'))
