"""Keep screening, artifact checks and human judgment distinct and pilot-labelled."""
import json,secrets
from datetime import datetime
from .db import dumps,now
from .assessment_catalog import POLICY,GRADES
from .work_samples import PATH_VERSION,AUTO_PATH_VERSION,AUTO_PATH_VERSIONS,BALANCED_PATH_VERSION,RUBRIC

def result_for(data,results):
    screening=[x for x in results if next(i for i in data['items'] if i['id']==x['item_id'])['stage']=='screening']
    work=next(x for x in results if next(i for i in data['items'] if i['id']==x['item_id'])['kind']=='work_sample')
    case=next(i for i in data['items'] if i['kind']=='work_sample');score=round(sum(x['score'] for x in screening)/len(screening),2)
    frozen_policy=data.get('policy_snapshot') or POLICY
    automatic=score>=frozen_policy['pass_score'] and work.get('all_checks_passed',False)
    auto=data['assessment_path']['version'] in AUTO_PATH_VERSIONS
    domains={};transfer=None
    if data['assessment_path']['version']==BALANCED_PATH_VERSION:
        plan=data['assembly_plan'];by_id={i['id']:i for i in data['items']}
        for competency,quota in plan['quota'].items():
            values=[r['score'] for r in screening if by_id[r['item_id']]['competency']==competency]
            domains[competency]={'count':len(values),'required':quota,'score':round(sum(values)/len(values),2) if values else 0,'passed':len(values)==quota and sum(values)/len(values)>=frozen_policy['core_floor']}
        transfer=next(r for r in results if by_id[r['item_id']]['stage']=='transfer')
        automatic=automatic and all(d['passed'] for d in domains.values()) and transfer.get('all_checks_passed',False)
    path={'version':data['assessment_path']['version'],'review_required':not auto,'screening_passed':score>=frozen_policy['pass_score'],'work_score':work['score'],'work_passed':work.get('all_checks_passed',False),'work_checks':work.get('checks',[]),'work_family':case['family'],'review_status':'optional' if auto else 'pending','state':('automatic_pass' if automatic else 'automatic_needs_work') if auto else 'awaiting_review','automatic_passed':automatic,'rubric':data['assessment_path']['rubric']}
    if transfer is not None:path.update(screening_passed=score>=frozen_policy['pass_score'] and all(d['passed'] for d in domains.values()),core_scores=domains,core_passed=all(d['passed'] for d in domains.values()),transfer_score=transfer['score'],transfer_passed=transfer.get('all_checks_passed',False),transfer_checks=transfer.get('checks',[]),assembly_plan=data['assembly_plan'])
    strong=auto and automatic and score>=frozen_policy['strong_score']
    level=GRADES.index(case['private']['condition']['grade'])
    scope='Тест по квотам, минимум по каждой основной области, рабочий кейс и изменённое условие проверены автоматически. Это пилотный результат ограниченных задач, не подтверждение квалификации на людях.' if transfer is not None else 'Короткий тест и ограниченный рабочий кейс проверены автоматически. Объяснение не влияет на категорию; результат и пороги пилотные.' if auto else 'Короткий тест и модельная проверка кейса готовы. Разбор по рубрике ожидается; категория ещё не выдана.'
    return {'score':score,'passed':auto and automatic,'strong':strong,'items':results,'calibration':'pilot_unvalidated','policy':frozen_policy,'suggested_grade':GRADES[level+1] if strong and level<3 else GRADES[level-1] if auto and not automatic and level>0 else None,'scope':scope,'assessment_path':path}

def ratings(body,rubric):
    values=body.criteria
    if not isinstance(values,list) or len(values)!=len(rubric):raise ValueError('Заполните все критерии рубрики.')
    required={x['id'] for x in rubric};seen=set();result=[]
    for entry in values:
        if not isinstance(entry,dict) or set(entry)!={'id','level','note'} or not isinstance(entry['id'],str) or entry['id'] not in required or entry['id'] in seen:raise ValueError('Неизвестный или повторный критерий.')
        if type(entry['level']) is not int or not 0<=entry['level']<=3 or not isinstance(entry['note'],str) or not 10<=len(entry['note'].strip())<=1000:raise ValueError('Укажите уровень 0–3 и обоснование не короче 10 символов для каждого критерия.')
        definition=next(x for x in rubric if x['id']==entry['id']);seen.add(entry['id']);result.append({'id':entry['id'],'label':definition['label'],'level':entry['level'],'note':entry['note'].strip()})
    score=round(sum(x['level'] for x in result)*100/(3*len(rubric)),2)
    if body.score is not None and body.score!=score:raise ValueError('Общий балл вычисляется по уровням рубрики; не задавайте другой балл.')
    return score,result

def complete(conn,row,data,score,criteria):
    result=data['result'];path=result['assessment_path'];levels={x['id']:x['level'] for x in criteria}
    if path['version'] in AUTO_PATH_VERSIONS:
        path['review_status']='reviewed'
        result['scope']='Автоматические проверки определяют категорию. Дополнительный ручной разбор сохранён отдельно и не изменяет её.'
        return result['passed']
    accepted=path['automatic_passed'] and score>=result['policy']['pass_score'] and levels['correctness']>=2 and levels['verification']>=2 and levels['reasoning']>=1 and levels['limits']>=1
    path.update(review_status='reviewed',state='reviewed_pass' if accepted else 'reviewed_needs_work')
    strong=accepted and result['score']>=result['policy']['strong_score'] and path['work_passed'] and score>=result['policy']['strong_score']
    level=GRADES.index(row['grade'])
    result.update(passed=accepted,strong=strong,suggested_grade=GRADES[level+1] if strong and level<3 else GRADES[level-1] if not accepted and level>0 else None,scope='Тест, ограниченный рабочий кейс и отдельный разбор по рубрике. Грейд и пороги остаются пилотными.')
    if accepted and row['mode']=='certification':
        current=conn.execute('SELECT * FROM grade_certifications WHERE candidate_id=? AND specialization=? AND active=1 ORDER BY created_at DESC LIMIT 1',(row['candidate_id'],row['specialization'])).fetchone()
        if current and current['session_id']!=row['id'] and datetime.fromisoformat(current['created_at'])>datetime.fromisoformat(row['created_at']):
            result['category_update']='historical_only';return accepted
        # Only this approved session may issue a category; practice never modifies it.
        conn.execute('UPDATE grade_certifications SET active=0 WHERE candidate_id=? AND specialization=?',(row['candidate_id'],row['specialization']))
        from .assessment_coverage import coverage
        proof={'blueprint_version':data['blueprint_version'],'calibration':'pilot_unvalidated','tested_skills':data.get('tested_skills',[]),'coverage':coverage(row['specialization'],row['grade'],data['items'],result,data.get('coverage')),'assessment_path':{'version':PATH_VERSION,'screening_score':result['score'],'work_score':path['work_score'],'review_score':score,'reviewed_at':result['expert_review']['reviewed_at']},'demo':False,'policy_version':result['policy']['version'],'sources_snapshot':data.get('sources_snapshot',[])}
        identity='cert_'+secrets.token_hex(10);result['certificate_id']=identity
        profile=conn.execute('SELECT payload FROM candidates WHERE id=?',(row['candidate_id'],)).fetchone();proof['demo']=bool(json.loads(profile[0]).get('demo'))
        conn.execute('INSERT INTO grade_certifications VALUES(?,?,?,?,?,?,?,?,?,1)',(identity,row['candidate_id'],row['specialization'],row['grade'],result['score'],row['id'],'provisional',dumps(proof),now()))
    return accepted

def issue_automatic(conn,row,data):
    """Issue once from objective checks; practice and newer categories remain intact."""
    result=data['result'];path=result['assessment_path']
    if path['version'] not in AUTO_PATH_VERSIONS or not result['passed'] or row['mode']!='certification':return
    existing=conn.execute('SELECT id FROM grade_certifications WHERE session_id=?',(row['id'],)).fetchone()
    if existing:result['certificate_id']=existing[0];return
    current=conn.execute('SELECT * FROM grade_certifications WHERE candidate_id=? AND specialization=? AND active=1 ORDER BY created_at DESC LIMIT 1',(row['candidate_id'],row['specialization'])).fetchone()
    if current and datetime.fromisoformat(current['created_at'])>datetime.fromisoformat(row['created_at']):
        result['category_update']='historical_only';return
    from .assessment_coverage import coverage
    profile=json.loads(conn.execute('SELECT payload FROM candidates WHERE id=?',(row['candidate_id'],)).fetchone()[0])
    proof={'blueprint_version':data['blueprint_version'],'calibration':'pilot_unvalidated','tested_skills':data.get('tested_skills',[]),'coverage':coverage(row['specialization'],row['grade'],data['items'],result,data.get('coverage')),'assessment_path':{'version':path['version'],'screening_score':result['score'],'work_score':path['work_score'],'review_required':False,'method':'objective_model_checks'},'score_basis':'screening_average_with_all_work_checks_gate','demo':bool(profile.get('demo')),'policy_version':result['policy']['version'],'sources_snapshot':data.get('sources_snapshot',[])}
    if path['version']==BALANCED_PATH_VERSION:
        proof['score_basis']='balanced_core_average_with_per_core_floor_and_original_transfer_gates'
        proof['assessment_path'].update(core_scores=path['core_scores'],transfer_score=path['transfer_score'],assembly_version=data['assembly_plan']['version'])
    conn.execute('UPDATE grade_certifications SET active=0 WHERE candidate_id=? AND specialization=?',(row['candidate_id'],row['specialization']))
    identity='cert_'+secrets.token_hex(10);result['certificate_id']=identity
    conn.execute('INSERT INTO grade_certifications VALUES(?,?,?,?,?,?,?,?,?,1)',(identity,row['candidate_id'],row['specialization'],row['grade'],result['score'],row['id'],'provisional',dumps(proof),now()))
