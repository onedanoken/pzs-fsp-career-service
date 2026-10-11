"""Organization authority, current needs, moderation and candidate protection."""
import json,re,secrets,time
from fastapi import APIRouter,Depends,HTTPException
from pydantic import Field,model_validator
from typing import Literal
from .auth import session_user,require_role,require_verified,require_org_role
from .db import connection,dumps,now,audit,bump
from .schemas import StrictModel

router=APIRouter(prefix='/api',tags=['Employer trust'])
def company(conn,org_id):
    row=conn.execute('SELECT p.*,o.name FROM company_profiles p JOIN organizations o ON o.id=p.org_id WHERE p.org_id=?',(org_id,)).fetchone()
    if not row:return {'org_id':org_id,'verification':'pending','version':0,'payload':{},'name':''}
    return dict(row,payload=json.loads(row['payload']))

def risk_signals(text):
    text=text.lower();signals=[]
    if re.search(r'(оплат\w*|перевед\w*)\s+(депозит|взнос|деньги|за доступ|за трудоустройство)',text):signals.append('payment_request')
    if re.search(r'https?://(?:bit\.ly|tinyurl\.com|t\.co)/',text):signals.append('obscured_link')
    if '<script' in text or 'javascript:' in text:signals.append('unsafe_link')
    return signals

def ensure_trusted(conn,user):
    require_org_role(user,'owner','recruiter');require_verified(user)
    record=company(conn,user['org_id'])
    if record['verification'] not in {'verified','demo_verified'}:raise HTTPException(403,'Компания должна пройти проверку перед приглашением')
    return record

def actionable(conn,need,user):
    record=ensure_trusted(conn,user)
    if need['org_id']!=user['org_id'] or need['state']!='active' or need['expires']<time.time():raise HTTPException(409,'Потребность закрыта, не подтверждена или устарела')
    return record

class Company(StrictModel):
    entity_type:Literal['company','team']='company'
    description:str=Field(min_length=20,max_length=3000)
    industry:str=Field(min_length=2,max_length=100)
    domain:str=Field(default='',pattern=r'^(?:[A-Za-z0-9.-]+\.[A-Za-z]{2,})?$',max_length=150)
    contact:str=Field(min_length=5,max_length=200)
    legal_name:str=Field(min_length=2,max_length=200)
    tax_id:str=Field(default='',pattern=r'^(?:\d{10}(?:\d{2})?)?$')
    team:str=Field(min_length=10,max_length=2000)
    version:int=Field(ge=0)
    @model_validator(mode='after')
    def team_is_not_legal_entity(self):
        if self.entity_type=='team' and self.tax_id:raise ValueError('Для команды не указывайте ИНН юридического лица')
        return self

@router.get('/company')
def read_company(user=Depends(session_user)):
    require_role(user,'employer')
    with connection() as conn:return company(conn,user['org_id'])

@router.put('/company')
def update_company(body:Company,user=Depends(session_user)):
    require_org_role(user,'owner');require_verified(user)
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');current=company(conn,user['org_id'])
        if body.version!=current['version']:raise HTTPException(409,'Компания изменена; обновите страницу')
        if current['verification']=='blocked':raise HTTPException(403,'Компания заблокирована; доступна апелляция')
        # Material changes invalidate authority; pending organizations cannot mass-contact.
        payload=body.model_dump(exclude={'version'});payload['submitted_at']=now()
        conn.execute("INSERT INTO company_profiles(org_id,payload,verification,version) VALUES(?,?,'pending',1) ON CONFLICT(org_id) DO UPDATE SET payload=excluded.payload,verification='pending',version=company_profiles.version+1",(user['org_id'],dumps(payload)))
        audit(conn,user['id'],'company_submitted',user['org_id'])
        return company(conn,user['org_id'])

class Verification(StrictModel):
    state:str;version:int=Field(ge=1)
    organization_evidence:str=Field(min_length=10,max_length=1500)
    authority_evidence:str=Field(min_length=10,max_length=1500)

@router.get('/admin/companies')
def companies(user=Depends(session_user)):
    require_role(user,'admin')
    with connection() as conn:return {'items':[company(conn,r[0]) for r in conn.execute('SELECT org_id FROM company_profiles') if operational_org(conn,r[0])]}

def operational_org(conn,identity):
    from .db import DEMO
    if DEMO:return True
    row=conn.execute('SELECT payload,verification FROM company_profiles WHERE org_id=?',(identity,)).fetchone()
    if row and (row['verification']=='demo_verified' or json.loads(row['payload']).get('sandbox')):return False
    members=list(conn.execute('SELECT is_synthetic FROM users WHERE org_id=?',(identity,)))
    return not members or any(not r['is_synthetic'] for r in members)

@router.get('/admin/needs')
def admin_needs(user=Depends(session_user)):
    require_role(user,'admin')
    with connection() as conn:return {'items':[dict(r,payload=json.loads(r['payload'])) for r in conn.execute('SELECT * FROM employer_needs ORDER BY created_at DESC') if operational_org(conn,r['org_id'])]}

@router.post('/admin/companies/{org_id}/review')
def review(org_id:str,body:Verification,user=Depends(session_user)):
    require_role(user,'admin');require_verified(user)
    if body.state not in {'verified','rejected','blocked'}:raise HTTPException(422,'Неизвестный результат')
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');current=company(conn,org_id)
        if current['version']!=body.version:raise HTTPException(409,'Компания изменена')
        payload=current['payload'];payload['verification_checks']={'organization':body.organization_evidence,'authority':body.authority_evidence,'reviewed_at':now(),'reviewer':user['id'],'method':'manual_evidence_review','scope':'team_representative' if payload.get('entity_type')=='team' else 'organization_representative','notice':'Проверка представителя и предоставленных сведений; не государственная регистрация юридического лица.'}
        conn.execute('UPDATE company_profiles SET payload=?,verification=?,version=version+1 WHERE org_id=?',(dumps(payload),body.state,org_id))
        if body.state=='blocked':
            from .integration_privacy import revoke_transfers
            for row in conn.execute('SELECT DISTINCT candidate_id FROM invitations WHERE org_id=?',(org_id,)).fetchall():
                revoke_transfers(conn,row[0],org_id,'company_blocked')
            conn.execute("UPDATE employer_needs SET state='blocked',version=version+1 WHERE org_id=?",(org_id,))
            conn.execute("UPDATE contact_grants SET active=0,revoked_at=? WHERE org_id=?",(now(),org_id))
            conn.execute('UPDATE vacancies SET archived=1,version=version+1 WHERE org_id=?',(org_id,))
            conn.execute("UPDATE regular_cases SET state='blocked',version=version+1 WHERE org_id=?",(org_id,))
            from .domain import update_index
            for vacancy in conn.execute('SELECT id FROM vacancies WHERE org_id=?',(org_id,)).fetchall():update_index('vacancy',vacancy[0],conn)
        audit(conn,user['id'],'company_reviewed',org_id,{'state':body.state});bump(conn)
    return {'ok':True}

def complaint_targets(conn,user):
    """Only companies already visible to this candidate; no organization directory."""
    from .product import public_vacancy
    profile=conn.execute('SELECT payload FROM candidates WHERE id=?',(user['candidate_id'],)).fetchone()
    sandbox=json.loads(profile[0]).get('sandbox_org_id') if profile else None
    identities=set()
    for table in ('invitations','applications'):
        identities.update(r[0] for r in conn.execute('SELECT org_id FROM '+table+' WHERE candidate_id=?',(user['candidate_id'],)))
    identities.update(r[0] for r in conn.execute('SELECT org_id FROM moderation_cases WHERE actor_id=?',(user['id'],)))
    identities.update(r['org_id'] for r in conn.execute('SELECT * FROM vacancies') if r['org_id'] and public_vacancy(r))
    result=[]
    for org in sorted(identities):
        row=conn.execute('SELECT o.id,o.name,p.payload FROM organizations o LEFT JOIN company_profiles p ON p.org_id=o.id WHERE o.id=?',(org,)).fetchone()
        if not row:continue
        private=json.loads(row['payload'] or '{}').get('sandbox',False)
        if sandbox and org!=sandbox or not sandbox and private:continue
        result.append({'org_id':org,'company':row['name']})
    return sorted(result,key=lambda x:x['company'].casefold())

@router.get('/complaint-targets')
def available_complaint_targets(user=Depends(session_user)):
    require_role(user,'candidate')
    with connection() as conn:return {'items':complaint_targets(conn,user)}

class Complaint(StrictModel):
    org_id:str=Field(min_length=1,max_length=100)
    need_id:str|None=Field(default=None,max_length=100)
    kind:Literal['payment_request','misleading_terms','fraud','abuse','other','appeal']
    message:str=Field(min_length=10,max_length=2000)
    @model_validator(mode='after')
    def meaningful_message(self):
        self.message=self.message.strip()
        if len(self.message)<10:raise ValueError('Опишите произошедшее: не менее 10 символов.')
        return self

@router.post('/complaints',status_code=201)
def complain(body:Complaint,user=Depends(session_user)):
    identity='case_'+secrets.token_hex(10)
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if not conn.execute('SELECT 1 FROM organizations WHERE id=?',(body.org_id,)).fetchone():raise HTTPException(404,'Компания не найдена')
        if body.need_id and not conn.execute('SELECT 1 FROM employer_needs WHERE id=? AND org_id=?',(body.need_id,body.org_id)).fetchone():raise HTTPException(422,'Потребность другой организации')
        if user['role']=='employer' and user['org_id']!=body.org_id:raise HTTPException(403,'Недоступно')
        if user['role']=='candidate':
            if body.org_id not in {x['org_id'] for x in complaint_targets(conn,user)}:raise HTTPException(404,'Компания недоступна для жалобы')
            if body.need_id and not conn.execute('SELECT 1 FROM invitations WHERE candidate_id=? AND org_id=? AND need_id=?',(user['candidate_id'],body.org_id,body.need_id)).fetchone():raise HTTPException(404,'Приглашение недоступно для жалобы')
        existing=conn.execute("SELECT id,state FROM moderation_cases WHERE actor_id=? AND org_id=? AND COALESCE(need_id,'')=? AND kind=? AND message=? AND state='open'",(user['id'],body.org_id,body.need_id or '',body.kind,body.message)).fetchone()
        if existing:return {'id':existing['id'],'state':existing['state'],'reused':True}
        from .recruiting import datetime_before
        if conn.execute('SELECT COUNT(*) FROM moderation_cases WHERE actor_id=? AND created_at>?',(user['id'],datetime_before(86400))).fetchone()[0]>=20:raise HTTPException(429,'Достигнут дневной лимит жалоб. Уже отправленные обращения доступны в истории.')
        conn.execute('INSERT INTO moderation_cases VALUES(?,?,?,?,?,?,\'open\',NULL,?,NULL)',(identity,user['id'],body.org_id,body.need_id,body.kind,body.message,now()))
        audit(conn,user['id'],'complaint_created',identity)
    return {'id':identity,'state':'open'}

@router.get('/complaints')
def my_complaints(user=Depends(session_user)):
    with connection() as conn:
        query='SELECT c.*,o.name AS company,n.payload AS need_payload FROM moderation_cases c JOIN organizations o ON o.id=c.org_id LEFT JOIN employer_needs n ON n.id=c.need_id'
        rows=conn.execute(query+' ORDER BY c.created_at DESC,c.id DESC') if user['role']=='admin' else conn.execute(query+' WHERE c.actor_id=? ORDER BY c.created_at DESC,c.id DESC',(user['id'],))
        items=[]
        for row in rows:
            item=dict(row);need=item.pop('need_payload');item['need_title']=json.loads(need).get('title') if need else None
            items.append(item)
        return {'items':items}

class Resolution(StrictModel):
    state:str;resolution:str=Field(min_length=10,max_length=2000)
    @model_validator(mode='after')
    def meaningful_resolution(self):
        self.resolution=self.resolution.strip()
        if len(self.resolution)<10:raise ValueError('Обоснуйте решение: не менее 10 символов.')
        return self
@router.post('/admin/complaints/{identity}/resolve')
def resolve(identity:str,body:Resolution,user=Depends(session_user)):
    require_role(user,'admin')
    if body.state not in {'confirmed','dismissed'}:raise HTTPException(422,'Неизвестный результат')
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');row=conn.execute('SELECT * FROM moderation_cases WHERE id=?',(identity,)).fetchone()
        if not row:raise HTTPException(404,'Жалоба не найдена')
        if row['state']!='open':raise HTTPException(409,'По жалобе уже принято решение. Обновите список.')
        conn.execute('UPDATE moderation_cases SET state=?,resolution=?,resolved_at=? WHERE id=?',(body.state,body.resolution,now(),identity))
        if body.state=='confirmed' and row['need_id']:conn.execute("UPDATE employer_needs SET state='blocked',version=version+1 WHERE id=?",(row['need_id'],))
        audit(conn,user['id'],'complaint_resolved',identity,{'state':body.state})
        from .identity import notify
        notify(conn,row['actor_id'],'complaint',identity,'По вашей жалобе принято решение. Откройте историю жалоб, чтобы прочитать обоснование.')
    return {'ok':True}

class Block(StrictModel):org_id:str;blocked:bool
@router.post('/employer-blocks')
def block(body:Block,user=Depends(session_user)):
    require_role(user,'candidate')
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if not conn.execute('SELECT 1 FROM organizations WHERE id=?',(body.org_id,)).fetchone():raise HTTPException(404,'Компания не найдена')
        if body.blocked:
            from .integration_privacy import revoke_transfers
            revoke_transfers(conn,user['candidate_id'],body.org_id,'employer_blocked')
            conn.execute('INSERT OR IGNORE INTO employer_blocks VALUES(?,?,?)',(user['candidate_id'],body.org_id,now()))
            conn.execute('UPDATE contact_grants SET active=0,revoked_at=? WHERE candidate_id=? AND org_id=?',(now(),user['candidate_id'],body.org_id))
        else:conn.execute('DELETE FROM employer_blocks WHERE candidate_id=? AND org_id=?',(user['candidate_id'],body.org_id))
    return {'ok':True}
