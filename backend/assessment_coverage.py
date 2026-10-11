"""An explicit distinction between the blueprint and observations in one session."""
from .assessment_catalog import SPECIALIZATIONS
from .professional_items import LEVEL_CRITERIA
from .competency_feedback import LABELS

def coverage(specialization, grade, items, result=None, frozen=None):
    required=(frozen or {}).get('blueprint_competencies',SPECIALIZATIONS[specialization]['competencies'])
    objective=sorted({c for i in items if i['kind']!='explanation' for c in i.get('competencies',[i['competency']])})
    open_items=sorted({i['competency'] for i in items if i['kind']=='explanation'})
    reviewed=(result or {}).get('expert_review') or {}
    reviewed_competencies=sorted({i['competency'] for i in items if i['id']==reviewed.get('item_id')})
    observed=set(objective)|set(reviewed_competencies)
    missing=[c for c in required if c not in observed]
    return {'blueprint_competencies':required,'objective_observations':objective,'open_answer_competencies':open_items,'expert_reviewed_competencies':reviewed_competencies,'not_observed':missing,'not_observed_labels':[LABELS.get(c,c) for c in missing],'level_criteria':(frozen or {}).get('level_criteria',LEVEL_CRITERIA[grade]),'phase':'objective_results' if result else 'task_content','validation':(frozen or {}).get('validation','prototype_team_reference'),'notice':'Покрытие показывает только области заданий. Оно не доказывает полноту навыка, равную сложность вариантов или валидность грейда.'}
