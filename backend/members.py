"""Company invitations bound to an immutable account; legacy invites retained."""
import hashlib,secrets,time,os
from fastapi import APIRouter,Depends,HTTPException
from pydantic import Field
from .auth import session_user,require_role,require_verified,require_org_role
from .db import connection,now,audit
from .schemas import StrictModel
router=APIRouter(prefix='/api',tags=['Company employees'])

@router.get('/company/members')
def members(user=Depends(session_user)):
    require_role(user,'employer')
    with connection() as conn:return {'items':[dict(r) for r in conn.execute('SELECT m.user_id,m.role,u.name,u.username FROM organization_members m JOIN users u ON u.id=m.user_id WHERE m.org_id=?',(user['org_id'],))],'can_manage':user.get('org_role')=='owner'}

class Invite(StrictModel):
    username:str|None=Field(default=None,pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{2,39}$')
    email:str|None=Field(default=None,min_length=5,max_length=160)
    role:str
@router.post('/company/members/invite',status_code=201)
def invite(body:Invite,user=Depends(session_user)):
    require_org_role(user,'owner');require_verified(user)
    if body.role not in {'recruiter','viewer'} or bool(body.username)==bool(body.email):raise HTTPException(422,'Укажите логин и роль recruiter/viewer')
    if body.email and (os.environ.get('FSP_LEGACY_EMAIL_AUTH')!='1' or '@' not in body.email):raise HTTPException(422,'Приглашайте сотрудника по логину')
    raw=secrets.token_urlsafe(32)
    with connection() as conn:
        recipient=body.email.lower() if body.email else None
        if body.username:
            from .auth import account_enabled
            target=conn.execute('SELECT * FROM users WHERE username=?',(body.username.lower(),)).fetchone()
            if not target or target['role']!='employer' or not account_enabled(dict(target)):raise HTTPException(404,'Активный аккаунт работодателя с таким логином не найден')
            if target['org_id']==user['org_id']:raise HTTPException(409,'Сотрудник уже состоит в компании')
            recipient='account:'+target['id']
        # Keep the original six-column format so historical invites and rollback
        # images retain compatibility. New references bind ID, never a mutable login.
        conn.execute('INSERT INTO member_invites VALUES(?,?,?,?,?,NULL)',(hashlib.sha256(raw.encode()).hexdigest(),user['org_id'],recipient,body.role,time.time()+86400))
        audit(conn,user['id'],'employee_invited',user['org_id'],{'recipient':recipient,'role':body.role})
    return {'token':raw,'expires_in':86400,'notice':'Передайте код сотруднику. Он должен войти под указанным логином и принять приглашение.'}

class Accept(StrictModel):token:str=Field(min_length=20,max_length=200)
@router.post('/company/members/accept')
def accept(body:Accept,user=Depends(session_user)):
    require_role(user,'employer');require_verified(user)
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT * FROM member_invites WHERE hash=? AND consumed_at IS NULL AND expires>?',(hashlib.sha256(body.token.encode()).hexdigest(),time.time())).fetchone()
        if not row or row['email'] not in {'account:'+user['id'],user['email']}:raise HTTPException(400,'Приглашение недействительно или адресовано другому аккаунту')
        # Owners with active work cannot abandon their company through this path.
        if user.get('org_role')=='owner' and user['org_id']!=row['org_id']:
            if any(conn.execute('SELECT 1 FROM '+table+' WHERE org_id=?',(user['org_id'],)).fetchone() for table in ['employer_needs','ats_connections','vacancies','regular_cases','invitations','applications','offers']):raise HTTPException(409,'В вашей организации есть работа; переход требует передачи полномочий')
        conn.execute('UPDATE users SET org_id=? WHERE id=?',(row['org_id'],user['id']))
        conn.execute('INSERT INTO organization_members VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET org_id=excluded.org_id,role=excluded.role',(user['id'],row['org_id'],row['role'],now()))
        conn.execute('UPDATE member_invites SET consumed_at=? WHERE hash=?',(now(),row['hash']))
        audit(conn,user['id'],'employee_joined',row['org_id'],{'role':row['role']})
    return {'ok':True,'org_id':row['org_id'],'role':row['role']}

class Role(StrictModel):role:str
@router.put('/company/members/{identity}')
def change(identity:str,body:Role,user=Depends(session_user)):
    require_org_role(user,'owner')
    if body.role not in {'recruiter','viewer'}:raise HTTPException(422,'Неподдерживаемая роль')
    with connection() as conn:
        row=conn.execute('SELECT * FROM organization_members WHERE user_id=? AND org_id=?',(identity,user['org_id'])).fetchone()
        if not row:raise HTTPException(404,'Сотрудник не найден')
        if row['role']=='owner':raise HTTPException(409,'Передача владельца через этот метод недоступна')
        conn.execute('UPDATE organization_members SET role=? WHERE user_id=?',(body.role,identity));audit(conn,user['id'],'employee_role_changed',identity,{'role':body.role})
    return {'ok':True}
