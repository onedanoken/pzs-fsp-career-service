"""Username accounts and a separate, audited service administration boundary."""
import hashlib,json,os,secrets,time
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException,Query,Request,Response
from pydantic import Field
from .schemas import StrictModel
from .auth import session_user,check_password,create_session,account_enabled,require_role
from .db import connection,now,audit,dumps,password_hash

router=APIRouter(prefix='/api',tags=['Accounts and administration'])
USER_FIELDS='id,username,name,role,active,admin_access,account_version,created_at,last_login_at,password_change_required,is_synthetic'

def registration_enabled():
    with connection() as conn:row=conn.execute("SELECT value FROM meta WHERE key='registration_enabled'").fetchone()
    return row[0]=='1' if row else os.environ.get('FSP_ALLOW_REGISTRATION','1')=='1'

def owner(user):
    if user.get('admin_access')!='owner':raise HTTPException(403,'Управление сервисом доступно только администратору')

@router.get('/admin/complaints')
def complaint_queue(user=Depends(session_user)):
    require_role(user,'admin')
    from .trust import my_complaints
    # Administrative queue is explicit; a staff member's personal complaint history
    # remains restricted to that account on /complaints.
    result=my_complaints(dict(user,role='admin'))
    from .db import DEMO
    if not DEMO:
        with connection() as conn:
            actual={r[0] for r in conn.execute('SELECT id FROM users WHERE is_synthetic=0')}
        result['items']=[x for x in result['items'] if x['actor_id'] in actual]
    return result

def lock(conn):
    # Serializes account mutations on SQLite and PostgreSQL, including last-owner checks.
    conn.execute("UPDATE meta SET value=value WHERE key='data_revision'")

def recovery_token(conn,identity,seconds=365*86400):
    raw=secrets.token_urlsafe(24)
    conn.execute('UPDATE account_recovery SET consumed_at=? WHERE user_id=? AND consumed_at IS NULL',(now(),identity))
    conn.execute('INSERT INTO account_recovery VALUES(?,?,?,?,NULL)',(hashlib.sha256(raw.encode()).hexdigest(),identity,time.time()+seconds,now()))
    return raw

class PasswordChange(StrictModel):
    current_password:str=Field(min_length=1,max_length=200)
    password:str=Field(min_length=12,max_length=200)

@router.post('/auth/password')
def change_password(body:PasswordChange,response:Response,user=Depends(session_user)):
    with connection() as conn:
        lock(conn)
        current=conn.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone()
        if not current or not account_enabled(dict(current)) or not check_password(body.current_password,current['password']):raise HTTPException(403,'Неверный текущий пароль')
        if check_password(body.password,current['password']):raise HTTPException(422,'Новый пароль должен отличаться от текущего')
        conn.execute('UPDATE users SET password=?,password_change_required=0,account_version=account_version+1 WHERE id=?',(password_hash(body.password),user['id']))
        conn.execute('DELETE FROM sessions WHERE user_id=?',(user['id'],))
        updated=dict(conn.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone())
        audit(conn,user['id'],'account_password_changed',user['id'])
    return create_session(response,updated)

class Recovery(StrictModel):
    username:str=Field(min_length=3,max_length=40)
    recovery_code:str=Field(min_length=20,max_length=100)
    password:str=Field(min_length=12,max_length=200)

@router.post('/auth/recover')
def recover(body:Recovery,request:Request):
    from .main import rate_limit
    rate_limit(request)
    with connection() as conn:
        lock(conn)
        target=conn.execute('SELECT * FROM users WHERE username=?',(body.username.lower(),)).fetchone()
        code=conn.execute('SELECT * FROM account_recovery WHERE hash=? AND consumed_at IS NULL AND expires>?',(hashlib.sha256(body.recovery_code.encode()).hexdigest(),time.time())).fetchone()
        if not target or not account_enabled(dict(target)) or not code or code['user_id']!=target['id']:raise HTTPException(400,'Логин или резервный код недействителен')
        conn.execute('UPDATE users SET password=?,password_change_required=0,account_version=account_version+1 WHERE id=?',(password_hash(body.password),target['id']))
        conn.execute('UPDATE account_recovery SET consumed_at=? WHERE user_id=?',(now(),target['id']))
        conn.execute('UPDATE auth_tokens SET consumed_at=? WHERE user_id=? AND consumed_at IS NULL',(now(),target['id']))
        conn.execute('DELETE FROM sessions WHERE user_id=?',(target['id'],))
        audit(conn,target['id'],'account_recovered',target['id'])
    return {'ok':True,'message':'Пароль изменён. Войдите с новым паролем и создайте новый резервный код в настройках.'}

class ConfirmPassword(StrictModel):
    current_password:str=Field(min_length=1,max_length=200)

@router.post('/auth/recovery-code')
def new_recovery(body:ConfirmPassword,user=Depends(session_user)):
    # Separate endpoint uses the current credential; no email dependency.
    if not check_password(body.current_password,user['password']):raise HTTPException(403,'Неверный текущий пароль')
    with connection() as conn:
        lock(conn);raw=recovery_token(conn,user['id']);audit(conn,user['id'],'account_recovery_rotated',user['id'])
    return {'recovery_code':raw}

@router.get('/admin/users')
def users(q:str=Query('',max_length=100),status:Literal['all','active','blocked']='all',access:Literal['all','none','operator','owner']='all',page:int=Query(1,ge=1),user=Depends(session_user)):
    owner(user);where=['is_synthetic=0'];args=[]
    if q:
        where.append('(LOWER(username) LIKE ? ESCAPE \'!\' OR LOWER(name) LIKE ? ESCAPE \'!\')')
        term='%'+q.lower().replace('!','!!').replace('%','!%').replace('_','!_')+'%';args.extend([term,term])
    if status!='all':where.append('active=?');args.append(int(status=='active'))
    if access!='all':where.append('admin_access=?');args.append(access)
    condition=' WHERE '+' AND '.join(where) if where else ''
    with connection() as conn:
        total=conn.execute('SELECT COUNT(*) FROM users'+condition,args).fetchone()[0]
        rows=[dict(r) for r in conn.execute('SELECT '+USER_FIELDS+' FROM users'+condition+' ORDER BY username,id LIMIT 25 OFFSET ?',[*args,(page-1)*25])]
    return {'items':rows,'total':total,'page':page,'page_size':25}

@router.get('/admin/users/{identity}')
def detail(identity:str,user=Depends(session_user)):
    owner(user)
    with connection() as conn:
        row=conn.execute('SELECT '+USER_FIELDS+' FROM users WHERE id=?',(identity,)).fetchone()
        if not row:raise HTTPException(404,'Пользователь не найден')
        sessions=conn.execute('SELECT COUNT(*) FROM sessions WHERE user_id=? AND expires>?',(identity,time.time())).fetchone()[0]
        return dict(row,sessions=sessions)

class AccountAction(StrictModel):
    action:Literal['block','unblock','access','revoke_sessions','reset_password']
    version:int=Field(ge=1)
    reason:str=Field(min_length=5,max_length=500)
    current_password:str=Field(min_length=1,max_length=200)
    access:Literal['none','operator','owner']|None=None

@router.post('/admin/users/{identity}/actions')
def act(identity:str,body:AccountAction,user=Depends(session_user)):
    owner(user)
    if not check_password(body.current_password,user['password']):raise HTTPException(403,'Подтвердите действие своим паролем')
    if body.action=='access' and body.access is None:raise HTTPException(422,'Выберите уровень доступа')
    result={'ok':True}
    with connection() as conn:
        lock(conn)
        actor=conn.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone()
        if not actor or not actor['active'] or actor['admin_access']!='owner' or not check_password(body.current_password,actor['password']):raise HTTPException(403,'Права администратора изменились')
        target=conn.execute('SELECT * FROM users WHERE id=?',(identity,)).fetchone()
        if not target:raise HTTPException(404,'Пользователь не найден')
        if target['account_version']!=body.version:raise HTTPException(409,'Данные изменились. Обновите карточку пользователя')
        if identity==user['id'] and body.action in {'block','access','reset_password','revoke_sessions'}:raise HTTPException(409,'Для собственного аккаунта используйте настройки безопасности')
        removing_owner=target['active'] and target['admin_access']=='owner' and (body.action=='block' or body.action=='access' and body.access!='owner')
        if removing_owner and conn.execute("SELECT COUNT(*) FROM users WHERE active=1 AND admin_access='owner' AND is_synthetic=0").fetchone()[0]<=1:raise HTTPException(409,'Нельзя отключить последнего администратора')
        if target['is_synthetic'] and (body.action=='unblock' or body.action=='access' and body.access!='none'):raise HTTPException(409,'Тестовый аккаунт нельзя включить или назначить администратором')
        before={'active':bool(target['active']),'access':target['admin_access']}
        if body.action in {'block','unblock'}:conn.execute('UPDATE users SET active=? WHERE id=?',(int(body.action=='unblock'),identity))
        if body.action=='access':conn.execute('UPDATE users SET admin_access=? WHERE id=?',(body.access,identity))
        if body.action=='reset_password':
            if not target['active'] or target['is_synthetic']:raise HTTPException(409,'Восстановление для этого аккаунта недоступно')
            result['recovery_code']=recovery_token(conn,identity,900)
            conn.execute('UPDATE users SET password=?,password_change_required=1 WHERE id=?',(password_hash(secrets.token_urlsafe(32)),identity))
        if body.action!='unblock':
            conn.execute('DELETE FROM sessions WHERE user_id=?',(identity,))
            conn.execute('UPDATE auth_tokens SET consumed_at=? WHERE user_id=? AND consumed_at IS NULL',(now(),identity))
        if body.action=='block':conn.execute('UPDATE account_recovery SET consumed_at=? WHERE user_id=? AND consumed_at IS NULL',(now(),identity))
        conn.execute('UPDATE users SET account_version=account_version+1 WHERE id=?',(identity,))
        audit(conn,user['id'],'admin_account_'+body.action,identity,{'before':before,'access':body.access,'reason':body.reason})
    return result

@router.get('/admin/audit')
def events(page:int=Query(1,ge=1),identity:str=Query('',max_length=100),user=Depends(session_user)):
    owner(user);where=' WHERE (action LIKE \'admin_account_%\' OR action LIKE \'account_%\' OR action=\'admin_registration_changed\')';args=[]
    if identity:where+=' AND entity_id=?';args.append(identity)
    with connection() as conn:
        total=conn.execute('SELECT COUNT(*) FROM audit'+where,args).fetchone()[0]
        items=[]
        for r in conn.execute('SELECT * FROM audit'+where+' ORDER BY id DESC LIMIT 25 OFFSET ?',[*args,(page-1)*25]):
            actor=conn.execute('SELECT username FROM users WHERE id=?',(r['actor_id'],)).fetchone()
            target=conn.execute('SELECT username FROM users WHERE id=?',(r['entity_id'],)).fetchone()
            items.append(dict(r,metadata=json.loads(r['metadata']),actor_username=actor[0] if actor else None,target_username=target[0] if target else None))
    return {'items':items,'total':total,'page':page,'page_size':25}

@router.get('/admin/overview')
def overview(user=Depends(session_user)):
    owner(user)
    with connection() as conn:
        counts={key:conn.execute(sql).fetchone()[0] for key,sql in {'users':'SELECT COUNT(*) FROM users WHERE is_synthetic=0','blocked':'SELECT COUNT(*) FROM users WHERE active=0 AND is_synthetic=0','administrators':"SELECT COUNT(*) FROM users WHERE active=1 AND admin_access='owner' AND is_synthetic=0",'jobs_failed':"SELECT COUNT(*) FROM jobs WHERE status='failed'",'open_complaints':"SELECT COUNT(*) FROM moderation_cases c JOIN users u ON u.id=c.actor_id WHERE c.state='open' AND u.is_synthetic=0"}.items()}
    return {'counts':counts,'registration_enabled':registration_enabled(),'login_method':'username_or_email' if os.environ.get('FSP_EMAIL_REQUIRED','1')=='1' else 'username','mail_required':os.environ.get('FSP_EMAIL_REQUIRED','1')=='1','history_retained':True}

class Settings(StrictModel):
    registration_enabled:bool
    current_password:str=Field(min_length=1,max_length=200)

@router.put('/admin/settings')
def settings(body:Settings,user=Depends(session_user)):
    owner(user)
    if not check_password(body.current_password,user['password']):raise HTTPException(403,'Подтвердите действие своим паролем')
    with connection() as conn:
        lock(conn)
        current=conn.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone()
        if not current or not account_enabled(dict(current)) or current['admin_access']!='owner' or not check_password(body.current_password,current['password']):raise HTTPException(403,'Права администратора изменились')
        conn.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('registration_enabled','1' if body.registration_enabled else '0'))
        audit(conn,user['id'],'admin_registration_changed','service',{'enabled':body.registration_enabled})
    return {'ok':True,'registration_enabled':body.registration_enabled}
