"""Vendor-neutral ATS API v1. Any ATS can implement this contract or use an adapter.

Organization-scoped, revocable bearer credentials; atomic idempotent writes;
candidate-selected fields only after acceptance and a separate transfer consent.
"""
import hashlib,hmac,json,secrets,time,base64
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException,Header,Security,Request
from fastapi.security import HTTPBearer,HTTPAuthorizationCredentials
from pydantic import Field,model_validator
from .auth import session_user,require_role,require_verified,require_org_role
from .schemas import StrictModel
from .db import connection,dumps,now,audit,bump
from .trust import ensure_trusted,company
from .recruiting import Need,create_need,need_view,own_invitation,digest
from .privacy import contact_access,consent
from .domain import candidate_from_row

from .ats_diagnostics import ObservedATSRoute
router=APIRouter(prefix='/api',tags=['Universal ATS API v1'],route_class=ObservedATSRoute)
bearer=HTTPBearer(auto_error=False,scheme_name='OrganizationATSKey')
SCOPES={'needs.write','profiles.read','stages.write','events.read'}
TRANSFER_FIELDS={'name','title','contacts','categories','skills','city','experience_years'}

def key_auth(request:Request,credentials:HTTPAuthorizationCredentials|None=Security(bearer)):
    if not credentials or credentials.scheme.lower()!='bearer':raise HTTPException(401,'Требуется ключ ATS')
    hashed=hashlib.sha256(credentials.credentials.encode()).hexdigest()
    with connection() as conn:
        rows=conn.execute("SELECT * FROM ats_connections WHERE provider='universal-v1'")
        for row in rows:
            payload=json.loads(row['payload'])
            if hmac.compare_digest(payload['token_hash'],hashed) and payload['active'] and payload['expires']>time.time():
                if company(conn,row['org_id'])['verification'] not in {'verified','demo_verified'}:raise HTTPException(403,'Организация не подтверждена')
                client=dict(row,payload=payload);request.state.ats_client=client;return client
    raise HTTPException(401,'Ключ недействителен, отозван или истёк')

def scoped(client,scope):
    if scope not in client['payload']['scopes']:raise HTTPException(403,'У ключа нет необходимого разрешения')

def connection_view(row):
    payload=json.loads(row['payload']);payload.pop('token_hash',None)
    return {k:row[k] for k in ['id','org_id','provider','account_id','created_at']}|{'settings':payload}

class ClientCreate(StrictModel):
    name:str=Field(min_length=3,max_length=100);scopes:list[str]=Field(min_length=1,max_length=4)
    valid_days:int=Field(default=90,ge=1,le=365)

@router.post('/ats-connections',status_code=201)
def create_client(body:ClientCreate,user=Depends(session_user)):
    require_org_role(user,'owner');require_verified(user)
    if set(body.scopes)-SCOPES:raise HTTPException(422,'Неизвестное разрешение')
    raw=secrets.token_urlsafe(40);identity='ats_'+secrets.token_hex(10)
    payload={'name':body.name,'owner_id':user['id'],'scopes':list(dict.fromkeys(body.scopes)),'token_hash':hashlib.sha256(raw.encode()).hexdigest(),'active':True,'expires':time.time()+body.valid_days*86400,'contract_version':'ats-v1'}
    with connection() as conn:
        ensure_trusted(conn,user)
        conn.execute('INSERT INTO ats_connections VALUES(?,?,\'universal-v1\',?,\'\',?,?)',(identity,user['org_id'],identity,dumps(payload),now()));audit(conn,user['id'],'ats_key_created',identity,{'scopes':payload['scopes']})
    return {'id':identity,'token':raw,'expires':payload['expires'],'message':'Ключ показывается только один раз; сохраните его в секретах ATS'}

@router.get('/ats-connections')
def connections(user=Depends(session_user)):
    require_role(user,'employer')
    with connection() as conn:return {'items':[connection_view(r) for r in conn.execute('SELECT * FROM ats_connections WHERE org_id=?',(user['org_id'],))],'contract':'/api/openapi.json','scopes':sorted(SCOPES)}

class SyncReport(StrictModel):
    run_id:str=Field(min_length=12,max_length=100)
    provider:str=Field(pattern=r'^[a-z0-9_-]{2,40}$')
    status:str=Field(pattern=r'^(running|completed|failed)$')
    counts:dict[str,int]=Field(default_factory=dict,max_length=8)
    error_code:Literal['network_error','access_denied','invalid_data','remote_error','configuration_error','sync_failed','worker_interrupted']|None=None
    @model_validator(mode='after')
    def bounded(self):
        allowed={'needs','closed','profiles','stages','deleted'}
        if set(self.counts)-allowed or any(v<0 or v>100000 for v in self.counts.values()):raise ValueError('Некорректные счётчики обмена')
        if self.status=='failed' and not self.error_code:raise ValueError('Укажите код сбоя')
        return self

@router.post('/ats/v1/sync-report')
def sync_report(body:SyncReport,client=Depends(key_auth)):
    scoped(client,'events.read')
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT * FROM ats_connections WHERE id=?',(client['id'],)).fetchone()
        settings=json.loads(row['payload']);sync=settings.get('sync',{})
        previous=conn.execute("SELECT * FROM integration_events WHERE connection_id=? AND direction='sync' AND external_key=?",(client['id'],body.run_id)).fetchone()
        if previous and previous['status'] in {'completed','failed'}:
            old=json.loads(previous['payload'])
            if old!=body.model_dump():raise HTTPException(409,'Запуск уже завершён с другим результатом')
            return {'run_id':body.run_id,'status':previous['status'],'connection_id':client['id']}
        if previous and json.loads(previous['payload'])['provider']!=body.provider:raise HTTPException(409,'Система запуска изменилась')
        if body.status!='running' and sync.get('run_id')!=body.run_id:raise HTTPException(409,'Этот запуск уже заменён новым')
        # One bridge run per key; a stalled run expires without blocking recovery.
        if sync.get('status')=='running' and sync.get('run_id')!=body.run_id and sync.get('lease_until',0)>time.time():raise HTTPException(409,'Другой запуск ещё выполняется')
        if body.status=='running':
            if not previous:
                conn.execute("INSERT INTO integration_events(id,connection_id,direction,external_key,payload,status,created_at) VALUES(?,?,'sync',?,?,'running',?)",('event_'+secrets.token_hex(10),client['id'],body.run_id,dumps(body.model_dump()),now()))
            started=sync.get('started_at') if sync.get('run_id')==body.run_id else now()
            sync.update(run_id=body.run_id,provider=body.provider,status='running',started_at=started,lease_until=time.time()+120,error_code=None)
        else:
            if not previous:raise HTTPException(409,'Сначала зарегистрируйте запуск')
            conn.execute('UPDATE integration_events SET payload=?,status=?,error=?,completed_at=?,attempts=attempts+1 WHERE id=?',(dumps(body.model_dump()),body.status,body.error_code,now(),previous['id']))
            sync.update(run_id=body.run_id,provider=body.provider,status=body.status,completed_at=now(),lease_until=0,counts=body.counts,error_code=body.error_code)
            if body.status=='completed':sync['last_success_at']=now()
        settings['sync']=sync;conn.execute('UPDATE ats_connections SET payload=? WHERE id=?',(dumps(settings),client['id']))
    return {'run_id':body.run_id,'status':body.status,'connection_id':client['id']}

@router.post('/ats-connections/{identity}/revoke')
def revoke(identity:str,user=Depends(session_user)):
    require_org_role(user,'owner')
    with connection() as conn:
        row=conn.execute('SELECT * FROM ats_connections WHERE id=? AND org_id=?',(identity,user['org_id'])).fetchone()
        if not row:raise HTTPException(404,'Подключение не найдено')
        payload=json.loads(row['payload']);payload['active']=False
        conn.execute('UPDATE ats_connections SET payload=? WHERE id=?',(dumps(payload),identity));audit(conn,user['id'],'ats_key_revoked',identity)
    return {'ok':True}

@router.post('/ats-connections/{identity}/rotate')
def rotate(identity:str,user=Depends(session_user)):
    require_org_role(user,'owner');require_verified(user)
    raw=secrets.token_urlsafe(40)
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');ensure_trusted(conn,user)
        row=conn.execute("SELECT * FROM ats_connections WHERE id=? AND org_id=? AND provider='universal-v1'",(identity,user['org_id'])).fetchone()
        if not row:raise HTTPException(404,'Подключение не найдено')
        settings=json.loads(row['payload']);settings.update(token_hash=hashlib.sha256(raw.encode()).hexdigest(),active=True,expires=time.time()+90*86400)
        conn.execute('UPDATE ats_connections SET payload=? WHERE id=?',(dumps(settings),identity));audit(conn,user['id'],'ats_key_rotated',identity)
    return {'id':identity,'token':raw,'expires':settings['expires'],'message':'Старый ключ больше не действует. Идентификаторы и история подключения сохранены.'}

class TransferConsent(StrictModel):granted:bool;fields:list[str]=Field(default_factory=lambda:['name','title','contacts','categories'],max_length=7)
@router.post('/invitations/{identity}/ats-consent')
def transfer_consent(identity:str,body:TransferConsent,user=Depends(session_user)):
    require_role(user,'candidate')
    if set(body.fields)-TRANSFER_FIELDS:raise HTTPException(422,'Недопустимые поля передачи')
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');row=own_invitation(conn,identity,user)
        if row['status']!='accepted':raise HTTPException(409,'Сначала примите приглашение')
        payload=json.loads(row['payload'])
        restricted=set(payload.get('ats_consent',{}).get('fields',[]))-set(body.fields)
        if not body.granted or restricted:
            from .integration_privacy import withdrawal_event
            withdrawal_event(conn,row,'ats_consent_withdrawn' if not body.granted else 'ats_fields_restricted')
        payload['ats_consent']={'granted':body.granted,'fields':list(dict.fromkeys(body.fields)),'policy_version':'2026-10-v1','at':now()}
        conn.execute('UPDATE invitations SET payload=?,version=version+1 WHERE id=?',(dumps(payload),identity))
        audit(conn,user['id'],'ats_consent_changed',identity,{'granted':body.granted,'fields':body.fields})
    return {'ok':True,'consent':payload['ats_consent']}

def event(conn,client,key,body,direction='inbound'):
    if not key or not 12<=len(key)<=150:raise HTTPException(422,'Укажите Idempotency-Key длиной 12–150 символов')
    fingerprint=digest(body)
    previous=conn.execute('SELECT * FROM integration_events WHERE connection_id=? AND direction=? AND external_key=?',(client['id'],direction,key)).fetchone()
    if previous:
        payload=json.loads(previous['payload'])
        if payload['request_sha256']!=fingerprint:raise HTTPException(409,'Ключ события уже использован с другими данными')
        return previous['id'],payload.get('response')
    identity='event_'+secrets.token_hex(10)
    conn.execute('INSERT INTO integration_events(id,connection_id,direction,external_key,payload,status,created_at) VALUES(?,?,?,?,?,\'processing\',?)',(identity,client['id'],direction,key,dumps({'request_sha256':fingerprint}),now()))
    return identity,None

def complete_event(conn,identity,response):
    row=conn.execute('SELECT payload FROM integration_events WHERE id=?',(identity,)).fetchone();payload=json.loads(row[0]);payload['response']=response
    conn.execute("UPDATE integration_events SET payload=?,status='completed',completed_at=?,attempts=attempts+1 WHERE id=?",(dumps(payload),now(),identity))

class ExternalNeed(StrictModel):external_id:str=Field(min_length=1,max_length=150);need:Need
@router.post('/ats/v1/needs',status_code=201)
def import_need(body:ExternalNeed,idempotency_key:str=Header(alias='Idempotency-Key'),client=Depends(key_auth)):
    scoped(client,'needs.write')
    from .trust import risk_signals
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');event_id,previous=event(conn,client,'need:'+idempotency_key,body.model_dump())
        if previous:return previous
        link=conn.execute("SELECT * FROM ats_links WHERE connection_id=? AND entity_type='need' AND external_id=?",(client['id'],body.external_id)).fetchone()
        identity=link['local_id'] if link else 'need_'+secrets.token_hex(10)
        payload=body.need.model_dump();signals=risk_signals(body.need.description+' '+body.need.employer_contact)
        payload.update({'currency':'RUB','period':'month','source':'ats','ats_external_id':body.external_id,'ats_connection_id':client['id'],'last_verified_at':now(),'risk_signals':signals,'company':company(conn,client['org_id'])['name']})
        state='pending' if signals else 'active'
        if link:
            current=conn.execute('SELECT state FROM employer_needs WHERE id=? AND org_id=?',(identity,client['org_id'])).fetchone()
            if not current:raise HTTPException(409,'Нарушена связь потребности')
            if current[0]=='blocked':state='blocked'
            conn.execute('UPDATE employer_needs SET payload=?,state=?,expires=?,version=version+1 WHERE id=?',(dumps(payload),state,time.time()+body.need.valid_days*86400,identity))
        else:
            conn.execute('INSERT INTO employer_needs(id,org_id,payload,state,expires,created_at) VALUES(?,?,?,?,?,?)',(identity,client['org_id'],dumps(payload),state,time.time()+body.need.valid_days*86400,now()))
            conn.execute('INSERT INTO ats_links VALUES(?,\'need\',?,?,?)',(client['id'],identity,body.external_id,dumps({'source':'ats-v1'})))
        response={'id':identity,'external_id':body.external_id,'state':state,'event_id':event_id}
        complete_event(conn,event_id,response);audit(conn,client['payload']['owner_id'],'ats_need_imported',identity,{'connection_id':client['id']});bump(conn)
        return response

@router.delete('/ats/v1/needs/{external_id}')
def close_external_need(external_id:str,idempotency_key:str=Header(alias='Idempotency-Key'),client=Depends(key_auth)):
    scoped(client,'needs.write')
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');event_id,previous=event(conn,client,'close:'+idempotency_key,{'external_id':external_id})
        if previous:return previous
        link=conn.execute("SELECT * FROM ats_links WHERE connection_id=? AND entity_type='need' AND external_id=?",(client['id'],external_id)).fetchone()
        if not link:raise HTTPException(404,'Потребность не найдена')
        conn.execute("UPDATE employer_needs SET state='closed',version=version+1 WHERE id=? AND org_id=?",(link['local_id'],client['org_id']))
        response={'id':link['local_id'],'state':'closed'};complete_event(conn,event_id,response);bump(conn)
        return response

@router.get('/ats/v1/profiles')
def accepted_profiles(limit:int=50,offset:int=0,cursor:str|None=None,client=Depends(key_auth)):
    scoped(client,'profiles.read')
    if not 1<=limit<=100 or not 0<=offset<=10000:raise HTTPException(422,'Некорректная пагинация')
    items=[]
    with connection() as conn:
        timestamp,identity=read_cursor(cursor)
        if cursor and offset:raise HTTPException(422,'Используйте cursor или offset отдельно')
        rows=conn.execute("SELECT * FROM invitations WHERE org_id=? AND status='accepted' AND (created_at>? OR (created_at=? AND id>?)) ORDER BY created_at,id LIMIT ? OFFSET ?",(client['org_id'],timestamp,timestamp,identity,limit+1,offset)).fetchall()
        has_more=len(rows)>limit;rows=rows[:limit]
        for row in rows:
            item=transfer_profile(conn,row,client)
            if item:items.append(item)
        audit(conn,client['payload']['owner_id'],'ats_profiles_read',client['id'],{'count':len(items)})
    return {'items':items,'offset':offset,'limit':limit,'next_offset':offset+limit,'next_cursor':make_cursor(rows[-1]) if rows else cursor,'has_more':has_more}

def transfer_profile(conn,row,client):
    permission=json.loads(row['payload']).get('ats_consent',{})
    if not permission.get('granted') or not contact_access(conn,row['candidate_id'],{'org_id':client['org_id']}) or not consent(conn,row['candidate_id'],'processing'):return None
    profile=candidate_from_row(conn.execute('SELECT * FROM candidates WHERE id=?',(row['candidate_id'],)).fetchone(),conn)
    data={key:profile.get(key) for key in permission['fields'] if key in TRANSFER_FIELDS}
    return {'invitation_id':row['id'],'need_id':row['need_id'],'candidate_id':row['candidate_id'],'profile':data,'consent':permission,'schema_version':'ats-profile-v1'}

@router.get('/ats/v1/profiles/{identity}')
def accepted_profile(identity:str,client=Depends(key_auth)):
    scoped(client,'profiles.read')
    with connection() as conn:
        row=conn.execute("SELECT * FROM invitations WHERE id=? AND org_id=? AND status='accepted'",(identity,client['org_id'])).fetchone()
        item=transfer_profile(conn,row,client) if row else None
        if not item:raise HTTPException(404,'Разрешённый профиль недоступен')
        return item

class Stage(StrictModel):invitation_id:str;external_candidate_id:str=Field(min_length=1,max_length=150);stage:str=Field(min_length=2,max_length=100)
@router.post('/ats/v1/stages')
def update_stage(body:Stage,idempotency_key:str=Header(alias='Idempotency-Key'),client=Depends(key_auth)):
    scoped(client,'stages.write')
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');event_id,previous=event(conn,client,'stage:'+idempotency_key,body.model_dump())
        if previous:return previous
        row=conn.execute("SELECT * FROM invitations WHERE id=? AND org_id=? AND status='accepted'",(body.invitation_id,client['org_id'])).fetchone()
        if not row:raise HTTPException(404,'Принятое приглашение не найдено')
        if not json.loads(row['payload']).get('ats_consent',{}).get('granted'):raise HTTPException(403,'Разрешение передачи в ATS отсутствует')
        if not contact_access(conn,row['candidate_id'],{'org_id':client['org_id']}) or not consent(conn,row['candidate_id'],'processing'):raise HTTPException(403,'Разрешение отозвано')
        conn.execute('INSERT INTO recruitment_stages VALUES(?,?,?,?,?) ON CONFLICT(invitation_id) DO UPDATE SET connection_id=excluded.connection_id,external_id=excluded.external_id,stage=excluded.stage,updated_at=excluded.updated_at',(body.invitation_id,client['id'],body.external_candidate_id,body.stage,now()))
        response={'invitation_id':body.invitation_id,'stage':body.stage,'event_id':event_id};complete_event(conn,event_id,response)
        audit(conn,client['payload']['owner_id'],'ats_stage_updated',body.invitation_id,{'stage':body.stage})
        return response

@router.get('/ats/v1/events')
def events(offset:int=0,cursor:str|None=None,client=Depends(key_auth)):
    scoped(client,'events.read')
    if not 0<=offset<=10000:raise HTTPException(422,'Некорректная пагинация')
    with connection() as conn:
        timestamp,identity=read_cursor(cursor)
        if cursor and offset:raise HTTPException(422,'Используйте cursor или offset отдельно')
        rows=conn.execute('SELECT * FROM integration_events WHERE connection_id=? AND (created_at>? OR (created_at=? AND id>?)) ORDER BY created_at,id LIMIT 101 OFFSET ?',(client['id'],timestamp,timestamp,identity,offset)).fetchall()
        has_more=len(rows)>100;rows=rows[:100];items=[]
        for row in rows:
            payload=json.loads(row['payload']);item={k:row[k] for k in ['id','direction','external_key','status','error','created_at','completed_at']}
            if payload.get('event_type')=='consent_revoked':item['withdrawal']={k:payload[k] for k in ['event_type','invitation_id','reason','occurred_at']};item['acknowledgement']=payload.get('acknowledgement')
            items.append(item)
        return {'items':items,'next_offset':offset+100,'next_cursor':make_cursor(rows[-1]) if rows else cursor,'has_more':has_more}

def make_cursor(row):return base64.urlsafe_b64encode(dumps([row['created_at'],row['id']]).encode()).decode().rstrip('=')
def read_cursor(raw):
    if raw is None:return '',''
    try:
        if len(raw)>400:raise ValueError()
        values=json.loads(base64.urlsafe_b64decode(raw+'='*(-len(raw)%4)))
        if not isinstance(values,list) or len(values)!=2 or any(not isinstance(v,str) or len(v)>100 for v in values):raise ValueError()
        return values
    except (ValueError,TypeError):raise HTTPException(422,'Некорректный cursor')

class DeletionAck(StrictModel):deleted:bool;note:str=Field(default='',max_length=500)
@router.post('/ats/v1/events/{identity}/ack')
def deletion_ack(identity:str,body:DeletionAck,client=Depends(key_auth)):
    scoped(client,'events.read');scoped(client,'profiles.read')
    if not body.deleted:raise HTTPException(422,'Подтвердите выполненное удаление/ограничение внешней копии')
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');row=conn.execute("SELECT * FROM integration_events WHERE id=? AND connection_id=? AND direction='outbound'",(identity,client['id'])).fetchone()
        if not row:raise HTTPException(404,'Событие не найдено')
        payload=json.loads(row['payload'])
        if payload.get('event_type')!='consent_revoked':raise HTTPException(422,'Это не событие отзыва')
        if not payload.get('acknowledgement'):
            payload['acknowledgement']={'deleted':True,'note':body.note,'at':now()}
            conn.execute("UPDATE integration_events SET payload=?,status='acknowledged',completed_at=? WHERE id=?",(dumps(payload),now(),identity))
            audit(conn,client['payload']['owner_id'],'ats_deletion_acknowledged',identity)
    return {'ok':True,'event_id':identity,'acknowledgement':payload['acknowledgement']}

@router.get('/invitations/{identity}/recruitment-stage')
def stage_for_user(identity:str,user=Depends(session_user)):
    with connection() as conn:
        own_invitation(conn,identity,user);row=conn.execute('SELECT stage,updated_at FROM recruitment_stages WHERE invitation_id=?',(identity,)).fetchone()
        return dict(row) if row else {'stage':None}
