import math
import re
import time
import os
import numpy as np
from .db import connection
from .domain import candidate_document, vacancy_document
from .taxonomy import SKILLS, normalize_text
from .ml import engine

MATCH_VERSION="constraints-bm25-qwen-rrf-evidence-v2"

def matrix(candidate,vacancy):
    result=[]
    claimed=set(candidate.get("skills",[]))
    for required,skills in [(True,vacancy.get("required_skills",[])),(False,vacancy.get("optional_skills",[]))]:
        for skill in skills:
            proofs=[a for a in candidate["achievements"] if a["active"] and a.get("individual_evidence")
                    and skill in a.get("task_skills",[])]
            status="demo_source" if proofs and all(a.get("demo") for a in proofs) else "source" if proofs else "claimed" if skill in claimed else "unknown"
            result.append({"skill":skill,"label":SKILLS.get(skill,(skill,[]))[0],"required":required,"status":status,
                           "claim_origin":candidate.get('resume_provenance',{}).get('skills',{}).get(skill,{'origin':'manual','verification':'claimed'}) if skill in claimed else None,
                           "evidence":[{"id":a["id"],"title":a["title"],"contest":a["contest"],"date":a["date"],
                                        "role":a.get("role_label"),"result":a["result"],"demo":a.get("demo",False)}
                                       for a in proofs]})
    return result

def constraints(c,v):
    blocked,unknown=[],[]
    if (v.get("salary_max") is not None and c.get("salary_min") is not None
            and v.get("currency")=="RUB" and v.get("salary_type")=="net"):
        if v["salary_max"]<c["salary_min"]:
            blocked.append("Зарплатный диапазон ниже ожиданий")
    else:
        unknown.append("Нужно уточнить зарплату и сопоставимость выплат")
    if v.get("experience_min") is not None and c.get("experience_years") is not None:
        if c["experience_years"]<v["experience_min"]:
            blocked.append("Заявленный опыт ниже указанного минимума")
    else:
        unknown.append("Нужно уточнить опыт")
    if v.get("work_mode") and c.get("work_modes"):
        if v["work_mode"] not in c["work_modes"]:
            blocked.append("Формат работы не совпадает")
    else:
        unknown.append("Нужно уточнить формат работы")
    if v.get("work_mode") in ["office","hybrid"] and v.get("city") and c.get("city"):
        if v["city"].casefold()!=c["city"].casefold() and not c.get("relocate",False):
            blocked.append("Город работы не совпадает")
    elif v.get('work_mode') in ['office','hybrid']:
        unknown.append('Нужно уточнить город работы и возможность переезда')
    return {"eligible":not blocked,"blocked":blocked,"unknown":unknown}

def assessment(c,v):
    rows=matrix(c,v)
    total=sum(2 if x["required"] else 1 for x in rows) or 1
    covered=sum((2 if x["required"] else 1) for x in rows if x["status"]!="unknown")
    demonstrated=sum((2 if x["required"] else 1) for x in rows if x["status"] in ["source","demo_source"])
    coverage=round(100*covered/total)
    evidence=round(100*demonstrated/total)
    return {"coverage":coverage,"evidence_coverage":evidence,"matrix":rows,
            "missing":[x["label"] for x in rows if x["required"] and x["status"]=="unknown"],
            "constraints":constraints(c,v),"version":MATCH_VERSION,
            "profile_version":c["version"],"vacancy_version":v["version"]}

def lexical_rank(query,documents,entity_type):
    ids=[d["id"] for d in documents]
    if not ids:
        return []
    tokens=re.findall(r"[\w]+",normalize_text(query))
    tokens=list(dict.fromkeys(t for t in tokens if len(t)>1))[:55]
    if not tokens:
        return ids
    match=" OR ".join('"'+t+'"' for t in tokens)
    with connection() as conn:
        rows=conn.execute("SELECT entity_id FROM search_index WHERE to_tsvector('simple',text) @@ websearch_to_tsquery('simple',?) AND entity_type=? AND entity_id IN ("+','.join('?' for _ in ids)+") ORDER BY ts_rank(to_tsvector('simple',text),websearch_to_tsquery('simple',?)) DESC",[match,entity_type,*ids,match]).fetchall()
    ranked=[r[0] for r in rows]
    return ranked+[id for id in ids if id not in ranked]

def rank_candidates(v,candidates,mode="hybrid"):
    started=time.perf_counter()
    pool=[]
    for c in candidates:
        match=assessment(c,v)
        if match["constraints"]["eligible"]:
            pool.append((c,match))
    if not pool:
        return [],{"mode":"baseline","elapsed_ms":round((time.perf_counter()-started)*1000),"model":engine.status()}
    docs=[c for c,_ in pool]
    query=vacancy_document(v)
    lexical=lexical_rank(query,docs,"candidate")
    baseline=sorted(pool,key=lambda item:(.85*item[1]["coverage"]+.15*(100 if item[0]["role"]==v["role"] else 0)),reverse=True)
    actual_mode="baseline"
    semantic_scores={}
    rerank_scores={}
    rerank_limit=max(1,min(100,int(os.environ.get('FSP_RERANK_TOP_K','20'))))
    if mode!="baseline" and engine.state=="ready":
        try:
            q=engine.embed([query],query=True)[0]
            vectors=engine.embed([candidate_document(c) for c in docs])
            semantic_scores={c["id"]:float(score) for c,score in zip(docs,vectors@q)}
            actual_mode="semantic" if mode=="semantic" else "hybrid"
            if mode=="hybrid":
                positions={identity:i+1 for i,identity in enumerate(lexical)}
                base_positions_temp={c['id']:i+1 for i,(c,_) in enumerate(baseline)}
                semantic_order=sorted(semantic_scores,key=semantic_scores.get,reverse=True)
                semantic_pos={identity:i+1 for i,identity in enumerate(semantic_order)}
                shortlist=sorted(docs,key=lambda c:sum(1/(60+p) for p in [positions[c['id']],base_positions_temp[c['id']],semantic_pos[c['id']]]),reverse=True)[:rerank_limit]
                scores=engine.rerank(query,[candidate_document(c) for c in shortlist])
                rerank_scores={c["id"]:score for c,score in zip(shortlist,scores)}
        except Exception as exc:
            # Retain observable fallback rather than silently reporting a neural result.
            import logging
            logging.getLogger(__name__).exception("Inference fallback")
            actual_mode="baseline"
    lexical_positions={id:i+1 for i,id in enumerate(lexical)}
    semantic_positions={id:i+1 for i,id in enumerate(sorted(semantic_scores,key=semantic_scores.get,reverse=True))}
    base_positions={c["id"]:i+1 for i,(c,_) in enumerate(baseline)}
    for c,m in pool:
        role=1 if c["role"]==v["role"] or v["role"] in [r["id"] for r in c["categories"]] else 0
        rrf=sum(1/(60+p) for p in [lexical_positions[c["id"]],base_positions[c["id"]]] if p)
        if semantic_positions:
            rrf+=1/(60+semantic_positions[c["id"]])
        if actual_mode=="baseline":
            value=.85*m["coverage"]/100+.15*role
        elif actual_mode=="semantic":
            value=.65*m["coverage"]/100+.15*role+.20*semantic_scores[c["id"]]
        else:
            normalized_rrf=rrf/(3/61)
            value=.55*m["coverage"]/100+.12*role+.15*normalized_rrf+.10*rerank_scores.get(c["id"],0)+.08*m["evidence_coverage"]/100
        m["rank_score"]=round(value,6)
        m["signals"]={"bm25_rank":lexical_positions[c["id"]],"semantic_similarity":semantic_scores.get(c["id"]),
                      "reranker_score":rerank_scores.get(c["id"]),"evidence_bonus":m["evidence_coverage"]/100 if actual_mode=="hybrid" else 0}
        m["mode"]=actual_mode
        m['reranked']=c['id'] in rerank_scores
    pool.sort(key=lambda item:item[1]["rank_score"],reverse=True)
    items=[dict(c,match=m) for c,m in pool]
    return items,{"mode":actual_mode,"elapsed_ms":round((time.perf_counter()-started)*1000),
                  "version":MATCH_VERSION,"model":engine.status(),"considered":len(candidates),"eligible":len(pool),
                  "reranked":len(rerank_scores),"rerank_limit":rerank_limit}

def rank_vacancies(candidate,vacancies,mode="hybrid"):
    started=time.perf_counter()
    items=[]
    for v in vacancies:
        m=assessment(candidate,v)
        if m["constraints"]["eligible"]:
            items.append((v,m))
    actual_mode="baseline"
    if mode!="baseline" and engine.state=="ready" and items:
        try:
            query=candidate_document(candidate)
            vectors=engine.embed([vacancy_document(v) for v,_ in items])
            q=engine.embed([query],query=True)[0]
            for (v,m),vector in zip(items,vectors):
                m["rank_score"]=.68*m["coverage"]/100+.15*(candidate["role"]==v["role"])+.10*float(vector@q)+.07*m["evidence_coverage"]/100
                m["mode"]="semantic"
            actual_mode="semantic"
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Vacancy inference fallback")
    if actual_mode=="baseline":
        for v,m in items:
            m["rank_score"]=.85*m["coverage"]/100+.15*(candidate["role"]==v["role"])
            m["mode"]="baseline"
    items.sort(key=lambda item:item[1]["rank_score"],reverse=True)
    return [dict(v,match=m) for v,m in items],{"mode":items[0][1]["mode"] if items else "baseline",
                                             "elapsed_ms":round((time.perf_counter()-started)*1000),"model":engine.status()}
