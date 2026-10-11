"""Need-based matching and employer-initiated invitations independent of vacancies."""
import hashlib,json,secrets,time,os
from fastapi import APIRouter,Depends,HTTPException
from pydantic import Field,model_validator
from .auth import session_user,require_role,require_verified,require_org_role
from .db import connection,dumps,now,audit,bump
from .schemas import StrictModel
from .assessment_catalog import GRADES,SPECIALIZATIONS
from .privacy import eligible,public_profile,verified_employer
from .domain import candidate_from_row
from .trust import actionable,company,risk_signals,ensure_trusted
from .identity import notify
from .taxonomy import SKILLS

router=APIRouter(prefix='/api',tags=['Employer initiated recruiting'])
def digest(data):return hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def need_view(row):
    from .contact_policy import hiring_activity
    from .demo_stage03 import readable_need_title
    payload=json.loads(row['payload'])
    return dict(row,payload=payload,display_title=readable_need_title(payload),hiring_activity=hiring_activity(row))
def owned_need(conn,identity,user):
    row=conn.execute('SELECT * FROM employer_needs WHERE id=? AND org_id=?',(identity,user['org_id'])).fetchone()
    if not row:raise HTTPException(404,'Запрос не найден')
    return row

class Need(StrictModel):
    title:str=Field(min_length=3,max_length=140);description:str=Field(min_length=20,max_length=3000)
    team:str=Field(min_length=10,max_length=2000);specialization:str;grade:str
    skills:list[str]=Field(min_length=1,max_length=25)
    salary_min:int=Field(ge=1,le=5000000);salary_max:int=Field(ge=1,le=5000000)
    salary_type:str='net';work_mode:str='remote';city:str=Field(default='',max_length=80)
    employer_contact:str=Field(min_length=5,max_length=200)
    valid_days:int=Field(default=30,ge=1,le=90)
    @model_validator(mode='after')
    def valid(self):
        from .taxonomy import SKILLS
        if self.specialization not in SPECIALIZATIONS or self.grade not in GRADES or any(s not in SKILLS for s in self.skills):raise ValueError('Неизвестная категория или навык')
        if self.salary_max<self.salary_min or self.salary_type not in {'net','gross'} or self.work_mode not in {'remote','hybrid','office'}:raise ValueError('Некорректные условия')
        if self.work_mode!='remote' and not self.city:raise ValueError('Укажите город')
        return self

@router.get('/needs')
def needs(user=Depends(session_user)):
    require_role(user,'employer')
    with connection() as conn:return {'items':[need_view(r) for r in conn.execute('SELECT * FROM employer_needs WHERE org_id=? ORDER BY created_at DESC,id DESC',(user['org_id'],))]}

@router.post('/needs',status_code=201)
def create_need(body:Need,user=Depends(session_user)):
    require_org_role(user,'owner','recruiter');require_verified(user)
    identity='need_'+secrets.token_hex(10);payload=body.model_dump();payload.update({'currency':'RUB','period':'month','source':'platform','last_verified_at':now(),'risk_signals':risk_signals(body.description+' '+body.employer_contact)})
    with connection() as conn:
        record=company(conn,user['org_id']);payload['company']=record['name']
        state='active' if record['verification'] in {'verified','demo_verified'} and not payload['risk_signals'] else 'pending'
        conn.execute('INSERT INTO employer_needs(id,org_id,payload,state,expires,created_at) VALUES(?,?,?,?,?,?)',(identity,user['org_id'],dumps(payload),state,time.time()+body.valid_days*86400,now()))
        audit(conn,user['id'],'need_created',identity,{'state':state});bump(conn)
        return need_view(owned_need(conn,identity,user))

class NeedUpdate(Need):version:int=Field(ge=1)
@router.put('/needs/{identity}')
def update_need(identity:str,body:NeedUpdate,user=Depends(session_user)):
    require_org_role(user,'owner','recruiter');require_verified(user)
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');row=owned_need(conn,identity,user)
        if row['version']!=body.version:raise HTTPException(409,'Запрос изменён')
        if row['state']=='blocked':raise HTTPException(403,'Запрос заблокирован; подайте апелляцию')
        payload=body.model_dump(exclude={'version'});record=company(conn,user['org_id']);signals=risk_signals(body.description+' '+body.employer_contact)
        payload.update({'company':record['name'],'currency':'RUB','period':'month','source':'platform','last_verified_at':now(),'risk_signals':signals})
        state='active' if record['verification'] in {'verified','demo_verified'} and not signals else 'pending'
        conn.execute('UPDATE employer_needs SET payload=?,state=?,expires=?,version=version+1 WHERE id=?',(dumps(payload),state,time.time()+body.valid_days*86400,identity));bump(conn)
        return need_view(owned_need(conn,identity,user))

@router.post('/needs/{identity}/close')
def close_need(identity:str,user=Depends(session_user)):
    require_org_role(user,'owner','recruiter')
    with connection() as conn:
        owned_need(conn,identity,user);conn.execute("UPDATE employer_needs SET state='closed',version=version+1 WHERE id=?",(identity,));bump(conn);audit(conn,user['id'],'need_closed',identity)
    return {'ok':True}

class NeedReview(StrictModel):state:str;reason:str=Field(min_length=10,max_length=1000);version:int=Field(ge=1)
@router.post('/admin/needs/{identity}/review')
def review_need(identity:str,body:NeedReview,user=Depends(session_user)):
    require_role(user,'admin')
    if body.state not in {'active','blocked','closed'}:raise HTTPException(422,'Неизвестное состояние')
    with connection() as conn:
        row=conn.execute('SELECT * FROM employer_needs WHERE id=?',(identity,)).fetchone()
        if not row:raise HTTPException(404,'Запрос не найден')
        if row['version']!=body.version:raise HTTPException(409,'Запрос изменился')
        if body.state=='active' and company(conn,row['org_id'])['verification'] not in {'verified','demo_verified'}:raise HTTPException(409,'Сначала подтвердите компанию')
        payload=json.loads(row['payload']);payload['moderation']={'reason':body.reason,'at':now()}
        conn.execute('UPDATE employer_needs SET state=?,payload=?,version=version+1 WHERE id=?',(body.state,dumps(payload),identity));audit(conn,user['id'],'need_reviewed',identity,{'state':body.state})
    return {'ok':True}

def match_need_v1(conn,candidate,need,filters,cert_rows=None):
    payload=json.loads(need['payload']);rows=cert_rows if cert_rows is not None else conn.execute('SELECT * FROM grade_certifications WHERE candidate_id=? AND active=1',(candidate['id'],))
    certs=[dict(r,payload=json.loads(r['payload'])) for r in rows]
    cert=next((c for c in certs if c['specialization']==payload['specialization'] and (c['grade']==payload['grade'] or filters.get('adjacent_grades') and abs(GRADES.index(c['grade'])-GRADES.index(payload['grade']))==1)),None)
    if not cert:return None
    blocked=[];unknown=[]
    if payload['work_mode'] not in candidate.get('work_modes',[]):blocked.append('Формат работы')
    if payload['salary_type']=='net' and candidate.get('salary_min') is not None and candidate['salary_min']>payload['salary_max']:blocked.append('Зарплата')
    elif candidate.get('salary_min') is None:unknown.append('Зарплатные ожидания')
    if payload['work_mode']!='remote' and candidate.get('city')!=payload['city'] and not candidate.get('relocate'):blocked.append('Город')
    if blocked:return None
    tested=set(cert['payload'].get('tested_skills',[]))
    skills=set(candidate.get('all_skills',[]));coverage=len(skills&set(payload['skills']))/len(payload['skills'])
    requested=set(filters.get('skills',[]))
    if requested-skills:return None
    proofs=[a for a in candidate.get('achievements',[]) if a['active'] and a.get('individual_evidence')]
    if filters.get('evidence_only') and not proofs:return None
    # FSP is bounded; evidence cannot compensate for a missing tested category.
    evidence=min(5,len(proofs));score=round(.7*cert['score']+25*coverage+evidence,2)
    matrix=[{'skill':s,'label':SKILLS[s][0] if s in SKILLS else s,'status':'tested_in_session' if s in tested else 'source' if any(s in a.get('task_skills',[]) for a in proofs) else 'claimed' if s in skills else 'unknown'} for s in payload['skills']]
    return {'score':score,'certification':{k:cert[k] for k in ['id','specialization','grade','score','status','created_at']},'calibration':cert['payload']['calibration'],'matrix':matrix,'unknown':unknown,'fsp_bonus':evidence,'explanation':[f"Категория: {SPECIALIZATIONS[cert['specialization']]['label']} {cert['grade']}",f"Результат объективных заданий: {cert['score']}%",f"Требования со сведениями: {round(coverage*100)}%",'Есть индивидуальные сведения ФСП' if proofs else 'Истории ФСП нет; участие доступно'],'algorithm':'category-evidence-v1'}

def match_need(conn,candidate,need,filters,cert_rows=None,policy=None,reference_time=None,user=None):
    import os
    selected_policy=policy or os.environ.get('FSP_MATCH_POLICY','category-evidence-v2')
    if selected_policy=='category-evidence-v1':return match_need_v1(conn,candidate,need,filters,cert_rows)
    if selected_policy!='category-evidence-v2':raise ValueError('Unknown match policy')
    from .evidence_ranking import select_certificate,score as evidence_score
    rows=list(cert_rows) if cert_rows is not None else list(conn.execute('SELECT * FROM grade_certifications WHERE candidate_id=? AND active=1',(candidate['id'],)))
    payload=json.loads(need['payload']);chosen=select_certificate(rows,payload,filters,reference_time)
    if not chosen:return None
    cert_payload=json.loads(chosen[0]['payload']) if isinstance(chosen[0]['payload'],str) else chosen[0]['payload']
    known=set(candidate.get('all_skills',[]))|set(cert_payload.get('tested_skills',[]))
    for achievement in candidate.get('achievements',[]):
        if achievement.get('active') and achievement.get('individual_evidence'):known.update(achievement.get('task_skills',[]))
    # Omitting a skill from self-description must not erase a recorded observation.
    gate_candidate=dict(candidate,all_skills=sorted(known))
    baseline=match_need_v1(conn,gate_candidate,need,filters,chosen)
    if not baseline:return None
    from .certification_evidence import evidence
    result=evidence_score(candidate,payload,chosen[0],baseline,reference_time)
    result['assessment']=evidence(conn,chosen[0],user)
    return result

class Search(StrictModel):
    need_id:str;session_id:str|None=None;version:int|None=None
    skills:list[str]=Field(default_factory=list,max_length=25);evidence_only:bool=False;adjacent_grades:bool=False
    limit:int=Field(default=50,ge=1,le=100)
    offset:int=Field(default=0,ge=0,le=100000)

def search_items(conn,need,filters,user,with_page=False):
    # Batch ranking inputs. Rights are checked again on every emitted profile.
    cert_rows={};achievements={};consents={}
    from .certification_validity import usable_certificate
    for row in conn.execute('SELECT * FROM grade_certifications WHERE active=1'):
        if not usable_certificate(row):continue
        cert_rows.setdefault(row['candidate_id'],[]).append(row)
    for row in conn.execute("SELECT * FROM achievements ORDER BY json_extract(payload,'$.date') DESC"):
        achievements.setdefault(row['candidate_id'],[]).append(dict(json.loads(row['payload']),active=bool(row['active']),version=row['version']))
    for row in conn.execute('SELECT candidate_id,kind,granted FROM consent_events ORDER BY created_at DESC,id DESC'):
        consents.setdefault((row['candidate_id'],row['kind']),bool(row['granted']))
    verified={r['candidate_id'] for r in conn.execute("SELECT candidate_id FROM users WHERE active=1 AND (email_verified=1 OR auth_kind='username') AND candidate_id IS NOT NULL")}
    blocked={r['candidate_id'] for r in conn.execute('SELECT candidate_id FROM employer_blocks WHERE org_id=?',(user['org_id'],))}
    org=conn.execute('SELECT payload FROM company_profiles WHERE org_id=?',(user['org_id'],)).fetchone();sandbox=bool(org and json.loads(org[0]).get('sandbox'))
    material={'cert_rows':cert_rows,'achievements':achievements};ranked=[]
    for row in conn.execute('SELECT * FROM candidates WHERE visible=1 AND NOT EXISTS (SELECT 1 FROM users WHERE users.candidate_id=candidates.id AND users.active=0)'):
        candidate=candidate_from_row(row,conn,material);identity=candidate['id']
        from .db import DEMO
        if candidate.get('demo') and not DEMO:continue
        if not candidate.get('looking',True) or not candidate.get('assessment_onboarding_done'):continue
        if candidate.get('sandbox_org_id') and candidate['sandbox_org_id']!=user['org_id'] or sandbox and candidate.get('sandbox_org_id')!=user['org_id']:continue
        if identity not in verified or identity in blocked or not consents.get((identity,'processing')) or not consents.get((identity,'publication')) or not cert_rows.get(identity):continue
        match=match_need(conn,candidate,need,filters,cert_rows[identity])
        if match:ranked.append((match['score'],identity))
    ranked.sort(key=lambda x:(-x[0],x[1]));items=[]
    offset=filters.get('offset',0);limit=filters.get('limit',50)
    for _,identity in ranked[offset:offset+limit]:
        row=conn.execute('SELECT * FROM candidates WHERE id=?',(identity,)).fetchone()
        if not row or not row['visible']:continue
        candidate=candidate_from_row(row,conn)
        if not eligible(conn,candidate,user.get('org_id')) or conn.execute('SELECT 1 FROM employer_blocks WHERE candidate_id=? AND org_id=?',(identity,user['org_id'])).fetchone():continue
        match=match_need(conn,candidate,need,filters,user=user)
        if match:items.append({'candidate':public_profile(candidate,user,conn),'match':match})
    page={'offset':offset,'limit':limit,'total_at_snapshot':len(ranked),'has_more':offset+limit<len(ranked)}
    return {'items':items,'page':page} if with_page else items

@router.post('/search-sessions',status_code=201)
def search(body:Search,user=Depends(session_user)):
    verified_employer(user)
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');need=owned_need(conn,body.need_id,user);filters=body.model_dump(exclude={'need_id','session_id','version'})
        page=search_items(conn,need,filters,user,True);items=page['items']
        frame={'filters':filters,'candidate_ids':[x['candidate']['id'] for x in items],'need_version':need['version'],'revision':conn.execute("SELECT value FROM meta WHERE key='data_revision'").fetchone()[0],'created_at':now()}
        identity=body.session_id or 'search_'+secrets.token_hex(10)
        if body.session_id:
            existing=conn.execute('SELECT * FROM search_sessions WHERE id=? AND org_id=?',(identity,user['org_id'])).fetchone()
            if not existing:raise HTTPException(404,'Поисковая сессия не найдена')
            if existing['need_id']!=body.need_id:raise HTTPException(409,'Создайте новую сессию для другого запроса')
            if existing['version']!=body.version:raise HTTPException(409,'Поиск изменён; обновите сессию')
            payload=json.loads(existing['payload']);payload['history'].append(frame)
            conn.execute('UPDATE search_sessions SET payload=?,version=version+1 WHERE id=?',(dumps(payload),identity))
        else:conn.execute('INSERT INTO search_sessions(id,org_id,actor_id,need_id,payload,created_at) VALUES(?,?,?,?,?,?)',(identity,user['org_id'],user['id'],body.need_id,dumps({'history':[frame]}),now()))
        version=conn.execute('SELECT version FROM search_sessions WHERE id=?',(identity,)).fetchone()[0]
    return {'id':identity,'version':version,'items':items,'page':page['page'],'need':need_view(need),'filters':filters,'algorithm':os.environ.get('FSP_MATCH_POLICY','category-evidence-v2')}

@router.get('/search-sessions/{identity}')
def search_history(identity:str,user=Depends(session_user)):
    verified_employer(user)
    with connection() as conn:
        row=conn.execute('SELECT * FROM search_sessions WHERE id=? AND org_id=?',(identity,user['org_id'])).fetchone()
        if not row:raise HTTPException(404,'Поисковая сессия не найдена')
        payload=json.loads(row['payload']);filters=payload['history'][-1]['filters']
        need=owned_need(conn,row['need_id'],user)
        return dict(row,payload=payload,filters=filters,need=need_view(need),**search_items(conn,need,filters,user,True),algorithm=os.environ.get('FSP_MATCH_POLICY','category-evidence-v2'))

def passport(conn,candidate,need,user):
    match=match_need(conn,candidate,need,{})
    if not match:raise HTTPException(409,'Категория или условия не соответствуют запросу')
    snapshot={'schema_version':'need-passport-v1','candidate':{k:candidate.get(k) for k in ['id','name','title','version']},'need':{**json.loads(need['payload']),'id':need['id'],'version':need['version']},'match':match,'profile_sources':[{'id':a['id'],'version':a['version'],'active':a['active']} for a in candidate.get('achievements',[])],'demo':bool(candidate.get('demo'))}
    from .privacy import redact
    snapshot['candidate']=redact(snapshot['candidate'])
    # Employer's own contact is part of an invitation, never the candidate's.
    fingerprint=digest({'candidate_version':candidate['version'],'need_version':need['version'],'certification':match['certification'],'sources':snapshot['profile_sources'],'algorithm':match['algorithm']})
    identity='np_'+secrets.token_hex(10);checksum=digest(snapshot)
    conn.execute('INSERT INTO need_passports VALUES(?,?,?,?,?,?,?,?)',(identity,user['org_id'],candidate['id'],need['id'],dumps(snapshot),fingerprint,checksum,now()))
    return identity,snapshot

def invitation_view(row):
    from .contact_policy import contact_state
    from .demo_stage03 import readable_need_title
    payload=json.loads(row['payload'])
    return dict(row,payload=payload,need_title=readable_need_title(payload['need']),history=json.loads(row['history']),contact_progress=contact_state(row))
class Invitation(StrictModel):
    need_id:str;candidate_id:str;message:str=Field(min_length=10,max_length=2000)
    idempotency_key:str=Field(min_length=12,max_length=100)
    next_step:str=Field(default='Короткая встреча о задачах команды и условиях работы.',min_length=10,max_length=300)
    response_days:int=Field(default=3,ge=1,le=14)
    @model_validator(mode='after')
    def meaningful_step(self):
        self.next_step=self.next_step.strip()
        if len(self.next_step)<10:raise ValueError('Опишите понятный следующий шаг после принятия.')
        return self

@router.post('/invitations',status_code=201)
def invite(body:Invitation,user=Depends(session_user)):
    require_org_role(user,'owner','recruiter');require_verified(user)
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');existing=conn.execute('SELECT * FROM invitations WHERE sender_id=? AND request_key=?',(user['id'],body.idempotency_key)).fetchone()
        if existing:
            saved=json.loads(existing['payload']);plan=saved.get('contact_plan',{})
            if existing['need_id']!=body.need_id or existing['candidate_id']!=body.candidate_id or saved['message']!=body.message or plan and (plan['next_step']!=body.next_step or plan['response_days']!=body.response_days):raise HTTPException(409,'Ключ уже использован для другого приглашения')
            return invitation_view(existing)
        need=owned_need(conn,body.need_id,user);record=actionable(conn,need,user)
        from .contact_policy import hiring_activity
        activity=hiring_activity(need)
        if activity['status']!='current':raise HTTPException(409,'Подтвердите актуальность найма перед новым приглашением.')
        if risk_signals(body.message+' '+body.next_step):raise HTTPException(422,'Приглашение требует модерации: подозрительные условия или ссылка')
        row=conn.execute('SELECT * FROM candidates WHERE id=?',(body.candidate_id,)).fetchone()
        if not row:raise HTTPException(404,'Кандидат недоступен')
        candidate=candidate_from_row(row,conn)
        if not eligible(conn,candidate,user.get('org_id')):raise HTTPException(404,'Кандидат недоступен')
        if conn.execute('SELECT 1 FROM employer_blocks WHERE candidate_id=? AND org_id=?',(body.candidate_id,user['org_id'])).fetchone():raise HTTPException(403,'Контакт недоступен')
        count=sum(conn.execute('SELECT COUNT(*) FROM '+table+' WHERE org_id=? AND created_at>?',(user['org_id'],datetime_before(86400))).fetchone()[0] for table in ['invitations','offers'])
        if count>=30:raise HTTPException(429,'Достигнут дневной лимит приглашений')
        passport_id,snapshot=passport(conn,candidate,need,user)
        conditions=json.loads(need['payload']);identity='inv_'+secrets.token_hex(10);timestamp=now()
        payload={'need':conditions,'company':record['name'],'company_verification':record['verification'],'message':body.message,'salary_min':conditions['salary_min'],'salary_max':conditions['salary_max'],'salary_type':conditions['salary_type'],'currency':'RUB','period':'month','employer_contact':conditions['employer_contact'],'candidate_name':snapshot['candidate']['name'],'passport_id':passport_id,'match_snapshot':snapshot}
        payload['hiring_activity']=activity
        payload['contact_plan']={'next_step':body.next_step,'response_days':body.response_days,'basis':'employer_commitment'}
        history=[{'status':'sent','at':timestamp,'actor':'employer','message':body.message}]
        conn.execute('INSERT INTO invitations(id,org_id,candidate_id,need_id,sender_id,payload,status,history,created_at,request_key) VALUES(?,?,?,?,?,?,\'sent\',?,?,?)',(identity,user['org_id'],candidate['id'],need['id'],user['id'],dumps(payload),dumps(history),timestamp,body.idempotency_key))
        recipient=conn.execute('SELECT id FROM users WHERE candidate_id=?',(candidate['id'],)).fetchone()
        if recipient:notify(conn,recipient[0],'invitation',identity,'Получено приглашение с условиями работы')
        audit(conn,user['id'],'invitation_sent',identity,{'need_id':need['id'],'candidate_id':candidate['id']})
        return invitation_view(conn.execute('SELECT * FROM invitations WHERE id=?',(identity,)).fetchone())

def datetime_before(seconds):
    from datetime import datetime,timezone,timedelta
    return (datetime.now(timezone.utc)-timedelta(seconds=seconds)).isoformat()

@router.get('/invitations')
def invitations(user=Depends(session_user)):
    require_role(user,'candidate','employer')
    condition,identity=('candidate_id',user['candidate_id']) if user['role']=='candidate' else ('org_id',user['org_id'])
    with connection() as conn:
        items=[]
        for row in conn.execute('SELECT * FROM invitations WHERE '+condition+'=? ORDER BY created_at DESC',(identity,)):
            value=invitation_view(row)
            stage=conn.execute('SELECT r.stage,r.updated_at,c.provider FROM recruitment_stages r JOIN ats_connections c ON c.id=r.connection_id WHERE r.invitation_id=?',(row['id'],)).fetchone()
            value['recruitment_stage']=dict(stage) if stage else None
            value['contact_progress']['can_record']=bool(row['status']=='accepted' and conn.execute('SELECT 1 FROM contact_grants WHERE candidate_id=? AND org_id=? AND active=1',(row['candidate_id'],row['org_id'])).fetchone())
            items.append(value)
        return {'items':items}

def own_invitation(conn,identity,user):
    row=conn.execute('SELECT * FROM invitations WHERE id=?',(identity,)).fetchone()
    if not row or not (user['role']=='candidate' and row['candidate_id']==user.get('candidate_id') or user['role']=='employer' and row['org_id']==user.get('org_id')):raise HTTPException(404,'Приглашение не найдено')
    return row

class Transition(StrictModel):action:str;version:int=Field(ge=1);message:str=Field(default='',max_length=1000)
@router.post('/invitations/{identity}/respond')
def transition(identity:str,body:Transition,user=Depends(session_user)):
    require_role(user,'candidate','employer')
    if user['role']=='employer':require_org_role(user,'owner','recruiter')
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');row=own_invitation(conn,identity,user)
        allowed={'viewed','accepted','declined','clarification'} if user['role']=='candidate' else {'replied','withdrawn'}
        if body.action not in allowed:raise HTTPException(403,'Действие недоступно роли')
        if row['version']!=body.version:raise HTTPException(409,'Статус изменён; обновите список')
        if row['status'] not in {'sent','viewed','clarification'}:raise HTTPException(409,'Приглашение завершено')
        if body.action in {'clarification','replied'} and not body.message:raise HTTPException(422,'Введите сообщение')
        if body.action=='accepted':
            if conn.execute('SELECT 1 FROM employer_blocks WHERE candidate_id=? AND org_id=?',(row['candidate_id'],row['org_id'])).fetchone():raise HTTPException(403,'Компания заблокирована кандидатом')
            need=conn.execute('SELECT * FROM employer_needs WHERE id=?',(row['need_id'],)).fetchone()
            if need['state']!='active' or need['expires']<time.time() or company(conn,row['org_id'])['verification'] not in {'verified','demo_verified'}:raise HTTPException(409,'Условия больше не актуальны')
            from .contact_policy import hiring_activity
            if hiring_activity(need)['status']!='current':raise HTTPException(409,'Работодатель должен повторно подтвердить актуальность найма.')
            from .privacy import consent
            if not consent(conn,row['candidate_id'],'processing'):raise HTTPException(403,'Согласие на обработку отозвано')
            conn.execute('INSERT INTO contact_grants VALUES(?,?,?,?,1,?,NULL)',('grant_'+secrets.token_hex(10),row['candidate_id'],row['org_id'],identity,now()))
        from .privacy import scrub
        text=scrub(body.message) if user['role']=='candidate' and body.action!='accepted' else body.message
        history=json.loads(row['history']);history.append({'status':body.action,'at':now(),'actor':user['role'],'message':text})
        status=row['status'] if body.action=='replied' else body.action
        conn.execute('UPDATE invitations SET status=?,history=?,version=version+1 WHERE id=?',(status,dumps(history),identity))
        recipient=row['sender_id'] if user['role']=='candidate' else conn.execute('SELECT id FROM users WHERE candidate_id=?',(row['candidate_id'],)).fetchone()[0]
        notify(conn,recipient,'invitation_status',identity,'Изменён статус приглашения');audit(conn,user['id'],'invitation_'+body.action,identity)
        return invitation_view(conn.execute('SELECT * FROM invitations WHERE id=?',(identity,)).fetchone())

@router.get('/need-passports/{identity}')
def read_passport(identity:str,user=Depends(session_user)):
    with connection() as conn:
        row=conn.execute('SELECT * FROM need_passports WHERE id=?',(identity,)).fetchone()
        if not row or not (user['role']=='employer' and row['org_id']==user.get('org_id') or user['role']=='candidate' and row['candidate_id']==user.get('candidate_id')):raise HTTPException(404,'Паспорт не найден')
        cr=conn.execute('SELECT * FROM candidates WHERE id=?',(row['candidate_id'],)).fetchone();need=conn.execute('SELECT * FROM employer_needs WHERE id=?',(row['need_id'],)).fetchone()
        snapshot=json.loads(row['snapshot']);current=match_need(conn,candidate_from_row(cr,conn),need,{})
        sources=[{'id':a['id'],'version':a['version'],'active':a['active']} for a in candidate_from_row(cr,conn).get('achievements',[])]
        stale=not current or snapshot['match'].get('algorithm')!=current.get('algorithm') or snapshot['candidate']['version']!=cr['version'] or snapshot['need']['version']!=need['version'] or snapshot['match']['certification']['id']!=current['certification']['id'] or snapshot['profile_sources']!=sources
        return {'id':identity,'snapshot':snapshot,'integrity_valid':digest(snapshot)==row['integrity_sha256'],'integrity_sha256':row['integrity_sha256'],'stale':bool(stale)}
