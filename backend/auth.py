import hashlib
import hmac
import secrets
import time
import os
from fastapi import HTTPException, Request, Response
from .db import connection, password_hash

COOKIE="fsp_session"

def session_user(request:Request):
    raw=request.cookies.get(COOKIE)
    if not raw:
        raise HTTPException(401,"Войдите в аккаунт")
    hashed=hashlib.sha256(raw.encode()).hexdigest()
    with connection() as conn:
        row=conn.execute("SELECT u.*,s.csrf FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires>?",
                         (hashed,time.time())).fetchone()
    if not row:
        raise HTTPException(401,"Сессия истекла")
    result=dict(row)
    if not account_enabled(result):raise HTTPException(401,"Доступ к аккаунту закрыт")
    if result.get('password_change_required') and request.url.path not in {'/api/auth/me','/api/auth/logout','/api/auth/password'}:
        raise HTTPException(403,'Сначала измените временный пароль')
    if result.get('org_id'):
        with connection() as conn:
            member=conn.execute('SELECT role FROM organization_members WHERE user_id=? AND org_id=?',(result['id'],result['org_id'])).fetchone()
        result['org_role']=member[0] if member else 'viewer'
    return result

def public_user(user):
    result={key:user.get(key) for key in ["id","email","name","role","candidate_id","org_id","org_role","csrf","email_verified","username","admin_access","password_change_required","account_version","auth_kind"]}
    if user.get("org_id"):
        with connection() as conn:
            org=conn.execute("SELECT name FROM organizations WHERE id=?",(user["org_id"],)).fetchone()
            result["company"]=org["name"] if org else ""
    if user.get('auth_kind')=='username':result['email']=None
    return result

def create_session(response:Response,user):
    raw=secrets.token_urlsafe(32)
    csrf=secrets.token_urlsafe(24)
    with connection() as conn:
        conn.execute("DELETE FROM sessions WHERE expires<?",(time.time(),))
        conn.execute("INSERT INTO sessions VALUES(?,?,?,?)",(hashlib.sha256(raw.encode()).hexdigest(),user["id"],csrf,time.time()+8*3600))
    response.set_cookie(COOKIE,raw,max_age=8*3600,httponly=True,samesite="lax",
                        secure=os.environ.get("FSP_SECURE_COOKIE","0")=="1",path="/")
    return public_user(dict(user,csrf=csrf))

def check_password(raw,hashed):
    try:
        return hmac.compare_digest(password_hash(raw,hashed.split(":")[0]),hashed)
    except (ValueError,IndexError):
        return False

def require_role(user,*roles):
    if user.get("role")=="admin" and user.get("admin_access")=="none":raise HTTPException(403,"Административный доступ отозван")
    if user['role'] not in roles and not ('admin' in roles and user.get('admin_access') in {'owner','operator'}):
        raise HTTPException(403,"Недостаточно прав")

def require_verified(user):
    if not user.get('email_verified') and user.get('auth_kind')!='username':
        raise HTTPException(403,'Подтвердите адрес электронной почты')

def require_org_role(user,*roles):
    require_role(user,'employer')
    if user.get('org_role') not in roles:raise HTTPException(403,'У сотрудника нет прав на это действие')


def account_enabled(user):
    from . import db
    return bool(user.get('active',1)) and (db.DEMO or not user.get('is_synthetic'))
