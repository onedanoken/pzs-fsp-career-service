"""Versioned safe evidence, published only by the owner; never a public bearer link."""
import hashlib,json,secrets,os
from fastapi import APIRouter,Depends,HTTPException
from fastapi.responses import JSONResponse
from .auth import session_user,require_role
from .db import connection,dumps,now,audit
from .schemas import StrictModel
router=APIRouter(prefix='/api',tags=['Skill result passports'])

def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def issue(conn,row,data):
    result=data['result'];path=result['assessment_path'];stamp=now()
    latest=conn.execute('SELECT revision,published FROM assessment_passports WHERE session_id=? ORDER BY revision DESC LIMIT 1',(row['id'],)).fetchone()
    revision=latest['revision']+1 if latest else 1;identity='sp_'+secrets.token_hex(12)
    reviewer=result.get('expert_review') or {}
    profile=conn.execute('SELECT payload FROM candidates WHERE id=?',(row['candidate_id'],)).fetchone()
    snapshot={'schema':'fsp-skill-passport-v1','id':identity,'revision':revision,'candidate_id':row['candidate_id'],'specialization':row['specialization'],'requested_grade':row['grade'],'mode':row['mode'],'issued_at':stamp,'attempt_completed_at':row['completed_at'] or stamp,'blueprint_version':data.get('blueprint_version'),'path_version':path['version'],'status':path['state'],'screening':{'score':result['score'],'passed':path['screening_passed'],'item_count':3},'work_sample':{'score':path['work_score'],'all_checks_passed':path['work_passed'],'family':path['work_family'],'method':'bounded_work_model','scope':'Ограниченный артефакт и заданные модельные условия; произвольный пользовательский код не исполнялся.'},'review':{'status':path['review_status'],'score':reviewer.get('score'),'reviewed_at':reviewer.get('reviewed_at'),'criteria':reviewer.get('criteria',[])},'category_issued':bool(result.get('certificate_id')),'calibration':'pilot_unvalidated','policy':{'pass_score':70,'rubric_minimums':{'correctness':2,'verification':2,'reasoning':1,'limits':1}},'sources':[{'id':s['id'],'url':s['url'],'sha256':s.get('sha256'),'fetched_at':s.get('fetched_at')} for s in data.get('sources_snapshot',[])],'limits':['Результат относится к конкретным заданиям и их версии.','Рубрика и пороги не прошли независимую калибровку; universal Senior не подтверждается.','Объяснение кандидата, решения, контакты и скрытые эталоны в паспорт не входят.','Хеш сравнивается с экземпляром в авторизованном сервисе; это не подпись ФСП.']}
    # Free-text reviewer notes can contain personal data; the portable snapshot has scores only.
    snapshot['review']['criteria']=[{k:x[k] for k in ['id','label','level'] if k in x} for x in snapshot['review']['criteria']]
    snapshot['demo']=bool(json.loads(profile[0]).get('demo')) if profile else False
    from .db import DEMO
    snapshot['environment']='demonstration' if DEMO else 'operational'
    snapshot['policy']['pass_score']=result['policy']['pass_score']
    snapshot['review']['method']='optional_structured_review' if path.get('review_required') is False and reviewer else 'not_performed_optional' if path.get('review_required') is False else 'authorized_structured_review'
    snapshot['policy']['review_required']=path.get('review_required',True)
    if path.get('review_required') is False:snapshot['policy']['rubric_minimums']={}
    snapshot['work_sample']['method']='bounded_work_model_v2' if path['version']=='work_sample_v2_auto' else 'bounded_work_model'
    case=next(x for x in data['items'] if x['kind']=='work_sample')
    snapshot['work_sample']['version']=case['version']
    if case.get('rubric_version'):snapshot['review']['rubric_version']=case['rubric_version']
    snapshot['review']['independent_calibration']=False
    snapshot['issuer']={'service':'ФСП Карьера','url':os.environ.get('FSP_PUBLIC_URL','http://localhost:8000').rstrip('/')}
    snapshot['conditions']={'tools_allowed':bool(data.get('tools_allowed',True)),'control':'Правило использования инструментов заявлено в сессии; отдельная проверка личности и мониторинг не проводились.'}
    if path['version']=='work_sample_v3_balanced':
        snapshot['schema']='fsp-skill-passport-v2'
        snapshot['screening'].update(item_count=data['assembly_plan']['count'],core_scores=path['core_scores'],assembly_plan=data['assembly_plan'])
        snapshot['work_sample']['method']='bounded_models_and_project_replay_v3'
        snapshot['transfer']={'score':path['transfer_score'],'all_checks_passed':path['transfer_passed'],'method':'explicit_changed_condition_checks'}
        snapshot['policy'].update(core_floor=result['policy']['core_floor'],version=result['policy']['version'],work_checks_required=['original','transfer'])
    conn.execute('INSERT INTO assessment_passports(id,session_id,candidate_id,revision,snapshot,integrity_sha256,published,created_at) VALUES(?,?,?,?,?,?,?,?)',(identity,row['id'],row['candidate_id'],revision,dumps(snapshot),digest(snapshot),latest['published'] if latest else 0,stamp))
    return identity

def authorize(conn,row,user):
    if user['role']=='candidate' and user.get('candidate_id')==row['candidate_id']:return
    if user['role']=='admin':return
    if user['role']!='employer' or not row['published']:raise HTTPException(404,'Паспорт недоступен')
    from .privacy import verified_employer,eligible
    from .domain import candidate_from_row
    from .trust import company
    verified_employer(user)
    candidate=conn.execute('SELECT * FROM candidates WHERE id=?',(row['candidate_id'],)).fetchone()
    if not candidate or not eligible(conn,candidate_from_row(candidate,conn),user.get('org_id')):raise HTTPException(404,'Паспорт недоступен')
    if company(conn,user['org_id'])['verification'] not in {'verified','demo_verified'} or conn.execute('SELECT 1 FROM employer_blocks WHERE candidate_id=? AND org_id=?',(row['candidate_id'],user['org_id'])).fetchone():raise HTTPException(404,'Паспорт недоступен')

def view(conn,row,user):
    authorize(conn,row,user);snapshot=json.loads(row['snapshot'])
    if digest(snapshot)!=row['integrity_sha256']:raise HTTPException(409,'Проверка целостности паспорта не пройдена')
    newest=conn.execute('SELECT MAX(revision) FROM assessment_passports WHERE session_id=?',(row['session_id'],)).fetchone()[0]
    from .certification_validity import usable_certificate
    grade=any(usable_certificate(record) for record in conn.execute('SELECT * FROM grade_certifications WHERE session_id=? AND active=1',(row['session_id'],)))
    return {'id':row['id'],'revision':row['revision'],'session_id':row['session_id'],'published':bool(row['published']),'snapshot':snapshot,'integrity_sha256':row['integrity_sha256'],'verification_url':'/api/skill-passports/'+row['id'],'current':{'superseded':row['revision']<newest,'category_active':bool(grade),'category_confirmed_by_this_revision':bool(grade) and snapshot['status'] in {'reviewed_pass','automatic_pass'},'notice':'Повторная выдача всегда проверяет текущее право доступа; исторический результат не является постоянным разрешением.'}}

@router.get('/skill-passports')
def listing(candidate_id:str|None=None,user=Depends(session_user)):
    target=user.get('candidate_id') if user['role']=='candidate' else candidate_id
    if not target:return {'items':[]}
    if user['role']=='candidate' and candidate_id and candidate_id!=target:raise HTTPException(404,'Паспорта недоступны')
    with connection() as conn:
        rows=conn.execute('SELECT p.* FROM assessment_passports p WHERE candidate_id=? AND revision=(SELECT MAX(q.revision) FROM assessment_passports q WHERE q.session_id=p.session_id) ORDER BY created_at DESC',(target,)).fetchall()
        items=[]
        for row in rows:
            try:items.append(view(conn,row,user))
            except HTTPException as error:
                if error.status_code!=404:raise
        return {'items':items}

@router.get('/skill-passports/{identity}')
def read(identity:str,user=Depends(session_user)):
    with connection() as conn:
        row=conn.execute('SELECT * FROM assessment_passports WHERE id=?',(identity,)).fetchone()
        if not row:raise HTTPException(404,'Паспорт не найден')
        return view(conn,row,user)

@router.get('/skill-passports/{identity}/download')
def download(identity:str,user=Depends(session_user)):
    result=read(identity,user)
    return JSONResponse(result,headers={'Content-Disposition':'attachment; filename="fsp-skill-passport-'+result['id']+'.json"','Cache-Control':'no-store'})

class Visibility(StrictModel):published:bool
@router.put('/skill-passports/{identity}/visibility')
def visibility(identity:str,body:Visibility,user=Depends(session_user)):
    require_role(user,'candidate')
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');row=conn.execute('SELECT * FROM assessment_passports WHERE id=? AND candidate_id=?',(identity,user['candidate_id'])).fetchone()
        if not row:raise HTTPException(404,'Паспорт не найден')
        from .privacy import consent
        if body.published and not (consent(conn,row['candidate_id'],'processing') and consent(conn,row['candidate_id'],'publication')):raise HTTPException(403,'Для показа работодателям нужны согласия на обработку и публикацию профиля.')
        conn.execute('UPDATE assessment_passports SET published=? WHERE session_id=? AND candidate_id=?',(int(body.published),row['session_id'],row['candidate_id']))
        audit(conn,user['id'],'skill_passport_visibility',row['id'],{'published':body.published})
        return {'ok':True,'published':body.published}
