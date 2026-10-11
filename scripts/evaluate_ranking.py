"""Actual rules/BM25/neural/full comparison on frozen, SMALL synthetic gold."""
import argparse,hashlib,json,math,os,re,statistics,sys,tempfile,time
from collections import Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
p=argparse.ArgumentParser();p.add_argument('--semantic',action='store_true');p.add_argument('--dataset',default='benchmarks/categories-v1.json');p.add_argument('--output',default='artifacts/stage04-ranking-evaluation.json');args=p.parse_args()
os.environ['FSP_ENABLE_ML']='1' if args.semantic else '0';os.environ['FSP_TORCH_THREADS']='4'
from backend import db
from backend.recruiting import match_need
from backend.ml import engine
dataset_path=ROOT/args.dataset;dataset=json.loads(dataset_path.read_text(encoding='utf-8'))
def tokens(text):return re.findall(r'\w+',text.lower())
def bm25(query,documents):
    corpus=[tokens(x) for x in documents];n=len(corpus);average=sum(map(len,corpus))/max(1,n);result=[]
    for document in corpus:
        counts=Counter(document);score=0
        for term in set(tokens(query)):
            df=sum(term in other for other in corpus);idf=math.log(1+(n-df+.5)/(df+.5));frequency=counts[term]
            score+=idf*frequency*2.2/(frequency+1.2*(.25+.75*len(document)/max(1,average)))
        result.append(score)
    return result
def dcg(values):return sum((2**grade-1)/math.log2(i+2) for i,grade in enumerate(values))
def metrics(order,gold):
    values=[gold[x] for x in order];ideal=sorted(gold.values(),reverse=True);relevant=sum(x>=2 for x in gold.values())
    return {'ndcg_at_3':round(dcg(values[:3])/dcg(ideal[:3]),4),'precision_at_3':round(sum(v>=2 for v in values[:3])/3,4),'recall_at_3':round(sum(v>=2 for v in values[:3])/max(1,relevant),4),'irrelevant_returned':sum(v==0 for v in values)}
results=[];startup=time.perf_counter()
with tempfile.TemporaryDirectory(prefix='fsp-ranking-') as temporary:
    db.DB_PATH=Path(temporary)/'benchmark.db';db.DATABASE_URL='';db.initialize();engine.prepare();model_startup=time.perf_counter()-startup
    if args.semantic and engine.state!='ready':raise RuntimeError('Real semantic model is not ready: '+str(engine.error))
    for case in dataset['cases']:
        need={'id':case['id'],'payload':db.dumps({'specialization':case['spec'],'grade':case['grade'],'skills':case['skills'],'work_mode':'remote','salary_type':'net','salary_max':150000,'city':''})}
        candidates=[];gold={};documents=[]
        with db.connection() as conn:
            for name,score,fsp,grade,spec,salary,skills,judgment in case['profiles']:
                identity=case['id']+':'+name;gold[identity]=judgment
                candidate={'id':identity,'role':spec,'all_skills':skills,'salary_min':salary,'work_modes':['remote'],'achievements':[{'active':True,'individual_evidence':True,'task_skills':[]} for _ in range(fsp)]}
                conn.execute('INSERT INTO candidates(id,payload) VALUES(?,?)',(identity,db.dumps(candidate)))
                if grade:conn.execute('INSERT INTO grade_certifications VALUES(?,?,?,?,?,NULL,?,?,?,1)',('cert:'+identity,identity,spec,grade,score,'demo',db.dumps({'calibration':'synthetic_fixture','demo':True,'tested_skills':[]}),db.now()))
                candidates.append(candidate)
                documents.append(f"Specialization {spec}. Skills: {' '.join(skills)}. "+(f'Assumed verified grade {grade}.' if grade else 'Grade unconfirmed.')+f' Salary minimum {salary} RUB.')
            full=[match_need(conn,c,need,{}) for c in candidates]
        query=f"Specialization {case['spec']}. Verified grade {case['grade']}. Skills: {' '.join(case['skills'])}. Salary maximum 150000 RUB."
        keyword=[len(set(case['skills'])&set(c['all_skills'])) for c in candidates]
        lexical=bm25(query,documents)
        methods={'rules':keyword,'bm25':lexical,'full_category_evidence':[x['score'] if x else None for x in full]}
        if args.semantic:
            vectors=engine.embed(documents);q=engine.embed([query],query=True)[0];methods['semantic']=[float(v@q) for v in vectors]
        for method,scores in methods.items():
            indices=sorted([i for i,s in enumerate(scores) if s is not None],key=lambda i:(-scores[i],candidates[i]['id']))
            ids=[candidates[i]['id'] for i in indices];results.append({'case':case['id'],'split':case['split'],'method':method,'ranked_ids':ids,**metrics(ids,gold)})
summary=[]
for split in ['development','holdout']:
    for method in sorted({r['method'] for r in results}):
        rows=[r for r in results if r['split']==split and r['method']==method]
        summary.append({'split':split,'method':method,'queries':len(rows),**{m:round(statistics.mean(r[m] for r in rows),4) for m in ['ndcg_at_3','precision_at_3','recall_at_3']},'irrelevant_returned':sum(r['irrelevant_returned'] for r in rows)})
report={'dataset_sha256':hashlib.sha256(dataset_path.read_bytes()).hexdigest(),'annotation':dataset['annotation'],'case_count':len(dataset['cases']),'methods':{'rules':'naive skill intersection','bm25':'k1=1.2, b=0.75, tokenized documents','semantic':'actual pinned Qwen3-Embedding-0.6B, cosine' if args.semantic else 'not run','full_category_evidence':'actual production match_need '+os.environ.get('FSP_MATCH_POLICY','category-evidence-v1')},'model':engine.status(),'model_startup_seconds':round(model_startup,3),'elapsed_seconds':round(time.perf_counter()-startup,3),'summary':summary,'per_query':results,'limitations':'Team-authored controlled synthetic relevance labels. No independent expert labels or real candidate data; this does not establish labor-market quality or professional grades.'}
target=ROOT/args.output;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(summary))
