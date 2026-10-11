"""Collect auditable expert/UX observations; never manufacture a human pilot."""
import json,secrets,statistics
from fastapi import APIRouter,Depends,HTTPException
from pydantic import Field
from .auth import session_user,require_role
from .db import connection,dumps,now,audit
from .schemas import StrictModel
from .assessment_catalog import GRADES,SPECIALIZATIONS
from .professional_items import LEVEL_CRITERIA

router=APIRouter(prefix='/api',tags=['Expert calibration and UX pilot'])

def weighted_kappa(pairs):
    if len(pairs)<2:return None
    n=len(pairs);a=[sum(x==i for x,y in pairs)/n for i in range(4)];b=[sum(y==i for x,y in pairs)/n for i in range(4)]
    observed=sum((x-y)**2 for x,y in pairs)/n;expected=sum(a[i]*b[j]*(i-j)**2 for i in range(4) for j in range(4))
    return round(1-observed/expected,4) if expected else None

def report(rows,include_splits=True):
    groups=[]
    for source in ['human_observation','synthetic_fixture']:
        observations=[r for r in rows if r['payload']['source']==source]
        assessment=[r['payload'] for r in observations if r['kind']=='assessment']
        ux=[r['payload'] for r in observations if r['kind']=='ux']
        pairs=[(GRADES.index(x['test_grade']),GRADES.index(x['expert_grade'])) for x in assessment if x['test_grade'] is not None]
        groups.append({'source':source,'assessment_ratings':len(assessment),'participants':len({x['participant_ref'] for x in assessment}),'assessors':len({x['assessor_ref'] for x in assessment}),'exact_agreement':round(sum(a==b for a,b in pairs)/len(pairs),4) if pairs else None,'weighted_kappa':weighted_kappa(pairs),'ux_walkthroughs':len(ux),'ux_completion_rate':round(sum(x['completed'] for x in ux)/len(ux),4) if ux else None,'ux_median_seconds':statistics.median(x['seconds'] for x in ux) if ux else None})
    result={'groups':groups,'calibration':'pilot_unvalidated','limitations':['Автор наблюдения отвечает за происхождение; запись не доказывает независимость эксперта.','Малые выборки и согласие оценок не подтверждают точность шкалы.','Порог и статус сертификатов не изменяются автоматически.'],'protocol':{'target_participants':'30–60 как стартовый ориентир','assessment':'Два независимых оценщика для части ответов; разные варианты повторного теста; контроль отделён от настройки.','ux':'Кандидат и работодатель проходят основные и дополнительные пути; фиксируют время, ошибки, обратную связь и повтор после изменений.'}}
    if include_splits:result['by_split']={split:report([r for r in rows if r['payload']['split']==split],False)['groups'] for split in ['development','holdout']}
    return result

@router.get('/admin/pilot')
def get(user=Depends(session_user)):
    require_role(user,'admin')
    with connection() as conn:rows=[dict(r,payload=json.loads(r['payload'])) for r in conn.execute('SELECT * FROM pilot_observations ORDER BY created_at DESC')]
    return {'report':report(rows),'items':rows,'level_criteria':LEVEL_CRITERIA,'specializations':SPECIALIZATIONS}

class Observation(StrictModel):
    kind:str;source:str='human_observation';participant_ref:str=Field(min_length=3,max_length=80)
    assessor_ref:str=Field(min_length=3,max_length=80);protocol_ref:str=Field(min_length=3,max_length=80)
    session_id:str|None=None;expert_grade:str|None=None
    split:str='holdout';route:str=Field(default='',max_length=120);completed:bool=False
    seconds:int=Field(default=0,ge=0,le=86400);errors:int=Field(default=0,ge=0,le=1000)
    feedback:str=Field(min_length=20,max_length=3000)

@router.post('/admin/pilot',status_code=201)
def add(body:Observation,user=Depends(session_user)):
    require_role(user,'admin')
    if body.kind not in {'assessment','ux'} or body.source not in {'human_observation','synthetic_fixture'} or body.split not in {'development','holdout'}:raise HTTPException(422,'Неизвестный тип наблюдения')
    data=body.model_dump()
    with connection() as conn:
        if body.kind=='assessment':
            if body.expert_grade not in GRADES:raise HTTPException(422,'Укажите экспертный грейд')
            session=conn.execute("SELECT * FROM assessment_sessions WHERE id=? AND status='completed'",(body.session_id,)).fetchone()
            if not session:raise HTTPException(422,'Нужна завершённая тестовая сессия')
            payload=json.loads(session['payload']);data.update(test_grade=session['grade'] if payload['result']['passed'] else None,attempted_grade=session['grade'],specialization=session['specialization'],blueprint_version=payload.get('blueprint_version'),test_score=payload['result']['score'],test_passed=payload['result']['passed'])
        elif not body.route or not body.seconds:raise HTTPException(422,'Укажите маршрут и измеренное время UX-прохода')
        identity='pilot_'+secrets.token_hex(10);conn.execute('INSERT INTO pilot_observations VALUES(?,?,?,?,?)',(identity,user['id'],body.kind,dumps(data),now()));audit(conn,user['id'],'pilot_observation',identity,{'source':body.source,'kind':body.kind})
    return {'id':identity}
