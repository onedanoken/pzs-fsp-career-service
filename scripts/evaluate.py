"""Honest frozen-fixture evaluation; no training or claim of market performance."""
import argparse,json,math,statistics,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend import db
from backend.seed import seed
from backend.domain import get_vacancy,list_candidates,rebuild_index
from backend.matching import rank_candidates,constraints
from backend.ml import engine

GOLD={
 "v_python":{"c_anna":3,"c_claims":2,"c_maria":1,"c_daria":1},
 "v_front":{"c_timur":3},
 "v_ml":{"c_maria":3},
 "v_dotnet":{"c_ilya":3},
 "v_qa":{"c_daria":3,"c_anna":1,"c_timur":1},
 "v_devops":{"c_alex":3},
 "v_security":{"c_sonya":3,"c_alex":1},
 "v_java":{"c_max":3},
 "v_intern":{"c_nikita":3},
}
def dcg(values):return sum((2**g-1)/math.log2(i+2) for i,g in enumerate(values))
def main():
 parser=argparse.ArgumentParser()
 parser.add_argument("--baseline-only",action="store_true")
 args=parser.parse_args()
 db.initialize();seed();rebuild_index()
 if not args.baseline_only:engine.prepare()
 candidates=[c for c in list_candidates() if c.get("demo")]
 details=[];results=[]
 for mode in ["baseline","semantic","hybrid"]:
  if mode!="baseline" and engine.state!="ready":
   results.append({"mode":mode,"available":False,"ndcg_at_5":None,"mrr":None,"median_ms":None});continue
  ndcgs=[];mrrs=[];warm=[];cold=[]
  for vacancy_id,grades in GOLD.items():
   vacancy=get_vacancy(vacancy_id)
   pool=[c for c in candidates if constraints(c,vacancy)["eligible"]]
   ideal=sorted([grades.get(c["id"],0) for c in pool],reverse=True)[:5]
   items,meta=rank_candidates(vacancy,candidates,mode)
   if meta["mode"]!=mode:raise RuntimeError("Unexpected fallback: "+json.dumps(meta))
   cold.append(meta["elapsed_ms"])
   actual=[grades.get(c["id"],0) for c in items]
   ndcg=dcg(actual[:5])/dcg(ideal) if dcg(ideal) else 0
   rr=next((1/(i+1) for i,g in enumerate(actual) if g>=2),0)
   ndcgs.append(ndcg);mrrs.append(rr)
   for _ in range(3):
    _,m=rank_candidates(vacancy,candidates,mode);warm.append(m["elapsed_ms"])
   details.append({"mode":mode,"vacancy_id":vacancy_id,"ndcg_at_5":ndcg,"mrr":rr,
                   "ranked_ids":[c["id"] for c in items],"cold_ms":meta["elapsed_ms"]})
   print(mode,vacancy_id,"ndcg",round(ndcg,3),"first",items[0]["id"] if items else None,"ms",meta["elapsed_ms"],flush=True)
  results.append({"mode":mode,"available":True,"ndcg_at_5":round(statistics.mean(ndcgs),6),
                  "mrr":round(statistics.mean(mrrs),6),"median_ms":round(statistics.median(warm)),
                  "first_pass_median_ms":round(statistics.median(cold))})
 report={"created_at":db.now(),"data_revision":db.revision(),"queries":len(GOLD),"candidates":len(candidates),
         "gold_version":"synthetic-role-tasks-v1","model":engine.status(),"results":results,"per_query":details,
         "note":"Разметка: 3 — целевой профиль с явным индивидуальным вкладом; 2 — целевой стек, заявленный самостоятельно; 1 — смежный профиль; 0 — нерелевантный. Идеальная выдача учитывает условия вакансии. Медиана измерена после прогрева кеша, первый проход сохранён отдельно. Девять простых синтетических запросов не доказывают качество на рынке или преимущество нейросетей. Нет независимого holdout; веса не обучались."}
 (ROOT/"artifacts").mkdir(exist_ok=True)
 (ROOT/"artifacts"/"evaluation.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
 (ROOT/"artifacts"/"gold.json").write_text(json.dumps(GOLD,ensure_ascii=False,indent=2),encoding="utf-8")
 print(json.dumps(results,ensure_ascii=False),flush=True)
if __name__=="__main__":main()
