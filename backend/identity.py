"""Email verification, single-use reset tokens and durable transactional mail."""
import hashlib,json,os,secrets,smtplib,ssl,time
from email.message import EmailMessage
from email.utils import formatdate,make_msgid
from pathlib import Path
from fastapi import APIRouter,Depends,HTTPException,Request
from pydantic import Field
from .auth import session_user,public_user,require_role,check_password
from .schemas import StrictModel
from .db import connection,dumps,now,audit,password_hash,DEMO,ROOT
from .jobs import enqueue,JobError

router=APIRouter(prefix='/api',tags=['Identity and consent'])
def hash_token(token):return hashlib.sha256(token.encode()).hexdigest()

def issue_token(conn,user,kind):
    raw=secrets.token_urlsafe(32)
    conn.execute('UPDATE auth_tokens SET consumed_at=? WHERE user_id=? AND kind=? AND consumed_at IS NULL',(now(),user['id'],kind))
    conn.execute('INSERT INTO auth_tokens VALUES(?,?,?,?,NULL)',(hash_token(raw),user['id'],kind,time.time()+(3600 if kind=='verify_email' else 900)))
    base=os.environ.get('FSP_PUBLIC_URL','http://localhost:8000').rstrip('/')
    fragment='verify' if kind=='verify_email' else 'reset'
    enqueue('mail.send',{'to':user['email'],'subject':'ФСП Карьера: подтверждение почты' if kind=='verify_email' else 'ФСП Карьера: восстановление доступа','text':f'{base}/#{fragment}={raw}\nСсылка одноразовая. Если вы не запрашивали действие, проигнорируйте письмо.'},user['id'],conn=conn)

def send_mail(context,payload):
    if os.environ.get('FSP_DELIVERY_API_URL'):
        from .delivery_gateway import send
        return send(context,payload)
    host=os.environ.get('FSP_SMTP_HOST')
    if not host:
        if not DEMO:raise JobError('smtp_not_configured','Отправка почты не настроена')
        # Local outbox is never mounted by the web server or returned in public APIs.
        directory=ROOT/'data'/'outbox';directory.mkdir(parents=True,exist_ok=True)
        (directory/(context.id+'.json')).write_text(dumps(payload),encoding='utf-8')
        return {'transport':'local_demo_outbox'}
    message=EmailMessage();message['Date']=formatdate(usegmt=True);message['Message-ID']=make_msgid(domain=os.environ['FSP_SMTP_FROM'].split('@')[-1]);message['To']=payload['to'];message['From']=os.environ['FSP_SMTP_FROM'];message['Subject']=payload['subject'];message.set_content(payload['text'])
    try:
        implicit_tls=os.environ.get('FSP_SMTP_SSL','0')=='1'
        transport=smtplib.SMTP_SSL if implicit_tls else smtplib.SMTP
        options={'timeout':15}
        if implicit_tls:options['context']=ssl.create_default_context()
        with transport(host,int(os.environ.get('FSP_SMTP_PORT','465' if implicit_tls else '587')),**options) as smtp:
            if implicit_tls:pass
            elif os.environ.get('FSP_SMTP_STARTTLS','1')=='1':smtp.starttls(context=ssl.create_default_context())
            elif host not in {'fsp-mail-relay','localhost','127.0.0.1'}:raise JobError('smtp_tls_required','Для внешнего SMTP требуется TLS')
            if os.environ.get('FSP_SMTP_USER'):smtp.login(os.environ['FSP_SMTP_USER'],os.environ['FSP_SMTP_PASSWORD'])
            smtp.send_message(message)
    except (OSError,smtplib.SMTPException):raise JobError('mail_unavailable','Не удалось отправить письмо',True)
    return {'transport':'smtp'}

class TokenBody(StrictModel):token:str=Field(min_length=20,max_length=200)
class ResetRequest(StrictModel):email:str=Field(max_length=160)
class ResetBody(TokenBody):password:str=Field(min_length=8,max_length=200)

def consume(conn,raw,kind):
    row=conn.execute('SELECT * FROM auth_tokens WHERE hash=? AND kind=? AND consumed_at IS NULL AND expires>?',(hash_token(raw),kind,time.time())).fetchone()
    if not row:raise HTTPException(400,'Ссылка недействительна или истекла')
    conn.execute('UPDATE auth_tokens SET consumed_at=? WHERE hash=?',(now(),row['hash']))
    return row['user_id']

@router.post('/auth/verify')
def verify(body:TokenBody):
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');identity=consume(conn,body.token,'verify_email')
        conn.execute('UPDATE users SET email_verified=1 WHERE id=?',(identity,));audit(conn,identity,'email_verified',identity)
    return {'ok':True}

@router.post('/auth/resend')
def resend(user=Depends(session_user)):
    with connection() as conn:
        last=conn.execute("SELECT expires FROM auth_tokens WHERE user_id=? AND kind='verify_email' ORDER BY expires DESC LIMIT 1",(user['id'],)).fetchone()
        if last and last[0]-3600>time.time()-60:raise HTTPException(429,'Повторная отправка доступна через минуту')
        if not user['email_verified']:issue_token(conn,user,'verify_email')
    return {'ok':True}

@router.post('/auth/reset-request')
def reset_request(body:ResetRequest,request:Request):
    from .main import rate_limit
    rate_limit(request)
    with connection() as conn:
        row=conn.execute('SELECT * FROM users WHERE email=?',(body.email.lower(),)).fetchone()
        if row:issue_token(conn,dict(row),'reset_password')
    return {'ok':True,'message':'Если аккаунт существует, письмо будет отправлено'}

@router.post('/auth/reset')
def reset(body:ResetBody):
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');identity=consume(conn,body.token,'reset_password')
        account=conn.execute('SELECT auth_kind FROM users WHERE id=?',(identity,)).fetchone()
        if account['auth_kind']=='email' and len(body.password)<12:raise HTTPException(422,'Пароль должен содержать не менее 12 символов')
        conn.execute('UPDATE users SET password=? WHERE id=?',(password_hash(body.password),identity))
        conn.execute('DELETE FROM sessions WHERE user_id=?',(identity,));audit(conn,identity,'password_reset',identity)
    return {'ok':True}

@router.get('/notifications')
def notifications(user=Depends(session_user)):
    with connection() as conn:return {'items':[dict(r,payload=json.loads(r['payload'])) for r in conn.execute('SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 100',(user['id'],))]}

def notify(conn,user_id,kind,entity_id,text):
    conn.execute('INSERT INTO notifications VALUES(?,?,?,?,?,NULL,?)',('n_'+secrets.token_hex(10),user_id,kind,entity_id,dumps({'text':text}),now()))
    user=conn.execute('SELECT email FROM users WHERE id=?',(user_id,)).fetchone()
    settings=conn.execute('SELECT value FROM meta WHERE key=?',('notification_settings:'+user_id,)).fetchone()
    if user[0] and not user[0].endswith('@accounts.invalid') and (not settings or json.loads(settings[0]).get('email',True)):enqueue('mail.send',{'to':user[0],'subject':'ФСП Карьера: новое событие','text':text+'\nОткройте свой кабинет.'},user_id,conn=conn)

class NotificationSettings(StrictModel):email:bool
@router.get('/notifications/settings')
def notification_settings(user=Depends(session_user)):
    with connection() as conn:
        row=conn.execute('SELECT value FROM meta WHERE key=?',('notification_settings:'+user['id'],)).fetchone()
        return json.loads(row[0]) if row else {'email':True}
@router.put('/notifications/settings')
def change_notification_settings(body:NotificationSettings,user=Depends(session_user)):
    with connection() as conn:
        conn.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('notification_settings:'+user['id'],dumps(body.model_dump())))
        audit(conn,user['id'],'notification_settings_changed',user['id'],body.model_dump())
    return body.model_dump()

@router.get('/consents')
def consents(user=Depends(session_user)):
    require_role(user,'candidate')
    with connection() as conn:return {'policy_version':'2026-10-v1','events':[dict(r) for r in conn.execute('SELECT * FROM consent_events WHERE candidate_id=? ORDER BY created_at DESC',(user['candidate_id'],))]}

class ConsentBody(StrictModel):kind:str;granted:bool
@router.post('/consents')
def change_consent(body:ConsentBody,user=Depends(session_user)):
    require_role(user,'candidate')
    from .privacy import set_consent
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');set_consent(user['candidate_id'],body.kind,body.granted,user,conn)
    return {'ok':True}

@router.post('/contacts/revoke')
def revoke_contacts(user=Depends(session_user)):
    require_role(user,'candidate')
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        from .integration_privacy import revoke_transfers
        revoke_transfers(conn,user['candidate_id'])
        conn.execute('UPDATE contact_grants SET active=0,revoked_at=? WHERE candidate_id=?',(now(),user['candidate_id']))
        audit(conn,user['id'],'contacts_revoked',user['candidate_id'])
    return {'ok':True}
