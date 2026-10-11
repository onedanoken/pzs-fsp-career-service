"""Provider-neutral notification transport; no browser-supplied destinations."""
import hashlib,hmac,json,os,time
from urllib.parse import urlparse
import httpx
from fastapi import APIRouter,Depends,HTTPException,Request
from pydantic import Field
from .auth import session_user,require_role
from .db import connection,dumps,now,DEMO,audit
from .jobs import JobError
from .schemas import StrictModel
router=APIRouter(prefix='/api',tags=['Universal integration contracts'])

def signature(key,stamp,body):return hmac.new(key.encode(),(stamp+'.').encode()+body,hashlib.sha256).hexdigest()

def checked_endpoint(url):
    p=urlparse(url)
    if not p.hostname or p.username or p.password or p.query or p.fragment:raise ValueError('Invalid endpoint')
    if p.scheme!='https' and not (DEMO and p.scheme=='http' and p.hostname in {'127.0.0.1','localhost'}):raise ValueError('HTTPS required')
    return url.rstrip('/')

async def signed_body(request,key_env):
    key=os.environ.get(key_env,'');stamp=request.headers.get('X-FSP-Timestamp','');chunks=[];size=0
    async for chunk in request.stream():
        size+=len(chunk)
        if size>8192:raise HTTPException(413,'Слишком большое событие')
        chunks.append(chunk)
    raw=b''.join(chunks)
    try:valid=len(key)>=32 and abs(time.time()-int(stamp))<=300 and hmac.compare_digest(signature(key,stamp,raw),request.headers.get('X-FSP-Signature',''))
    except (ValueError,OverflowError):valid=False
    if not valid:raise HTTPException(401,'Событие не подтверждено')
    return raw

def send(context,payload):
    key=os.environ.get('FSP_DELIVERY_API_KEY','');url=os.environ.get('FSP_DELIVERY_API_URL','')
    if len(key)<32:raise JobError('delivery_not_configured','Для провайдера нужен отдельный ключ не короче 32 символов')
    try:url=checked_endpoint(url)
    except ValueError:raise JobError('delivery_configuration','Недопустимая конфигурация провайдера')
    envelope={'schema':'fsp-notification-v1','message_id':context.id,'channel':'email','recipient':payload['to'],'subject':payload['subject'],'text':payload['text']}
    body=json.dumps(envelope,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode();stamp=str(int(time.time()))
    headers={'Content-Type':'application/json','Authorization':'Bearer '+key,'Idempotency-Key':context.id,'X-FSP-Timestamp':stamp,'X-FSP-Signature':signature(key,stamp,body)}
    try:
        with httpx.Client(timeout=15,trust_env=False,follow_redirects=False) as client:r=client.post(url+'/messages',content=body,headers=headers)
        if r.status_code==429 or r.status_code>=500:raise JobError('delivery_temporary','Провайдер временно недоступен',True)
        if r.status_code not in [200,201,202]:raise JobError('delivery_rejected','Провайдер отклонил сообщение')
        value=r.json()
        if set(value)!={'schema','message_id','delivery_id','state'} or value['schema']!='fsp-notification-receipt-v1' or value['message_id']!=context.id or value['state'] not in ['accepted','delivered'] or not isinstance(value['delivery_id'],str) or not 1<=len(value['delivery_id'])<=100:raise ValueError('Receipt mismatch')
    except (httpx.HTTPError,ValueError,TypeError):raise JobError('delivery_invalid_response','Ответ провайдера не подтверждён; запрос можно повторить',True)
    return {'transport':'universal_api','message_id':context.id,'delivery_id':value['delivery_id'],'state':value['state'],'demo':DEMO}

class Receipt(StrictModel):
    schema_version:str='fsp-notification-event-v1'
    event_id:str=Field(min_length=8,max_length=100)
    message_id:str=Field(min_length=5,max_length=100)
    delivery_id:str=Field(min_length=1,max_length=100)
    state:str

@router.post('/integration-events/notifications')
async def receipt(request:Request):
    raw=await signed_body(request,'FSP_DELIVERY_API_KEY')
    try:body=Receipt.model_validate_json(raw)
    except ValueError:raise HTTPException(422,'Некорректное событие')
    if body.schema_version!='fsp-notification-event-v1' or body.state not in ['accepted','delivered','bounced','failed']:raise HTTPException(422,'Неизвестное состояние')
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');row=conn.execute("SELECT * FROM jobs WHERE id=? AND kind='mail.send'",(body.message_id,)).fetchone()
        if not row:raise HTTPException(404,'Сообщение не найдено')
        result=json.loads(row['result']) if row['result'] else {}
        if result.get('delivery_id')!=body.delivery_id:raise HTTPException(409,'Передача ещё не подтверждена или ID не совпадает')
        event_key='delivery_event:'+hashlib.sha256(body.event_id.encode()).hexdigest();digest=hashlib.sha256(raw).hexdigest()
        existing=conn.execute('SELECT value FROM meta WHERE key=?',(event_key,)).fetchone()
        if existing:
            if existing[0]!=digest:raise HTTPException(409,'Повтор ID с другим содержимым')
            return {'ok':True,'duplicate':True}
        status_key='delivery_status:'+body.message_id
        previous=conn.execute('SELECT value FROM meta WHERE key=?',(status_key,)).fetchone()
        if previous and json.loads(previous[0])['state']=='delivered' and body.state!='delivered':raise HTTPException(409,'Нельзя отменить подтверждённую доставку устаревшим событием')
        conn.execute('INSERT INTO meta VALUES(?,?)',(event_key,digest))
        conn.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(status_key,dumps({'state':body.state,'updated_at':now(),'delivery_id':body.delivery_id})))
        audit(conn,None,'notification_delivery_receipt',body.message_id,{'state':body.state})
    return {'ok':True,'duplicate':False}

@router.get('/integration-contracts')
def contracts():
    from .neural_provider import Variant
    from .fsp_identity import RegistryEvent
    return {'version':'fsp-integration-contracts-v1','notifications':{'request_schema':'fsp-notification-v1','send_path':'POST {configured_url}/messages','authentication':'Bearer + HMAC-SHA256(timestamp.body)','idempotency':'message_id; repeat must return the same delivery_id','receipt_schema':'fsp-notification-receipt-v1','callback':'POST /api/integration-events/notifications','event_schema':Receipt.model_json_schema(),'states':['accepted','delivered','bounced','failed']},'identity':{'protocol':'OIDC Authorization Code + PKCE S256','metadata':'/.well-known/openid-configuration','algorithms':['RS256'],'participant_claim':'operator-configurable','registry_import':'POST /api/admin/fsp/registry/achievements','registry_event':'POST /api/integration-events/fsp-achievements','registry_event_schema':RegistryEvent.model_json_schema(),'scope':'Proposed team contract; official provider access not implied'},'generator':{'schema':Variant.model_json_schema(),'scope':'Anonymous bounded conditions, keys computed by application','protocols':['ollama','llamacpp','fsp-json-v1']},'ats':{'version':'fsp-ats-v1','documentation':'/api/openapi.json','scope':'Contract and explicit adapters; arbitrary vendor adaptation is not automatic'}}

@router.get('/admin/integration-status')
def status(user=Depends(session_user)):
    require_role(user,'admin')
    with connection() as conn:
        counts={r['status']:r['n'] for r in conn.execute("SELECT status,COUNT(*) AS n FROM jobs WHERE kind='mail.send' GROUP BY status")}
        receipts=conn.execute("SELECT COUNT(*) FROM meta WHERE key LIKE 'delivery_status:%'").fetchone()[0]
    return {'notification_api_configured':bool(os.environ.get('FSP_DELIVERY_API_URL')) and len(os.environ.get('FSP_DELIVERY_API_KEY',''))>=32,'smtp_configured':bool(os.environ.get('FSP_SMTP_HOST')),'oidc_configured':bool(os.environ.get('FSP_OIDC_ISSUER')),'mode':'demonstration' if DEMO else 'operational','mail_job_counts':counts,'notification_receipts':receipts,'notice':'Настроенный контракт не означает доступ к официальной системе или доставку в реальный почтовый ящик.'}
