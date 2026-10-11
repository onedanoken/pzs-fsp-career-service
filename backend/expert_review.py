"""Human review queue; neither reviewer feedback nor appeals fake calibration."""
import json,secrets
from fastapi import APIRouter,Depends,HTTPException
from pydantic import Field
from .auth import session_user,require_role,require_verified
from .db import connection,dumps,now,audit,bump
from .schemas import StrictModel
router=APIRouter(prefix='/api',tags=['Assessment expert review'])

@router.get('/admin/assessment-reviews')
def queue(user=Depends(session_user)):
    require_role(user,'admin')
    with connection() as conn:
        items=[]
        for row in conn.execute("SELECT * FROM assessment_sessions WHERE status='completed' ORDER BY completed_at DESC"):
            payload=json.loads(row['payload'])
            for item in payload['items']:
                if item['kind']!='explanation':continue
                reviews=[dict(r) for r in conn.execute('SELECT * FROM assessment_reviews WHERE session_id=? AND item_id=? ORDER BY created_at DESC',(row['id'],item['id']))]
                answer=conn.execute('SELECT answer FROM assessment_answers WHERE session_id=? AND item_id=?',(row['id'],item['id'])).fetchone()
                entry={'session_id':row['id'],'candidate_id':row['candidate_id'],'specialization':row['specialization'],'grade':row['grade'],'version':row['version'],'item_id':item['id'],'prompt':item['prompt'],'answer':answer[0] if answer else '', 'rubric':item['private']['rubric'],'reviews':reviews}
                if payload.get('assessment_path'):
                    case=next(x for x in payload['items'] if x['kind']=='work_sample')
                    artifact=conn.execute('SELECT answer FROM assessment_answers WHERE session_id=? AND item_id=?',(row['id'],case['id'])).fetchone()
                    entry.update(rubric_criteria=item['private']['rubric_criteria'],assessment_path=payload['result']['assessment_path'],work_sample={'prompt':case['prompt'],'starter':case['starter'],'answer':artifact[0] if artifact else '', 'requirements':case['requirements']})
                    if case.get('example_table'):
                        entry['work_sample']['example_table']=case['example_table']
                items.append(entry)
        audit(conn,user['id'],'assessment_review_queue_viewed','queue',{'items':len(items)})
        return {'items':items,'appeals':[dict(r) for r in conn.execute("SELECT * FROM assessment_appeals WHERE state='open'")]}

class Review(StrictModel):
    item_id:str;version:int=Field(ge=1);score:int|None=Field(default=None,ge=0,le=100)
    rationale:str=Field(min_length=20,max_length=3000)
    criteria:list[dict]|None=Field(default=None,max_length=4)
@router.post('/admin/assessment-reviews/{identity}')
def review(identity:str,body:Review,user=Depends(session_user)):
    require_role(user,'admin');require_verified(user)
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');row=conn.execute("SELECT * FROM assessment_sessions WHERE id=? AND status='completed'",(identity,)).fetchone()
        if not row:raise HTTPException(404,'Результат не найден')
        if row['version']!=body.version:raise HTTPException(409,'Результат уже проверен или изменён')
        data=json.loads(row['payload'])
        if not any(x['id']==body.item_id and x['kind']=='explanation' for x in data['items']):raise HTTPException(422,'Нужен открытый ответ этой сессии')
        if len(body.rationale.strip())<20:raise HTTPException(422,'Нужно содержательное обоснование решения')
        score=body.score;criteria=None
        if data.get('assessment_path'):
            if data['result']['assessment_path']['review_status']=='reviewed':raise HTTPException(409,'Разбор уже сохранён; используйте процедуру апелляции.')
            from .assessment_journey import ratings
            rubric=next(x for x in data['items'] if x['id']==body.item_id)['private']['rubric_criteria']
            try:score,criteria=ratings(body,rubric)
            except ValueError as error:raise HTTPException(422,str(error))
        elif score is None:raise HTTPException(422,'Укажите оценку открытого ответа')
        conn.execute('INSERT INTO assessment_reviews VALUES(?,?,?,?,?,?,NULL,?)',('review_'+secrets.token_hex(10),identity,user['id'],body.item_id,score,body.rationale.strip(),now()))
        data['result']['expert_review']={'item_id':body.item_id,'score':score,'rationale':body.rationale.strip(),'reviewed_at':now()}
        if criteria is not None:
            data['result']['expert_review']['criteria']=criteria
            from .assessment_journey import complete
            complete(conn,row,data,score,criteria)
        if criteria is None:data['result']['scope']='Объективные задания и отдельная экспертная оценка открытого ответа; шкала ещё пилотная'
        conn.execute('UPDATE assessment_sessions SET payload=?,version=version+1 WHERE id=?',(dumps(data),identity))
        if criteria is not None:
            from .skill_passports import issue
            issue(conn,conn.execute('SELECT * FROM assessment_sessions WHERE id=?',(identity,)).fetchone(),data)
            from .identity import notify
            actor=conn.execute('SELECT id FROM users WHERE candidate_id=?',(row['candidate_id'],)).fetchone()
            if actor:notify(conn,actor[0],'assessment_review',identity,'Разбор проверки навыков готов. Откройте результат и новую версию паспорта.')
            bump(conn)
        audit(conn,user['id'],'assessment_expert_reviewed',identity,{'score':score})
    return {'ok':True,'notice':'Оценка открытого ответа сохранена отдельно; пороги грейда требуют калибровки'}

class Resolve(StrictModel):state:str;resolution:str=Field(min_length=20,max_length=3000)
@router.post('/admin/assessment-appeals/{identity}/resolve')
def resolve(identity:str,body:Resolve,user=Depends(session_user)):
    require_role(user,'admin');require_verified(user)
    if body.state not in {'accepted','dismissed'}:raise HTTPException(422,'Неизвестное решение')
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');row=conn.execute("SELECT * FROM assessment_appeals WHERE id=? AND state='open'",(identity,)).fetchone()
        if not row:raise HTTPException(409,'Апелляция уже рассмотрена или отсутствует')
        conn.execute('INSERT INTO assessment_reviews VALUES(?,?,?,NULL,NULL,?,?,?)',('review_'+secrets.token_hex(10),row['session_id'],user['id'],body.resolution,identity,now()))
        conn.execute('UPDATE assessment_appeals SET state=? WHERE id=?',(body.state,identity));audit(conn,user['id'],'assessment_appeal_resolved',identity,{'state':body.state})
        if body.state=='accepted':
            attempt=conn.execute('SELECT * FROM assessment_sessions WHERE id=?',(row['session_id'],)).fetchone();data=json.loads(attempt['payload'])
            if data.get('assessment_path') and data['result']['assessment_path']['review_status']=='reviewed':
                data['result']['previous_expert_review']=data['result'].pop('expert_review')
                if data['result']['assessment_path'].get('review_required') is False:
                    data['result']['assessment_path']['review_status']='optional'
                    data['result']['scope']='Апелляция принята для дополнительного разбора. Автоматический результат и действующая категория сохранены.'
                else:
                    data['result']['assessment_path'].update(review_status='pending',state='awaiting_review')
                    data['result'].update(passed=False,strong=False,scope='Апелляция принята; разбор переоткрыт. Действующая категория сохраняется до решения.')
                conn.execute('UPDATE assessment_sessions SET payload=?,version=version+1 WHERE id=?',(dumps(data),attempt['id']))
                from .skill_passports import issue
                issue(conn,conn.execute('SELECT * FROM assessment_sessions WHERE id=?',(attempt['id'],)).fetchone(),data);bump(conn)
        from .identity import notify
        recipient=conn.execute('SELECT id FROM users WHERE candidate_id=?',(row['candidate_id'],)).fetchone()
        if recipient:notify(conn,recipient[0],'assessment_appeal',identity,'Апелляция рассмотрена; решение доступно в истории аттестации')
    return {'ok':True,'state':body.state}

@router.get('/assessment-appeals')
def my_appeals(user=Depends(session_user)):
    require_role(user,'candidate')
    with connection() as conn:
        items=[]
        for row in conn.execute('SELECT * FROM assessment_appeals WHERE candidate_id=? ORDER BY created_at DESC',(user['candidate_id'],)):
            items.append(dict(row,resolutions=[dict(r) for r in conn.execute('SELECT rationale,created_at FROM assessment_reviews WHERE appeal_id=?',(row['id'],))]))
        return {'items':items}
