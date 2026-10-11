"""Isolated protocol reference server, not an official FSP/mail/LLM service.

Run only with an explicit secret and registered client/redirect. Captured email
is available only to the owning test process, never through a public endpoint.
"""
import base64,hashlib,hmac,json,secrets,time
from urllib.parse import parse_qs,urlencode
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI,HTTPException,Request
from fastapi.responses import RedirectResponse

def reference_app(issuer,key,client_id='fsp-reference',redirect_uri=None):
    registered_client=client_id
    app=FastAPI(title='FSP integration protocol simulator')
    app.state.messages={};app.state.codes={};app.state.failed_messages=set();app.state.reject_once=False
    private=rsa.generate_private_key(public_exponent=65537,key_size=2048);public=private.public_key().public_numbers()
    enc=lambda v:base64.urlsafe_b64encode(v.to_bytes((v.bit_length()+7)//8,'big')).decode().rstrip('=')
    jwk={'kty':'RSA','use':'sig','alg':'RS256','kid':'reference-key','n':enc(public.n),'e':enc(public.e)}
    @app.get('/.well-known/openid-configuration')
    def discovery():return {'issuer':issuer,'authorization_endpoint':issuer+'/authorize','token_endpoint':issuer+'/token','jwks_uri':issuer+'/jwks','response_types_supported':['code'],'code_challenge_methods_supported':['S256']}
    @app.get('/jwks')
    def jwks():return {'keys':[jwk]}
    @app.get('/authorize')
    def authorize(client_id:str,redirect_uri:str,state:str,nonce:str,code_challenge:str,code_challenge_method:str,response_type:str):
        if client_id!=registered_client or redirect_uri!=app.state.redirect_uri or code_challenge_method!='S256' or response_type!='code':raise HTTPException(400,'Invalid registered client')
        code=secrets.token_urlsafe(32);app.state.codes[code]={'nonce':nonce,'challenge':code_challenge,'expires':time.time()+60}
        return RedirectResponse(redirect_uri+'?'+urlencode({'state':state,'code':code}))
    @app.post('/token')
    async def token(request:Request):
        form=parse_qs((await request.body()).decode());get=lambda k:form.get(k,[''])[0]
        value=app.state.codes.pop(get('code'),None)
        challenge=base64.urlsafe_b64encode(hashlib.sha256(get('code_verifier').encode()).digest()).decode().rstrip('=')
        if not value or value['expires']<time.time() or get('client_id')!=client_id or get('redirect_uri')!=app.state.redirect_uri or not hmac.compare_digest(value['challenge'],challenge):raise HTTPException(400,'Code or PKCE invalid')
        stamp=int(time.time());claims={'iss':issuer,'aud':client_id,'sub':'reference-subject','iat':stamp,'exp':stamp+120,'nonce':value['nonce'],'fsp_id':'FSP-REFERENCE-001'}
        return {'access_token':'reference-only','token_type':'Bearer','id_token':jwt.encode(claims,private,algorithm='RS256',headers={'kid':'reference-key'})}
    @app.post('/messages')
    async def message(request:Request):
        raw=await request.body();stamp=request.headers.get('X-FSP-Timestamp','');sig=hmac.new(key.encode(),(stamp+'.').encode()+raw,hashlib.sha256).hexdigest()
        if not hmac.compare_digest(request.headers.get('Authorization',''),'Bearer '+key) or not hmac.compare_digest(request.headers.get('X-FSP-Signature',''),sig):raise HTTPException(401,'Signature')
        data=json.loads(raw)
        if data.get('schema')!='fsp-notification-v1' or request.headers.get('Idempotency-Key')!=data['message_id']:raise HTTPException(422,'Contract')
        previous=app.state.messages.get(data['message_id'])
        if previous and previous['payload']!=data:raise HTTPException(409,'Idempotency conflict')
        if app.state.reject_once and data['message_id'] not in app.state.failed_messages:
            app.state.failed_messages.add(data['message_id']);raise HTTPException(503,'Injected temporary failure')
        if not previous:app.state.messages[data['message_id']]={'payload':data,'delivery_id':'reference-'+data['message_id']}
        return {'schema':'fsp-notification-receipt-v1','message_id':data['message_id'],'delivery_id':app.state.messages[data['message_id']]['delivery_id'],'state':'accepted'}
    @app.post('/generate')
    async def generate(request:Request):
        if not hmac.compare_digest(request.headers.get('Authorization',''),'Bearer '+key):raise HTTPException(401,'Key')
        value=await request.json()
        return {'schema':'fsp-generator-response-v1','response':json.dumps({'context':'Сервис получает размеры пакетов от независимых очередей и собирает статистику нагрузки.','values':value['parameters']['values'],'threshold':value['parameters']['threshold'],'operation':value['parameters']['operation']},ensure_ascii=False),'provider':'reference_protocol_simulator','mock':True}
    app.state.redirect_uri=redirect_uri
    return app

def create_app():
    import os
    key=os.environ.get('FSP_REFERENCE_KEY','')
    if len(key)<32:raise RuntimeError('Set FSP_REFERENCE_KEY to an explicit local secret of at least32characters')
    return reference_app(os.environ.get('FSP_REFERENCE_BASE_URL','http://127.0.0.1:17771'),key,os.environ.get('FSP_REFERENCE_CLIENT_ID','fsp-reference'),os.environ.get('FSP_REFERENCE_REDIRECT_URI','http://127.0.0.1:8000/api/fsp/oidc/callback'))
