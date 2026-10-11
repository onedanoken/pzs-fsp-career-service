"""Frozen relevance rules written independently of match_need score weights."""
import json,random,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.assessment_catalog import SPECIALIZATIONS,GRADES
rng=random.Random(20261007);cases=[]
for spec,definition in SPECIALIZATIONS.items():
    requested=definition['skills'][:2]
    for grade in GRADES:
        for variant in range(6):
            profiles=[]
            for i in range(24):
                kind=i%6;score=rng.randint(90,100) if kind==0 else rng.randint(70,85)
                given_grade=None if kind==2 else GRADES[(GRADES.index(grade)+1)%4] if kind==3 else grade
                given_spec=next(s for s in SPECIALIZATIONS if s!=spec) if kind==4 else spec
                salary=200000 if kind==5 else rng.choice([80000,100000,120000]);skills=requested[:] if i<18 else requested[:1]
                # Hard category/conditions gates precede quality. The score weights are not read here.
                judgment=0 if kind in [2,3,4,5] else 1 if len(skills)<len(requested) else 3 if score>=90 else 2
                profiles.append(['p'+str(i),score if given_grade else None,rng.randint(0,4),given_grade,given_spec,salary,skills,judgment])
            rng.shuffle(profiles)
            cases.append({'id':spec+'-'+grade+'-'+str(variant),'split':'development' if variant<3 else 'holdout','spec':spec,'grade':grade,'skills':requested,'profiles':profiles})
value={'version':'team-ranking-reference-v2','annotation':{'origin':'Team-defined category, explicit-condition, stack-completeness and objective-score rules; labels frozen before algorithm comparison','independent_expert_review':False,'real_personal_data':False,'weights_tuned_on_dataset':False,'rubric':'0 wrong/unconfirmed category or salary conflict;1 eligible partial stack;2 full stack objective70-85;3 full stack objective90-100','split':'Separate deterministic profile pools, development variants0-2 and holdout3-5; not an independent market sample'},'cases':cases}
path=ROOT/'benchmarks/ranking-v2.json';path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'queries':len(cases),'profiles':sum(len(c['profiles']) for c in cases),'holdout_queries':sum(c['split']=='holdout' for c in cases)}))
