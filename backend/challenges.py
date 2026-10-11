"""Externalizable, fully judged challenge pools; judgments never drive matching."""
import json
from .db import ROOT
from .taxonomy import categories,SKILLS

DEFAULT_DATASET=ROOT/'benchmarks'/'challenge-v1.json'

def load(path=DEFAULT_DATASET):
    data=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data.get('annotation'),dict) or not data.get('cases'):raise ValueError('Dataset requires annotation metadata and cases')
    seen=set()
    for case in data['cases']:
        if case['id'] in seen:raise ValueError('Duplicate case id')
        seen.add(case['id'])
        profiles=case['profiles'];ids=[c['id'] for c in profiles]
        if len(set(ids))!=len(ids) or set(ids)!=set(case['judgments']):raise ValueError('Every candidate in each pool must have one judgment')
        if any(grade not in [0,1,2,3] for grade in case['judgments'].values()):raise ValueError('Grades must be 0..3')
        if any(s not in SKILLS for c in profiles for s in c.get('skills',[])):raise ValueError('Unknown skill in dataset')
    return data

def materialize(case):
    vacancy={'id':'challenge_'+case['id'],'title':'Backend Python','role':'backend','company':'Синтетическая компания',
             'required_skills':[],'optional_skills':[],'salary_min':100000,'salary_max':180000,'currency':'RUB',
             'salary_type':'net','experience_min':0,'work_mode':'remote','city':'','description':'Разработка сервисов и API.',
             'version':1,'archived':False,'demo':True,**case['vacancy']}
    candidates=[]
    for original in case['profiles']:
        item={'name':'Тестовый участник','title':'Backend разработчик','role':'backend','skills':[],
              'salary_min':100000,'currency':'RUB','experience_years':3,'work_modes':['remote'],
              'city':'Москва','relocate':False,'bio':'','looking':True,'visible':True,'version':1,'demo':True,
              **{k:v for k,v in original.items() if k!='proof'}}
        item['id']=case['id']+'__'+original['id'];item['achievements']=[]
        if original.get('proof'):
            item['achievements']=[{'id':item['id']+'_proof','title':'Тестовое индивидуальное задание','contest':'Синтетический контрольный случай',
                'active':True,'version':1,'demo':True,'date':'2026-09-18','result':'Выполнено',
                'source':'Synthetic challenge','source_id':item['id']+'_proof',**original['proof']}]
        proven={s for a in item['achievements'] if a['active'] and a.get('individual_evidence') for s in a.get('task_skills',[])}
        item['all_skills']=sorted(set(item['skills'])|proven)
        item['categories']=categories(item,item['achievements'])
        item['skill_labels']={s:SKILLS[s][0] for s in item['all_skills']}
        candidates.append(item)
    return vacancy,candidates
