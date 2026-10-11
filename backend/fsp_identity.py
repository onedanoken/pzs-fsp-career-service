"""Confirmed external identity, revocation and OIDC Authorization Code + PKCE."""
import base64,hashlib,hmac,json,os,secrets,time
from urllib.parse import urlencode,urlparse
import httpx,jwt
from . import db
from fastapi import APIRouter,Depends,HTTPException,Request
from fastapi.responses import RedirectResponse
from pydantic import Field,field_validator
from .auth import session_user,require_role,require_verified
from .db import connection,dumps,now,audit,bump,DEMO
from .schemas import StrictModel

router=APIRouter(prefix='/api',tags=['FSP identity and registry contract'])

@router.get('/admin/fsp/pending')
def pending_requests(user=Depends(session_user)):
    require_role(user,'admin')
    with connection() as conn:return {'items':[dict(r) for r in conn.execute("SELECT e.id,e.candidate_id,e.participant_id,e.created_at,u.name FROM external_identities e JOIN users u ON u.candidate_id=e.candidate_id WHERE e.status='pending' ORDER BY e.created_at")]}
def sha(raw):return hashlib.sha256(raw.encode()).hexdigest()
def public_identity(row):return {k:row[k] for k in ['id','provider','issuer','subject','participant_id','status','method','created_at','revoked_at']}

@router.get('/fsp/identities')
def identities(user=Depends(session_user)):
    require_role(user,'candidate')
    with connection() as conn:return {'items':[public_identity(r) for r in conn.execute('SELECT * FROM external_identities WHERE candidate_id=? ORDER BY created_at DESC',(user['candidate_id'],))],'oidc_available':bool(os.environ.get('FSP_OIDC_ISSUER')),'registry_available':False,'notice':'Контракт реестра проектируется командой; официальный API ФСП не предоставлен'}

class Link(StrictModel):participant_id:str=Field(min_length=1,max_length=100)
@router.post('/fsp/link',status_code=201)
def pending_link(body:Link,user=Depends(session_user)):
    require_role(user,'candidate');require_verified(user)
    identity='identity_'+secrets.token_hex(10)
    with connection() as conn:
        conn.execute('INSERT INTO external_identities VALUES(?,?,\'fsp\',\'fsp:pending\',?,?,\'pending\',\'self_entered\',\'{}\',?,NULL)',(identity,user['candidate_id'],body.participant_id,body.participant_id,now()))
        audit(conn,user['id'],'fsp_link_pending',identity)
    return {'id':identity,'status':'pending','message':'Номер сохранён; достижения появятся после подтверждения владения'}

class ProofRequest(StrictModel):candidate_id:str;participant_id:str=Field(min_length=1,max_length=100);evidence:str=Field(min_length=10,max_length=1500)
@router.post('/admin/fsp/proofs',status_code=201)
def issue_proof(body:ProofRequest,user=Depends(session_user)):
    require_role(user,'admin');require_verified(user)
    raw=secrets.token_urlsafe(32)
    with connection() as conn:
        if not conn.execute("SELECT 1 FROM external_identities WHERE candidate_id=? AND participant_id=? AND status='pending'",(body.candidate_id,body.participant_id)).fetchone():raise HTTPException(409,'Кандидат ещё не запросил эту привязку')
        conn.execute('INSERT INTO identity_proofs VALUES(?,?,?,?,NULL)',(sha(raw),body.candidate_id,body.participant_id,time.time()+900))
        audit(conn,user['id'],'fsp_proof_issued',body.candidate_id,{'participant_id':body.participant_id,'evidence':body.evidence,'demo':DEMO})
    return {'proof':raw,'expires_in':900,'demo':DEMO,'message':'Одноразовое подтверждение передаётся только указанному кандидату'}

class Proof(StrictModel):token:str=Field(min_length=20,max_length=200)
@router.post('/fsp/confirm')
def confirm(body:Proof,user=Depends(session_user)):
    require_role(user,'candidate');require_verified(user)
    try:
        with connection() as conn:
            conn.execute('BEGIN IMMEDIATE');proof=conn.execute('SELECT * FROM identity_proofs WHERE hash=? AND candidate_id=? AND consumed_at IS NULL AND expires>?',(sha(body.token),user['candidate_id'],time.time())).fetchone()
            if not proof:raise HTTPException(400,'Подтверждение недействительно или выдано другому кандидату')
            existing=conn.execute("SELECT * FROM external_identities WHERE candidate_id=? AND participant_id=? AND status='pending' ORDER BY created_at DESC LIMIT 1",(user['candidate_id'],proof['participant_id'])).fetchone()
            if not existing:raise HTTPException(409,'Запрос привязки отсутствует')
            status='demo' if DEMO else 'verified';issuer='fsp:demo-manual' if DEMO else 'fsp:authorized-manual'
            conn.execute('UPDATE external_identities SET issuer=?,status=?,method=\'authorized_one_time_proof\' WHERE id=?',(issuer,status,existing['id']))
            conn.execute('UPDATE identity_proofs SET consumed_at=? WHERE hash=?',(now(),proof['hash']));audit(conn,user['id'],'fsp_identity_confirmed',existing['id'],{'mode':status});bump(conn)
            return public_identity(conn.execute('SELECT * FROM external_identities WHERE id=?',(existing['id'],)).fetchone())
    except db.IntegrityError:raise HTTPException(409,'Этот подтверждённый ID уже связан с другим профилем')

@router.post('/fsp/identities/{identity}/revoke')
def revoke(identity:str,user=Depends(session_user)):
    require_role(user,'candidate')
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');row=conn.execute('SELECT * FROM external_identities WHERE id=? AND candidate_id=?',(identity,user['candidate_id'])).fetchone()
        if not row:raise HTTPException(404,'Привязка не найдена')
        conn.execute("UPDATE external_identities SET status='revoked',revoked_at=? WHERE id=?",(now(),identity))
        for achievement in conn.execute('SELECT * FROM achievements WHERE candidate_id=?',(user['candidate_id'],)).fetchall():
            if json.loads(achievement['payload']).get('identity_id')==identity:conn.execute('UPDATE achievements SET active=0,version=version+1 WHERE id=?',(achievement['id'],))
        audit(conn,user['id'],'fsp_identity_revoked',identity);bump(conn)
    return {'ok':True}

def checked_url(url,issuer):
    parsed=urlparse(url);trusted=urlparse(issuer)
    if parsed.netloc!=trusted.netloc or parsed.scheme!=trusted.scheme or parsed.username or parsed.password or (parsed.scheme!='https' and not ((DEMO or os.environ.get('FSP_OIDC_DEMO_ISSUER')=='1') and parsed.hostname in {'127.0.0.1','localhost'})):raise HTTPException(503,'Недопустимая конфигурация OIDC')
    return url

def provider_url(url,issuer):
    checked_url(url,issuer)
    base=os.environ.get('FSP_OIDC_BACKCHANNEL_BASE','').rstrip('/')
    if not base:return url
    parsed=urlparse(base)
    if os.environ.get('FSP_OIDC_DEMO_ISSUER')!='1' or parsed.scheme!='http' or parsed.hostname not in {'fsp-id-mock','localhost','127.0.0.1'} or parsed.username or parsed.password or parsed.path not in {'','/'}:
        raise HTTPException(503,'Backchannel разрешён только для изолированного OIDC-мока')
    external=urlparse(issuer)
    return base+url[len(external.scheme+'://'+external.netloc):]

def metadata():
    issuer=os.environ.get('FSP_OIDC_ISSUER','').rstrip('/')
    if not issuer:raise HTTPException(409,'Провайдер ФСП ID пока не настроен')
    checked_url(issuer,issuer)
    try:
        with httpx.Client(timeout=10,trust_env=False,follow_redirects=False) as client:
            response=client.get(provider_url(issuer+'/.well-known/openid-configuration',issuer));response.raise_for_status();data=response.json()
        if data['issuer']!=issuer:raise ValueError('issuer mismatch')
        for field in ['authorization_endpoint','token_endpoint','jwks_uri']:checked_url(data[field],issuer)
        return data
    except (httpx.HTTPError,KeyError,ValueError):raise HTTPException(503,'Провайдер идентификации недоступен')

@router.get('/fsp/oidc/start')
def oidc_start(user=Depends(session_user)):
    require_role(user,'candidate');require_verified(user);provider=metadata()
    raw=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(64);nonce=secrets.token_urlsafe(32)
    redirect=os.environ.get('FSP_PUBLIC_URL','http://localhost:8000').rstrip('/')+'/api/fsp/oidc/callback'
    payload={'verifier':verifier,'nonce':nonce,'redirect_uri':redirect,'issuer':provider['issuer']}
    with connection() as conn:conn.execute('INSERT INTO oidc_states VALUES(?,?,?, ?,NULL)',(sha(raw),user['id'],dumps(payload),time.time()+600))
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    query={'client_id':os.environ['FSP_OIDC_CLIENT_ID'],'redirect_uri':redirect,'response_type':'code','scope':'openid profile','state':raw,'nonce':nonce,'code_challenge':challenge,'code_challenge_method':'S256'}
    return {'url':provider['authorization_endpoint']+'?'+urlencode(query)}

def validate_id_token(token,provider,nonce,jwks):
    header=jwt.get_unverified_header(token)
    if header.get('alg')!='RS256':raise ValueError('algorithm')
    candidates=[key for key in jwks['keys'] if key.get('kid')==header.get('kid') and key.get('kty')=='RSA']
    if len(candidates)!=1:raise ValueError('key')
    key=jwt.PyJWK.from_dict(candidates[0]).key
    claims=jwt.decode(token,key,algorithms=['RS256'],audience=os.environ['FSP_OIDC_CLIENT_ID'],issuer=provider['issuer'],options={'require':['exp','iat','sub','iss','aud']},leeway=15)
    if not hmac.compare_digest(str(claims.get('nonce','')),nonce):raise ValueError('nonce')
    if isinstance(claims['aud'],list) and len(claims['aud'])>1 and claims.get('azp')!=os.environ['FSP_OIDC_CLIENT_ID']:raise ValueError('authorized party')
    return claims

@router.get('/fsp/oidc/callback')
def callback(state:str,code:str,user=Depends(session_user)):
    require_role(user,'candidate');provider=metadata()
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');row=conn.execute('SELECT * FROM oidc_states WHERE hash=? AND user_id=? AND consumed_at IS NULL AND expires>?',(sha(state),user['id'],time.time())).fetchone()
        if not row:raise HTTPException(400,'Состояние входа недействительно')
        payload=json.loads(row['payload'])
        if payload['issuer']!=provider['issuer']:raise HTTPException(400,'Провайдер изменился')
        conn.execute('UPDATE oidc_states SET consumed_at=? WHERE hash=?',(now(),row['hash']))
    request={'grant_type':'authorization_code','code':code,'client_id':os.environ['FSP_OIDC_CLIENT_ID'],'redirect_uri':payload['redirect_uri'],'code_verifier':payload['verifier']}
    if os.environ.get('FSP_OIDC_CLIENT_SECRET'):request['client_secret']=os.environ['FSP_OIDC_CLIENT_SECRET']
    try:
        with httpx.Client(timeout=15,trust_env=False,follow_redirects=False) as client:
            response=client.post(provider_url(provider['token_endpoint'],provider['issuer']),data=request);response.raise_for_status();tokens=response.json()
            response=client.get(provider_url(provider['jwks_uri'],provider['issuer']));response.raise_for_status();keys=response.json()
        claims=validate_id_token(tokens['id_token'],provider,payload['nonce'],keys)
        participant=claims.get(os.environ.get('FSP_OIDC_PARTICIPANT_CLAIM','participant_id'))
        if not isinstance(participant,str) or not 1<=len(participant)<=100:raise ValueError('participant claim missing')
        with connection() as conn:
            identity='identity_'+secrets.token_hex(10);status='demo' if os.environ.get('FSP_OIDC_DEMO_ISSUER','0')=='1' or provider.get('fsp_mock') or claims.get('fsp_mock') else 'verified'
            conn.execute('INSERT INTO external_identities VALUES(?,?,\'fsp\',?,?,?,?,\'oidc_pkce\',\'{}\',?,NULL)',(identity,user['candidate_id'],provider['issuer'],claims['sub'],participant,status,now()))
            audit(conn,user['id'],'fsp_oidc_linked',identity,{'issuer':provider['issuer'],'mode':status});bump(conn)
    except db.IntegrityError:raise HTTPException(409,'Внешний аккаунт уже связан с профилем')
    except (httpx.HTTPError,KeyError,ValueError,jwt.PyJWTError):raise HTTPException(400,'Не удалось подтвердить аккаунт у провайдера')
    return RedirectResponse('/candidate/fsp?linked=1',status_code=303)

class Achievement(StrictModel):
    external_id:str=Field(min_length=1,max_length=150);identity_id:str
    title:str=Field(min_length=3,max_length=200);contest:str=Field(min_length=3,max_length=200)
    date:str=Field(pattern=r'^\d{4}-\d{2}-\d{2}$');result:str=Field(max_length=200)
    role:str|None=None;role_label:str|None=Field(default=None,max_length=100)
    individual_evidence:bool=False;task_skills:list[str]=Field(default_factory=list,max_length=25)
    sports_rank:str|None=Field(default=None,max_length=100);source_url:str=Field(max_length=500)
    @field_validator('source_url')
    @classmethod
    def source_http(cls,value):
        p=urlparse(value)
        if p.scheme not in {'https','http'} or not p.hostname or p.username or p.password:raise ValueError('Нужна безопасная ссылка HTTP/HTTPS')
        return value
    @field_validator('date')
    @classmethod
    def real_date(cls,value):
        from datetime import date
        date.fromisoformat(value);return value

class RegistryEvent(StrictModel):
    schema_version:str='fsp-registry-entry-v1'
    event_id:str=Field(min_length=8,max_length=100)
    issuer:str=Field(min_length=3,max_length=500)
    participant_id:str=Field(min_length=1,max_length=100)
    achievement:dict

def import_record(conn,body,user):
    from .taxonomy import SKILLS
    if any(x not in SKILLS for x in body.task_skills):raise HTTPException(422,'Неизвестный навык')
    identity=conn.execute("SELECT * FROM external_identities WHERE id=? AND status IN ('verified','demo')",(body.identity_id,)).fetchone()
    if not identity:raise HTTPException(409,'Сначала подтвердите внешнюю идентичность')
    key='fsp_'+sha(identity['issuer']+':'+body.external_id)[:24]
    payload=body.model_dump();payload.update({'id':key,'candidate_id':identity['candidate_id'],'source':'proposed_fsp_registry','source_id':body.external_id,'issuer':identity['issuer'],'demo':identity['status']=='demo','imported_at':now(),'note':'Запись по контракту команды; источник указан явно'})
    existing=conn.execute('SELECT candidate_id FROM achievements WHERE id=?',(key,)).fetchone()
    if existing and existing[0]!=identity['candidate_id']:raise HTTPException(409,'Запись уже относится к другому участнику')
    conn.execute('INSERT INTO achievements(id,candidate_id,payload) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,active=1,version=achievements.version+1',(key,identity['candidate_id'],dumps(payload)));bump(conn)
    audit(conn,user.get('id'),'fsp_registry_import',key)
    return {'id':key,'demo':payload['demo']}

@router.post('/integration-events/fsp-achievements')
async def registry_event(request:Request):
    from .delivery_gateway import signed_body
    raw=await signed_body(request,'FSP_REGISTRY_API_KEY')
    try:body=RegistryEvent.model_validate_json(raw)
    except ValueError:raise HTTPException(422,'Некорректная запись реестра')
    if body.schema_version!='fsp-registry-entry-v1' or body.issuer!=os.environ.get('FSP_REGISTRY_ISSUER') or 'identity_id' in body.achievement:raise HTTPException(403,'Источник или идентичность не разрешены')
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');identities=conn.execute("SELECT id FROM external_identities WHERE issuer=? AND participant_id=? AND status IN ('verified','demo')",(body.issuer,body.participant_id)).fetchall()
        if len(identities)!=1:raise HTTPException(409,'Нет однозначной подтверждённой связи')
        try:record=Achievement.model_validate(dict(body.achievement,identity_id=identities[0]['id']))
        except ValueError:raise HTTPException(422,'Некорректное достижение')
        event_key='fsp_registry_event:'+sha(body.event_id);digest=hashlib.sha256(raw).hexdigest();existing=conn.execute('SELECT value FROM meta WHERE key=?',(event_key,)).fetchone()
        if existing:
            value=json.loads(existing[0])
            if value['digest']!=digest:raise HTTPException(409,'Повтор ID с другим содержимым')
            return dict(value['result'],duplicate=True)
        result=import_record(conn,record,{'id':None});conn.execute('INSERT INTO meta VALUES(?,?)',(event_key,dumps({'digest':digest,'result':result})))
    return dict(result,duplicate=False)

@router.post('/admin/fsp/registry/achievements',status_code=201)
def import_achievement(body:Achievement,user=Depends(session_user)):
    require_role(user,'admin')
    with connection() as conn:return import_record(conn,body,user)
