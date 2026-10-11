"""Explainable bounded evidence policy; scores are ranking points, not probabilities."""
import json,math
from datetime import datetime,timezone
from .assessment_catalog import GRADES

VERSION='category-evidence-v2'
WEIGHTS={'objective':55,'declared_stack':20,'requested_evidence':20,'recency':3,'fsp':2,'adjacent_penalty':8,'unknown_penalty':2}

def timestamp(value):
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (ValueError,TypeError,AttributeError,OverflowError):return None

def select_certificate(rows,need,filters,reference_time=None):
    from .certification_validity import usable_certificate
    matches=[]
    for row in rows:
        if not usable_certificate(row,reference_time):continue
        if row['specialization']!=need['specialization'] or row['grade'] not in GRADES:continue
        gap=abs(GRADES.index(row['grade'])-GRADES.index(need['grade']))
        if gap and (not filters.get('adjacent_grades') or gap!=1):continue
        score=row['score']
        if type(score) not in [int,float] or not math.isfinite(score) or not 0<=score<=100:continue
        matches.append(row)
    return sorted(matches,key=lambda row:(row['grade']!=need['grade'],-(timestamp(row['created_at']) or 0),row['id']))[:1]

def score(candidate,need,certificate,previous,reference_time=None):
    stamp=timestamp(certificate['created_at']);current=reference_time if reference_time is not None else datetime.now(timezone.utc).timestamp()
    age=None if stamp is None or stamp>current+60 else max(0,(current-stamp)/86400)
    freshness=0 if age is None else max(0,1-age/365)
    evidence_factor=.5 if age is None else 1 if age<=90 else max(.5,1-(age-90)/550)
    payload=json.loads(certificate['payload']) if isinstance(certificate['payload'],str) else certificate['payload']
    tested=set(payload.get('tested_skills',[]));requested=set(need['skills']);claimed=set(candidate.get('all_skills',[]))
    proofs=[a for a in candidate.get('achievements',[]) if a.get('active') and a.get('individual_evidence')]
    values=[]
    for skill in requested:
        values.append(evidence_factor if skill in tested else .75 if any(skill in a.get('task_skills',[]) for a in proofs) else 0)
    known=set(claimed)|tested|{skill for proof in proofs for skill in proof.get('task_skills',[])}
    known_coverage=len(requested&known)/len(requested)
    evidence_coverage=sum(values)/len(requested)
    unknown=list(previous['unknown'])
    if age is None:unknown.append('Дата проверки')
    if need['salary_type']!='net':unknown.append('Сопоставимость зарплаты до и после налогов')
    components={'objective':round(.55*certificate['score'],4),'declared_stack':round(20*known_coverage,4),'requested_evidence':round(20*evidence_coverage,4),'recency':round(3*freshness,4),'fsp':min(2,len(proofs)),'adjacent_penalty':-8 if certificate['grade']!=need['grade'] else 0,'unknown_penalty':-2*len(unknown)}
    total=round(max(0,min(100,sum(components.values()))),2)
    result=dict(previous,score=total,algorithm=VERSION,unknown=unknown,fsp_bonus=components['fsp'])
    result['score_components']=components
    result['evidence_summary']={'requested_skills':len(requested),'tested':len(requested&tested),'source_backed':sum(skill not in tested and any(skill in a.get('task_skills',[]) for a in proofs) for skill in requested),'claimed_only':sum(skill in claimed and skill not in tested and not any(skill in a.get('task_skills',[]) for a in proofs) for skill in requested),'age_days':round(age,2) if age is not None else None,'freshness_factor':round(freshness,4),'tested_evidence_factor':round(evidence_factor,4),'uncertainty':'Missing evidence is not established lack of competence; model scores are not calibrated across test versions.'}
    documented=sum(skill in tested or any(skill in a.get('task_skills',[]) for a in proofs) for skill in requested)
    result['explanation']=[previous['explanation'][0],f"Проверки или источники по навыкам: {documented} из {len(requested)} · вклад {round(components['requested_evidence'],1)} балла",f"Результат заданий: {certificate['score']}% · вклад {round(components['objective'],1)} балла",f"Совпадение технологий: вклад {round(components['declared_stack'],1)} балла",f"Давность проверки: {'дата неизвестна' if age is None else str(round(age))+' дн.'} · вклад {round(components['recency'],1)} балла",f"Индивидуальные сведения ФСП: вклад {components['fsp']} балла"]
    if components['adjacent_penalty']:result['explanation'].append('Соседняя категория разрешена фильтром; применяется поправка8 баллов.')
    if unknown:result['explanation'].append('Есть неопределённые условия; это не установленное несоответствие.')
    return result
