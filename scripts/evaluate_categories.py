"""Frozen synthetic category benchmark; no claim of calibrated grades or market fit."""
import hashlib,json,math,os,statistics,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend import db
from backend.recruiting import match_need
dataset_path=ROOT/'benchmarks/categories-v1.json';dataset=json.loads(dataset_path.read_text(encoding='utf-8'));results=[]
def dcg(values):return sum((2**grade-1)/math.log2(i+2) for i,grade in enumerate(values))
with tempfile.TemporaryDirectory(prefix='fsp-evaluation-') as temporary:
    db.DB_PATH=Path(temporary)/'eval.db';db.DATABASE_URL='';db.initialize()
    with db.connection() as conn:
        for case in dataset['cases']:
            need={'id':case['id'],'payload':db.dumps({'specialization':case['spec'],'grade':case['grade'],'skills':case['skills'],'work_mode':'remote','salary_type':'net','salary_max':150000,'city':''})}
            candidates=[];gold={}
            for name,score,fsp,grade,spec,salary,skills,judgment in case['profiles']:
                identity=case['id']+':'+name;gold[identity]=judgment
                candidate={'id':identity,'role':spec,'all_skills':skills,'salary_min':salary,'work_modes':['remote'],'achievements':[{'active':True,'individual_evidence':True,'task_skills':[]} for _ in range(fsp)]}
                conn.execute('INSERT INTO candidates(id,payload) VALUES(?,?)',(identity,db.dumps(candidate)))
                if grade:conn.execute('INSERT INTO grade_certifications VALUES(?,?,?,?,?,NULL,?,?,?,1)',('cert:'+identity,identity,spec,grade,score,'demo',db.dumps({'calibration':'synthetic_fixture','demo':True,'tested_skills':[]}),db.now()))
                candidates.append(candidate)
            for method in ['keyword_only','category_evidence']:
                if method=='keyword_only':ranked=sorted(candidates,key=lambda c:(-len(set(c['all_skills'])&set(case['skills'])),c['id']))
                else:
                    matched=[(c,match_need(conn,c,need,{})) for c in candidates];ranked=[c for c,m in sorted([(c,m) for c,m in matched if m],key=lambda x:(-x[1]['score'],x[0]['id']))]
                grades=[gold[c['id']] for c in ranked];ideal=sorted(gold.values(),reverse=True)
                results.append({'case':case['id'],'split':case['split'],'method':method,'ranked_ids':[c['id'] for c in ranked],'ndcg_at_3':round(dcg(grades[:3])/dcg(ideal[:3]),4),'precision_at_1':int(bool(grades) and grades[0]>=2),'irrelevant_returned':sum(g==0 for g in grades)})
summary=[]
for split in ['development','holdout']:
    for method in ['keyword_only','category_evidence']:
        rows=[r for r in results if r['split']==split and r['method']==method]
        summary.append({'split':split,'method':method,'queries':len(rows),'ndcg_at_3':round(statistics.mean(r['ndcg_at_3'] for r in rows),4),'precision_at_1':statistics.mean(r['precision_at_1'] for r in rows),'irrelevant_returned':sum(r['irrelevant_returned'] for r in rows)})
report={'dataset_sha256':hashlib.sha256(dataset_path.read_bytes()).hexdigest(),'annotation':dataset['annotation'],'summary':summary,'per_query':results,'semantic_model_comparison':{'available':False,'reason':'Optional semantic engine is not enabled; no invented metrics'},'limitations':'6 simple synthetic pools, 3 holdout queries. Not human calibration, no independent external assessor, no claim about labor market.'}
(ROOT/'artifacts/stage03-category-evaluation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(summary))
