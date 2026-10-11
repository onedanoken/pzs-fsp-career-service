"""Voluntary company cases: freshness evidence, never a grade mutation."""
import json,secrets,time
from fastapi import APIRouter,Depends,HTTPException
from pydantic import Field,model_validator
from .auth import session_user,require_role,require_verified,require_org_role
from .schemas import StrictModel
from .db import connection,dumps,now,audit,bump
from .trust import ensure_trusted,company,risk_signals
from .assessment_catalog import SPECIALIZATIONS
from .privacy import consent

router=APIRouter(prefix='/api',tags=['Voluntary regular cases'])

class Case(StrictModel):
    title:str=Field(min_length=3,max_length=140);specialization:str
    task:str=Field(min_length=40,max_length=6000);rubric:list[str]=Field(min_length=2,max_length=8)
    interval_days:int=Field(default=30,ge=7,le=180);expires_days:int=Field(default=90,ge=7,le=365)
    @model_validator(mode='after')
    def valid(self):
        if self.specialization not in SPECIALIZATIONS or any(not 5<=len(x)<=300 for x in self.rubric):raise ValueError('Укажите направление и проверяемые критерии')
        return self

def view(conn,row,user):
    data=dict(row,payload=json.loads(row['payload']))
    if user['role']=='candidate':
        sub=conn.execute('SELECT * FROM case_subscriptions WHERE candidate_id=? AND case_id=?',(user['candidate_id'],row['id'])).fetchone()
        data['subscription']=dict(sub) if sub else None
        data['attempts']=[dict(r,payload=json.loads(r['payload'])) for r in conn.execute('SELECT * FROM case_attempts WHERE case_id=? AND candidate_id=? ORDER BY created_at DESC',(row['id'],user['candidate_id']))]
    return data

@router.get('/regular-cases')
def cases(user=Depends(session_user)):
    with connection() as conn:
        rows=conn.execute('SELECT * FROM regular_cases ORDER BY created_at DESC').fetchall()
        if user['role']=='employer':rows=[r for r in rows if r['org_id']==user['org_id']]
        if user['role']=='candidate':rows=[r for r in rows if r['state']=='approved' and json.loads(r['payload'])['expires']>time.time() and company(conn,r['org_id'])['verification'] in {'verified','demo_verified'}]
        return {'items':[view(conn,r,user) for r in rows]}

@router.post('/regular-cases',status_code=201)
def create(body:Case,user=Depends(session_user)):
    with connection() as conn:
        ensure_trusted(conn,user);identity='case_'+secrets.token_hex(10)
        data=body.model_dump();data.update(expires=time.time()+body.expires_days*86400,signals=risk_signals(body.task),company=company(conn,user['org_id'])['name'])
        conn.execute("INSERT INTO regular_cases(id,org_id,state,payload,created_at) VALUES(?,?,'pending',?,?)",(identity,user['org_id'],dumps(data),now()))
        audit(conn,user['id'],'regular_case_submitted',identity)
    return {'id':identity,'state':'pending','version':1}

class Review(StrictModel):version:int=Field(ge=1);approve:bool;rationale:str=Field(min_length=10,max_length=1000)
@router.post('/admin/regular-cases/{identity}/review')
def moderate(identity:str,body:Review,user=Depends(session_user)):
    require_role(user,'admin')
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');row=conn.execute('SELECT * FROM regular_cases WHERE id=?',(identity,)).fetchone()
        if not row:raise HTTPException(404,'Кейс не найден')
        if row['version']!=body.version or row['state']!='pending':raise HTTPException(409,'Кейс изменён')
        if body.approve and company(conn,row['org_id'])['verification'] not in {'verified','demo_verified'}:raise HTTPException(409,'Компания не подтверждена')
        data=json.loads(row['payload']);data['moderation']={'rationale':body.rationale,'reviewer':user['id'],'at':now()}
        conn.execute('UPDATE regular_cases SET state=?,payload=?,version=version+1 WHERE id=?',('approved' if body.approve else 'rejected',dumps(data),identity))
        audit(conn,user['id'],'regular_case_reviewed',identity,{'approve':body.approve})
    return {'ok':True}

def available(conn,identity):
    row=conn.execute("SELECT * FROM regular_cases WHERE id=? AND state='approved'",(identity,)).fetchone()
    if not row or json.loads(row['payload'])['expires']<=time.time() or company(conn,row['org_id'])['verification'] not in {'verified','demo_verified'}:raise HTTPException(409,'Кейс недоступен')
    return row

class Subscription(StrictModel):enabled:bool
@router.put('/regular-cases/{identity}/subscription')
def subscribe(identity:str,body:Subscription,user=Depends(session_user)):
    require_role(user,'candidate');require_verified(user)
    with connection() as conn:
        available(conn,identity)
        conn.execute('INSERT INTO case_subscriptions VALUES(?,?,?,?) ON CONFLICT(candidate_id,case_id) DO UPDATE SET enabled=excluded.enabled',(user['candidate_id'],identity,int(body.enabled),time.time()))
        audit(conn,user['id'],'regular_case_subscription',identity,{'enabled':body.enabled})
    return {'ok':True}

class Submit(StrictModel):answer:str=Field(min_length=10,max_length=6000);share_answer:bool=False
@router.post('/regular-cases/{identity}/attempts',status_code=201)
def submit(identity:str,body:Submit,user=Depends(session_user)):
    require_role(user,'candidate');require_verified(user)
    if not body.share_answer:raise HTTPException(422,'Подтвердите передачу ответа автору кейса без контактов')
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');row=available(conn,identity);data=json.loads(row['payload'])
        if conn.execute('SELECT 1 FROM employer_blocks WHERE candidate_id=? AND org_id=?',(user['candidate_id'],row['org_id'])).fetchone():raise HTTPException(403,'Компания заблокирована кандидатом')
        if not consent(conn,user['candidate_id'],'processing'):raise HTTPException(403,'Согласие на обработку отозвано')
        pending=conn.execute("SELECT id FROM case_attempts WHERE case_id=? AND candidate_id=? AND state='submitted'",(identity,user['candidate_id'])).fetchone()
        if pending:raise HTTPException(409,'Предыдущий ответ ожидает проверки')
        last=conn.execute('SELECT created_at FROM case_attempts WHERE case_id=? AND candidate_id=? ORDER BY created_at DESC LIMIT 1',(identity,user['candidate_id'])).fetchone()
        if last:
            from datetime import datetime
            if time.time()-datetime.fromisoformat(last[0]).timestamp()<data['interval_days']*86400:raise HTTPException(429,'Следующий добровольный кейс доступен после указанного интервала')
        attempt='attempt_'+secrets.token_hex(10)
        conn.execute("INSERT INTO case_attempts(id,candidate_id,case_id,state,payload,created_at) VALUES(?,?,?,'submitted',?,?)",(attempt,user['candidate_id'],identity,dumps({'answer':body.answer,'case_version':row['version'],'rubric':data['rubric'],'shared_at':now()}),now()))
        conn.execute('UPDATE case_subscriptions SET next_due=? WHERE candidate_id=? AND case_id=?',(time.time()+data['interval_days']*86400,user['candidate_id'],identity))
        audit(conn,user['id'],'regular_case_answered',attempt)
    return {'id':attempt,'state':'submitted','version':1}

@router.get('/regular-case-attempts')
def attempts(user=Depends(session_user)):
    require_role(user,'employer','admin')
    with connection() as conn:
        rows=conn.execute('SELECT a.*,c.org_id FROM case_attempts a JOIN regular_cases c ON c.id=a.case_id ORDER BY a.created_at DESC').fetchall()
        from .privacy import redact
        return {'items':[dict(r,payload=redact(json.loads(r['payload']))) for r in rows if user['role']=='admin' or r['org_id']==user['org_id']]}

class Feedback(StrictModel):version:int=Field(ge=1);passed:bool;rationale:str=Field(min_length=20,max_length=2000)
@router.post('/regular-case-attempts/{identity}/review')
def feedback(identity:str,body:Feedback,user=Depends(session_user)):
    require_org_role(user,'owner','recruiter');require_verified(user)
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');row=conn.execute('SELECT a.*,c.org_id FROM case_attempts a JOIN regular_cases c ON c.id=a.case_id WHERE a.id=? AND c.org_id=?',(identity,user['org_id'])).fetchone()
        if not row:raise HTTPException(404,'Ответ не найден')
        ensure_trusted(conn,user)
        if row['version']!=body.version or row['state']!='submitted':raise HTTPException(409,'Ответ уже проверен')
        data=json.loads(row['payload']);data['feedback']={'passed':body.passed,'rationale':body.rationale,'reviewer':user['id'],'at':now()}
        conn.execute("UPDATE case_attempts SET state='reviewed',payload=?,version=version+1,reviewed_at=? WHERE id=?",(dumps(data),now(),identity))
        if body.passed:
            profile=conn.execute('SELECT payload FROM candidates WHERE id=?',(row['candidate_id'],)).fetchone();p=json.loads(profile[0]);p['freshness']={'case_id':row['case_id'],'at':now(),'source':'employer_review'}
            conn.execute('UPDATE candidates SET payload=?,version=version+1 WHERE id=?',(dumps(p),row['candidate_id']));bump(conn)
        audit(conn,user['id'],'regular_case_feedback',identity,{'passed':body.passed})
    return {'ok':True,'grade_changed':False}
