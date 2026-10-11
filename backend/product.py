"""Vacancy lifecycle and candidate-initiated, recipient-scoped applications."""
import json,secrets,time
from fastapi import APIRouter,Depends,HTTPException
from pydantic import Field
from .auth import session_user,require_role,require_verified,require_org_role
from .db import connection,dumps,now,audit,bump
from .schemas import StrictModel,VacancyInput
from .domain import vacancy_from_row,update_index,candidate_from_row
from .trust import ensure_trusted,risk_signals,company
from .privacy import consent,public_profile,contact_access
from .identity import notify

router=APIRouter(prefix='/api',tags=['Vacancies and applications'])

def public_vacancy(row):
    payload=json.loads(row['payload'])
    return not row['archived'] and payload.get('state','active')=='active' and payload.get('expires',time.time()+1)>time.time()

def own(conn,identity,user):
    require_org_role(user,'owner','recruiter')
    row=conn.execute('SELECT * FROM vacancies WHERE id=? AND org_id=?',(identity,user['org_id'])).fetchone()
    if not row:raise HTTPException(404,'Вакансия не найдена')
    return row

def create(body,user):
    require_org_role(user,'owner','recruiter');require_verified(user)
    identity='v_'+secrets.token_hex(10)
    with connection() as conn:
        name=conn.execute('SELECT name FROM organizations WHERE id=?',(user['org_id'],)).fetchone()[0]
        payload=dict(body.model_dump(),id=identity,company=name,currency='RUB',source='Платформа',source_url=None,published_at=None,imported_at=now(),demo=False,extraction_reviewed=True,state='draft')
        conn.execute('INSERT INTO vacancies(id,org_id,payload) VALUES(?,?,?)',(identity,user['org_id'],dumps(payload)))
        bump(conn);audit(conn,user['id'],'vacancy_drafted',identity)
        return vacancy_from_row(conn.execute('SELECT * FROM vacancies WHERE id=?',(identity,)).fetchone(),user)

class Edit(VacancyInput):version:int=Field(ge=1)
@router.put('/vacancies/{identity}')
def edit(identity:str,body:Edit,user=Depends(session_user)):
    require_verified(user)
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');row=own(conn,identity,user)
        if row['version']!=body.version:raise HTTPException(409,'Запись изменена; обновите страницу')
        data=json.loads(row['payload']);data.update(body.model_dump(exclude={'version'}));data['state']='draft';data['published_at']=None
        data.pop('moderation',None)
        conn.execute('UPDATE vacancies SET payload=?,archived=0,version=version+1 WHERE id=?',(dumps(data),identity));bump(conn);update_index('vacancy',identity,conn)
        audit(conn,user['id'],'vacancy_edited',identity)
        return vacancy_from_row(conn.execute('SELECT * FROM vacancies WHERE id=?',(identity,)).fetchone(),user)

class Version(StrictModel):version:int=Field(ge=1)
@router.post('/vacancies/{identity}/publish')
def publish(identity:str,body:Version,user=Depends(session_user)):
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');row=own(conn,identity,user);ensure_trusted(conn,user)
        if row['version']!=body.version:raise HTTPException(409,'Запись изменена; обновите страницу')
        data=json.loads(row['payload']);signals=risk_signals(data['title']+' '+data['description'])
        data.update(state='pending' if signals else 'active',published_at=now(),expires=time.time()+data.get('valid_days',30)*86400,moderation={'signals':signals,'decision':'pending' if signals else 'automated_allow'})
        conn.execute('UPDATE vacancies SET payload=?,archived=0,version=version+1 WHERE id=?',(dumps(data),identity));bump(conn);update_index('vacancy',identity,conn)
        audit(conn,user['id'],'vacancy_publish_requested',identity,{'signals':signals})
        return vacancy_from_row(conn.execute('SELECT * FROM vacancies WHERE id=?',(identity,)).fetchone(),user)

@router.get('/admin/vacancy-reviews')
def reviews(user=Depends(session_user)):
    require_role(user,'admin')
    with connection() as conn:return {'items':[vacancy_from_row(r,user) for r in conn.execute('SELECT * FROM vacancies WHERE archived=0') if json.loads(r['payload']).get('state')=='pending']}

class Decision(Version):approve:bool;rationale:str=Field(min_length=10,max_length=1000)
@router.post('/admin/vacancy-reviews/{identity}')
def review(identity:str,body:Decision,user=Depends(session_user)):
    require_role(user,'admin')
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');row=conn.execute('SELECT * FROM vacancies WHERE id=?',(identity,)).fetchone()
        if not row:raise HTTPException(404,'Вакансия не найдена')
        data=json.loads(row['payload'])
        if row['version']!=body.version or data.get('state')!='pending':raise HTTPException(409,'Проверка уже завершена или запись изменена')
        if body.approve and company(conn,row['org_id'])['verification'] not in {'verified','demo_verified'}:raise HTTPException(409,'Компания не подтверждена')
        data.update(state='active' if body.approve else 'rejected',moderation={'reviewer':user['id'],'rationale':body.rationale,'reviewed_at':now()})
        conn.execute('UPDATE vacancies SET payload=?,version=version+1 WHERE id=?',(dumps(data),identity));bump(conn);update_index('vacancy',identity,conn)
        audit(conn,user['id'],'vacancy_reviewed',identity,{'approved':body.approve})
    return {'ok':True}

def application_view(conn,row,user):
    data=dict(row,payload=json.loads(row['payload']))
    c=conn.execute('SELECT * FROM candidates WHERE id=?',(row['candidate_id'],)).fetchone()
    data['candidate']=public_profile(candidate_from_row(c,conn),user,conn)
    v=conn.execute('SELECT * FROM vacancies WHERE id=?',(row['vacancy_id'],)).fetchone()
    data['vacancy']=vacancy_from_row(v,user)
    from .privacy import scrub
    if user['role']=='employer' and not contact_access(conn,row['candidate_id'],user):data['payload']['message']=scrub(data['payload']['message'])
    return data

@router.get('/applications')
def applications(user=Depends(session_user)):
    require_role(user,'candidate','employer');require_verified(user)
    key,value=('candidate_id',user['candidate_id']) if user['role']=='candidate' else ('org_id',user['org_id'])
    with connection() as conn:return {'items':[application_view(conn,r,user) for r in conn.execute('SELECT * FROM applications WHERE '+key+'=? ORDER BY created_at DESC',(value,))]}

class Apply(StrictModel):
    vacancy_id:str=Field(max_length=100);message:str=Field(default='',max_length=2000);share_contacts:bool=False

@router.post('/applications',status_code=201)
def apply(body:Apply,user=Depends(session_user)):
    require_role(user,'candidate');require_verified(user)
    if not body.share_contacts:raise HTTPException(422,'Подтвердите передачу профиля и контактов именно этой компании')
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if not consent(conn,user['candidate_id'],'processing'):raise HTTPException(403,'Требуется согласие на обработку')
        v=conn.execute('SELECT * FROM vacancies WHERE id=?',(body.vacancy_id,)).fetchone()
        if not v or not public_vacancy(v):raise HTTPException(409,'Вакансия закрыта или не опубликована')
        if not v['org_id']:raise HTTPException(409,'Отклик на внешнюю вакансию подаётся на сайте источника')
        if company(conn,v['org_id'])['verification'] not in {'verified','demo_verified'}:raise HTTPException(403,'Компания не подтверждена')
        if conn.execute('SELECT 1 FROM employer_blocks WHERE candidate_id=? AND org_id=?',(user['candidate_id'],v['org_id'])).fetchone():raise HTTPException(403,'Компания заблокирована кандидатом')
        existing=conn.execute('SELECT * FROM applications WHERE candidate_id=? AND vacancy_id=?',(user['candidate_id'],v['id'])).fetchone()
        if existing:return application_view(conn,existing,user)
        identity='app_'+secrets.token_hex(10);stamp=now()
        data={'message':body.message,'contact_consent':{'granted':True,'recipient_org':v['org_id'],'policy':'application-v1','at':stamp},'vacancy_version':v['version'],'history':[{'status':'submitted','at':stamp}]}
        conn.execute("INSERT INTO applications(id,candidate_id,org_id,vacancy_id,status,payload,created_at,updated_at) VALUES(?,?,?,?,'submitted',?,?,?)",(identity,user['candidate_id'],v['org_id'],v['id'],dumps(data),stamp,stamp))
        conn.execute('INSERT INTO contact_grants VALUES(?,?,?,NULL,1,?,NULL)',('application_grant_'+identity,user['candidate_id'],v['org_id'],stamp))
        for member in conn.execute('SELECT user_id FROM organization_members WHERE org_id=?',(v['org_id'],)):notify(conn,member[0],'application',identity,'Новый отклик на вакансию')
        audit(conn,user['id'],'application_submitted',identity,{'org_id':v['org_id']})
        return application_view(conn,conn.execute('SELECT * FROM applications WHERE id=?',(identity,)).fetchone(),user)

class Status(Version):status:str
@router.post('/applications/{identity}/status')
def status(identity:str,body:Status,user=Depends(session_user)):
    require_role(user,'candidate','employer');require_verified(user)
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');key,value=('candidate_id',user['candidate_id']) if user['role']=='candidate' else ('org_id',user['org_id'])
        row=conn.execute('SELECT * FROM applications WHERE id=? AND '+key+'=?',(identity,value)).fetchone()
        if not row:raise HTTPException(404,'Отклик не найден')
        if row['version']!=body.version:raise HTTPException(409,'Отклик изменён; обновите страницу')
        if row['status'] in {'withdrawn','rejected','hired'}:raise HTTPException(409,'Отклик уже завершён')
        if user['role']=='candidate':
            if body.status!='withdrawn':raise HTTPException(422,'Кандидат может только отозвать отклик')
        else:
            ensure_trusted(conn,user)
            allowed={'submitted':{'reviewing','rejected'},'reviewing':{'interview','rejected'},'interview':{'hired','rejected'}}
            if body.status not in allowed.get(row['status'],set()):raise HTTPException(422,'Недопустимый переход статуса')
        data=json.loads(row['payload']);data['history'].append({'status':body.status,'at':now(),'actor':user['id']})
        if body.status in {'withdrawn','rejected'}:
            data['contact_consent']['granted']=False
            conn.execute('UPDATE contact_grants SET active=0,revoked_at=? WHERE id=?',(now(),'application_grant_'+identity))
            if not conn.execute('SELECT 1 FROM contact_grants WHERE candidate_id=? AND org_id=? AND active=1',(row['candidate_id'],row['org_id'])).fetchone():
                from .integration_privacy import revoke_transfers
                revoke_transfers(conn,row['candidate_id'],row['org_id'],'application_'+body.status)
        conn.execute('UPDATE applications SET status=?,payload=?,version=version+1,updated_at=? WHERE id=?',(body.status,dumps(data),now(),identity))
        audit(conn,user['id'],'application_status',identity,{'status':body.status})
        return application_view(conn,conn.execute('SELECT * FROM applications WHERE id=?',(identity,)).fetchone(),user)
