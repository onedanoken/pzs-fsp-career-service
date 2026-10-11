import json,secrets,time,os
from datetime import datetime,timezone
from fastapi import APIRouter,Depends,HTTPException
from pydantic import Field
from .auth import session_user,require_role,require_verified
from .db import connection,dumps,now,audit,bump
from .schemas import StrictModel
from .assessment_catalog import GRADES,SPECIALIZATIONS,SOURCES,POLICY
from .item_generator import generate,public_item,grade_answer
from .jobs import enqueue,JobError

router=APIRouter(prefix='/api',tags=['Competency assessments'])
def owned(conn,identity,user):
    row=conn.execute('SELECT * FROM assessment_sessions WHERE id=? AND candidate_id=?',(identity,user['candidate_id'])).fetchone()
    if not row:raise HTTPException(404,'Сессия не найдена')
    return row

def view(conn,row):
    payload=json.loads(row['payload']);result={k:row[k] for k in ['id','specialization','grade','mode','status','version','created_at','expires','completed_at']}
    result.update({'items':[public_item(i) for i in payload.get('items',[])],'generator':payload.get('generator'),'calibration':payload.get('calibration'),'result':payload.get('result'),'technical_error':payload.get('technical_error')})
    result['generation']={k:payload.get(k) for k in ['generator','generator_error','neural_items','total_items','model','model_revision','quantization','generator_scope']}
    from .assessment_coverage import coverage
    result['coverage']=coverage(row['specialization'],row['grade'],payload.get('items',[]),payload.get('result'),payload.get('coverage'))
    result['answers']={r['item_id']:r['answer'] for r in conn.execute('SELECT * FROM assessment_answers WHERE session_id=?',(row['id'],))}
    result['assessment_path']=payload.get('assessment_path')
    result['assembly_plan']=payload.get('assembly_plan')
    result['can_answer']=row['status']=='active' and row['expires']>time.time()
    if payload.get('assessment_path'):
        from .skill_passports import view as passport_view
        passport=conn.execute('SELECT * FROM assessment_passports WHERE session_id=? ORDER BY revision DESC LIMIT 1',(row['id'],)).fetchone()
        result['skill_passport']=passport_view(conn,passport,{'role':'candidate','candidate_id':row['candidate_id']}) if passport else None
    if result['result']:
        from .competency_feedback import feedback
        result['result']=dict(result['result'],competency_feedback=feedback(result['result']))
    return result

@router.get('/assessments/catalog')
def catalog():
    from .work_samples import RUBRIC
    path=os.environ.get('FSP_ASSESSMENT_PATH','work_sample_v3_balanced')
    policy=dict(POLICY)
    if path=='work_sample_v3_balanced':policy.update(version='grade-policy-v3-balanced',review_required=False,core_floor=50,screening_count=9,work_checks_required=['original','transfer'])
    elif path=='work_sample_v2_auto':policy.update(version='grade-policy-v2-auto',review_required=False)
    return {'specializations':SPECIALIZATIONS,'grades':GRADES,'policy':policy,'sources':SOURCES,'assessment_path':{'enabled':path!='legacy','version':path,'review_required':path=='work_sample_v1','screening_count':9 if path=='work_sample_v3_balanced' else 3,'transfer_required':path=='work_sample_v3_balanced','rubric':RUBRIC}}

@router.get('/assessments')
def sessions(user=Depends(session_user)):
    require_role(user,'candidate')
    with connection() as conn:return {'items':[view(conn,r) for r in conn.execute('SELECT * FROM assessment_sessions WHERE candidate_id=? ORDER BY created_at DESC',(user['candidate_id'],))],'certifications':[dict(r,payload=json.loads(r['payload'])) for r in conn.execute('SELECT * FROM grade_certifications WHERE candidate_id=? ORDER BY created_at DESC',(user['candidate_id'],))]}

class Start(StrictModel):
    specialization:str;grade:str;mode:str='certification';industry:str=Field(default='IT',max_length=80)
    tools_allowed:bool=True

@router.post('/assessments',status_code=202)
def start(body:Start,user=Depends(session_user)):
    require_role(user,'candidate');require_verified(user)
    if body.specialization not in SPECIALIZATIONS or body.grade not in GRADES or body.mode not in {'certification','practice'}:raise HTTPException(422,'Неизвестная специализация, уровень или режим')
    from .privacy import consent
    identity='assessment_'+secrets.token_hex(10)
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if not consent(conn,user['candidate_id'],'processing'):raise HTTPException(403,'Требуется согласие на обработку')
        active=conn.execute("SELECT id FROM assessment_sessions WHERE candidate_id=? AND status IN ('generating','active','grading') AND expires>?",(user['candidate_id'],time.time())).fetchone()
        if active:raise HTTPException(409,'Завершите или отмените текущую сессию')
        if body.mode=='certification':
            pending=conn.execute("SELECT payload FROM assessment_sessions WHERE candidate_id=? AND specialization=? AND mode='certification' AND status='completed'",(user['candidate_id'],body.specialization)).fetchall()
            if os.environ.get('FSP_ASSESSMENT_PATH','work_sample_v3_balanced')=='work_sample_v1' and any((json.loads(x[0]).get('result') or {}).get('assessment_path',{}).get('automatic_passed') and (json.loads(x[0]).get('result') or {}).get('assessment_path',{}).get('review_status')=='pending' for x in pending):raise HTTPException(409,'Дождитесь разбора предыдущей аттестации этого направления. Тренировка доступна сейчас.')
            cert=conn.execute('SELECT * FROM grade_certifications WHERE candidate_id=? AND specialization=? AND active=1 ORDER BY created_at DESC LIMIT 1',(user['candidate_id'],body.specialization)).fetchone()
            profile=json.loads(conn.execute('SELECT payload FROM candidates WHERE id=?',(user['candidate_id'],)).fetchone()[0])
            if cert and profile.get('assessment_onboarding_done') and not json.loads(cert['payload']).get('demo'):
                elapsed=(datetime.now(timezone.utc)-datetime.fromisoformat(cert['created_at'])).total_seconds()
                if elapsed<POLICY['change_days']*86400:raise HTTPException(409,'Следующий переход уровня доступен через 90 дней; тренировка доступна сейчас')
            last=conn.execute("SELECT created_at FROM assessment_sessions WHERE candidate_id=? AND specialization=? AND mode='certification' AND status='completed' ORDER BY created_at DESC LIMIT 1",(user['candidate_id'],body.specialization)).fetchone()
            # First level search may try adjacent grades; repeat identical attempts have a cooldown.
            repeated=conn.execute("SELECT created_at FROM assessment_sessions WHERE candidate_id=? AND specialization=? AND grade=? AND mode='certification' AND status='completed' ORDER BY created_at DESC LIMIT 1",(user['candidate_id'],body.specialization,body.grade)).fetchone()
            if repeated and (datetime.now(timezone.utc)-datetime.fromisoformat(repeated[0])).total_seconds()<86400:raise HTTPException(429,'Повтор этого уровня доступен через 24 часа')
        payload={'industry':body.industry,'tools_allowed':body.tools_allowed,'items':[],'calibration':'pilot_unvalidated'}
        path=os.environ.get('FSP_ASSESSMENT_PATH','work_sample_v3_balanced')
        if path not in {'work_sample_v1','work_sample_v2_auto','work_sample_v3_balanced','legacy'}:raise HTTPException(503,'Проверка временно недоступна')
        if path in {'work_sample_v1','work_sample_v2_auto','work_sample_v3_balanced'}:
            payload.update(assessment_path_version=path,policy_snapshot=dict(POLICY))
            if path in {'work_sample_v2_auto','work_sample_v3_balanced'}:payload['policy_snapshot'].update(version='grade-policy-v2-auto' if path=='work_sample_v2_auto' else 'grade-policy-v3-balanced',review_required=False)
            if path=='work_sample_v3_balanced':payload['policy_snapshot'].update(core_floor=50,screening_count=9,work_checks_required=['original','transfer'])
        conn.execute('INSERT INTO assessment_sessions(id,candidate_id,specialization,grade,mode,status,payload,created_at,expires) VALUES(?,?,?,?,?,\'generating\',?,?,?)',(identity,user['candidate_id'],body.specialization,body.grade,body.mode,dumps(payload),now(),time.time()+3600))
        job=enqueue('assessment.generate',{'session_id':identity},user['id'],identity,conn=conn)
        audit(conn,user['id'],'assessment_started',identity,{'specialization':body.specialization,'grade':body.grade,'mode':body.mode})
    return {'id':identity,'job_id':job,'status':'generating'}

@router.post('/assessments/onboarding/complete')
def complete_onboarding(user=Depends(session_user)):
    require_role(user,'candidate');require_verified(user)
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        from .certification_validity import usable_certificate
        if not any(usable_certificate(row) for row in conn.execute('SELECT * FROM grade_certifications WHERE candidate_id=? AND active=1',(user['candidate_id'],))):raise HTTPException(409,'Сначала подтвердите хотя бы один уровень')
        row=conn.execute('SELECT * FROM candidates WHERE id=?',(user['candidate_id'],)).fetchone();profile=json.loads(row['payload']);profile['assessment_onboarding_done']=True
        conn.execute('UPDATE candidates SET payload=?,version=version+1 WHERE id=?',(dumps(profile),user['candidate_id']));bump(conn)
        audit(conn,user['id'],'assessment_onboarding_completed',user['candidate_id'])
    return {'ok':True}

def process_generation(context,payload):
    with connection() as conn:row=conn.execute('SELECT * FROM assessment_sessions WHERE id=?',(payload['session_id'],)).fetchone()
    for attempt in range(5):
        if row['specialization']=='qa' and json.loads(row['payload']).get('assessment_path_version') in {'work_sample_v2_auto','work_sample_v3_balanced'}:
            variants=generate(row['specialization'],row['grade'],allow_neural=attempt==0 and json.loads(row['payload']).get('assessment_path_version')!='work_sample_v3_balanced',qa_professional=True)
        else:variants=generate(row['specialization'],row['grade'],allow_neural=attempt==0 and json.loads(row['payload']).get('assessment_path_version')!='work_sample_v3_balanced')
        if json.loads(row['payload']).get('assessment_path_version') in {'work_sample_v1','work_sample_v2_auto','work_sample_v3_balanced'}:
            from .work_samples import add_journey
            variants=add_journey(variants,row['specialization'],row['grade'],json.loads(row['payload'])['assessment_path_version'])
        with connection() as conn:
            conn.execute('BEGIN IMMEDIATE');context.check(conn)
            current=conn.execute('SELECT * FROM assessment_sessions WHERE id=?',(row['id'],)).fetchone()
            if current['status']!='generating':return {'skipped':True}
            recent={json.loads(r[0]).get('condition_signature') for r in conn.execute("SELECT payload FROM assessment_sessions WHERE specialization=? AND id<>? ORDER BY created_at DESC,id DESC LIMIT 1000",(row['specialization'],row['id']))}
            legacy_signature=variants.get('legacy_condition_signature')
            if variants['condition_signature'] in recent or legacy_signature and legacy_signature in recent:continue
            original=json.loads(current['payload']);original.update(variants)
            from .assessment_coverage import coverage
            original['coverage']=coverage(current['specialization'],current['grade'],variants['items'])
            conn.execute("UPDATE assessment_sessions SET payload=?,status='active',version=version+1,expires=? WHERE id=? AND status='generating'",(dumps(original),time.time()+2700,row['id']))
            return {'session_id':row['id'],'items':len(variants['items']),'generator':variants['generator']}
    raise JobError('variant_pool_collision','Не удалось подобрать новый вариант. Попытка не оценивается; начните новую сессию.',retryable=False)

@router.get('/assessments/{identity}')
def read(identity:str,user=Depends(session_user)):
    require_role(user,'candidate')
    with connection() as conn:return view(conn,owned(conn,identity,user))

class Answer(StrictModel):item_id:str;answer:str=Field(max_length=5000);version:int=Field(ge=1)
@router.put('/assessments/{identity}/answer')
def save_answer(identity:str,body:Answer,user=Depends(session_user)):
    require_role(user,'candidate')
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');row=owned(conn,identity,user)
        if row['status']!='active' or row['expires']<time.time():raise HTTPException(409,'Сессия не принимает ответы')
        if row['version']!=body.version:raise HTTPException(409,'Ответ изменён в другой вкладке; обновите сессию')
        if body.item_id not in {x['id'] for x in json.loads(row['payload'])['items']}:raise HTTPException(422,'Неизвестное задание')
        conn.execute('INSERT INTO assessment_answers(session_id,item_id,answer,saved_at) VALUES(?,?,?,?) ON CONFLICT(session_id,item_id) DO UPDATE SET answer=excluded.answer,version=assessment_answers.version+1,saved_at=excluded.saved_at',(identity,body.item_id,body.answer,now()))
        conn.execute('UPDATE assessment_sessions SET version=version+1 WHERE id=?',(identity,))
        return view(conn,owned(conn,identity,user))

class Finish(StrictModel):version:int=Field(ge=1)
@router.post('/assessments/{identity}/finish',status_code=202)
def finish(identity:str,body:Finish,user=Depends(session_user)):
    require_role(user,'candidate')
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');row=owned(conn,identity,user)
        if row['status']=='completed':return view(conn,row)
        if row['status']!='active' or row['version']!=body.version:raise HTTPException(409,'Сессия изменилась; обновите страницу')
        if row['expires']<time.time():raise HTTPException(409,'Время истекло; сохранённые ответы доступны, начните новую попытку')
        conn.execute("UPDATE assessment_sessions SET status='grading',version=version+1 WHERE id=?",(identity,))
        job=enqueue('assessment.grade',{'session_id':identity},user['id'],identity+':'+str(row['version']),conn=conn)
    return {'id':identity,'job_id':job,'status':'grading'}

def process_grading(context,payload):
    with connection() as conn:
        row=conn.execute('SELECT * FROM assessment_sessions WHERE id=?',(payload['session_id'],)).fetchone()
        answers={r['item_id']:r['answer'] for r in conn.execute('SELECT * FROM assessment_answers WHERE session_id=?',(row['id'],))}
    data=json.loads(row['payload']);results=[]
    for item in data['items']:
        try:result=grade_answer(item,answers.get(item['id'],''))
        except HTTPException:
            with connection() as conn:
                data['technical_error']='Проверка временно недоступна. Ответы сохранены.'
                conn.execute("UPDATE assessment_sessions SET status='active',payload=?,expires=?,version=version+1 WHERE id=?",(dumps(data),time.time()+2700,row['id']))
            return {'session_id':row['id'],'technical_pause':True}
        results.append({'item_id':item['id'],'competency':item['competency'],**result})
    measured=[r['score'] for r in results if r['score'] is not None];score=round(sum(measured)/len(measured),2)
    passed=score>=POLICY['pass_score'];level=GRADES.index(row['grade'])
    data['result']={'score':score,'passed':passed,'strong':score>=POLICY['strong_score'],'items':results,'calibration':'pilot_unvalidated','scope':'Объективные задания этой сессии; открытый ответ ждёт эксперта','suggested_grade':GRADES[level+1] if passed and score>=POLICY['strong_score'] and level<3 else GRADES[level-1] if not passed and level>0 else None,'policy':POLICY}
    if data.get('assessment_path'):
        from .assessment_journey import result_for
        data['result']=result_for(data,results);score=data['result']['score'];passed=False
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');context.check(conn)
        current=conn.execute('SELECT status FROM assessment_sessions WHERE id=?',(row['id'],)).fetchone()
        if current[0]!='grading':return {'skipped':True}
        if data.get('assessment_path'):
            from .assessment_journey import issue_automatic
            issue_automatic(conn,row,data)
            passed=data['result']['passed']
        if passed and not data.get('assessment_path') and row['mode']=='certification':
            conn.execute('UPDATE grade_certifications SET active=0 WHERE candidate_id=? AND specialization=?',(row['candidate_id'],row['specialization']))
            from .competency_feedback import feedback
            from .assessment_coverage import coverage
            proof={'blueprint_version':data['blueprint_version'],'calibration':'pilot_unvalidated','tested_skills':data.get('tested_skills',[]),'competencies':{r['competency']:r['score'] for r in feedback(data['result']) if r['score'] is not None},'coverage':coverage(row['specialization'],row['grade'],data['items'],data['result'],data.get('coverage')),'demo':False,'policy_version':POLICY['version'],'sources_snapshot':data.get('sources_snapshot',[]),'generator':{k:data.get(k) for k in ['generator','model','model_revision','quantization','generator_scope','neural_items','total_items']}}
            conn.execute('INSERT INTO grade_certifications VALUES(?,?,?,?,?,?,?,?,?,1)',('cert_'+secrets.token_hex(10),row['candidate_id'],row['specialization'],row['grade'],score,row['id'],'provisional',dumps(proof),now()))
        conn.execute("UPDATE assessment_sessions SET payload=?,status='completed',completed_at=?,version=version+1 WHERE id=?",(dumps(data),now(),row['id']))
        if data.get('assessment_path'):
            from .skill_passports import issue
            issue(conn,conn.execute('SELECT * FROM assessment_sessions WHERE id=?',(row['id'],)).fetchone(),data)
            if data['assessment_path']['version'] in {'work_sample_v2_auto','work_sample_v3_balanced'}:
                from .identity import notify
                recipient=conn.execute('SELECT id FROM users WHERE candidate_id=?',(row['candidate_id'],)).fetchone()
                if recipient:notify(conn,recipient[0],'assessment_result',row['id'],'Автоматическая проверка завершена. Результат и паспорт доступны в кабинете.')
        bump(conn);audit(conn,context.job['actor_id'],'assessment_completed',row['id'],{'score':score,'passed':passed,'calibration':'pilot_unvalidated'})
    return {'session_id':row['id'],'score':score,'passed':passed}

@router.post('/assessments/{identity}/cancel')
def cancel(identity:str,user=Depends(session_user)):
    require_role(user,'candidate')
    with connection() as conn:
        row=owned(conn,identity,user)
        if row['status'] not in {'generating','active'}:raise HTTPException(409,'Сессия уже завершена или проверяется')
        conn.execute("UPDATE assessment_sessions SET status='cancelled',version=version+1 WHERE id=?",(identity,))
    return {'ok':True}

class Appeal(StrictModel):message:str=Field(min_length=10,max_length=2000)
@router.post('/assessments/{identity}/appeal',status_code=201)
def appeal(identity:str,body:Appeal,user=Depends(session_user)):
    require_role(user,'candidate')
    with connection() as conn:
        row=owned(conn,identity,user)
        if row['status']!='completed':raise HTTPException(409,'Результат ещё не готов')
        identity='appeal_'+secrets.token_hex(10)
        conn.execute('INSERT INTO assessment_appeals VALUES(?,?,?,?,?,?)',(identity,row['id'],user['candidate_id'],body.message,'open',now()))
    return {'id':identity,'state':'open'}
