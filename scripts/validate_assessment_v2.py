"""Controlled team-reference experiment, not a human-grade calibration study."""
import argparse,hashlib,json,random,sqlite3,sys,time
from fractions import Fraction
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.work_cases_v2 import FAMILIES,generate_case,evaluate
from backend.assessment_catalog import GRADES

def independent(item):
    c=item['private']['condition'];expected=item['private']['expected'];f=c['family']
    if f=='cursor_page':
        with sqlite3.connect(':memory:') as conn:
            conn.execute('CREATE TABLE rows(id INTEGER,score INTEGER,deleted INTEGER,created INTEGER)');conn.executemany('INSERT INTO rows VALUES(?,?,?,?)',[(r['id'],r['score'],int(r.get('deleted',False)),r.get('created',0)) for r in c['records']])
            rows=conn.execute('SELECT id,score FROM rows WHERE(score>? OR(score=? AND id>?)) AND (?=0 OR(deleted=0 AND created<=?)) ORDER BY score,id LIMIT ?', (c['cursor'][0],c['cursor'][0],c['cursor'][1],int(c.get('snapshot_filter',False)),c.get('as_of',0),c['limit'])).fetchall()
        assert expected=={'page_ids':[x[0] for x in rows],'next_cursor':[rows[-1][1],rows[-1][0]] if rows else None}
    elif f=='rollout_budget':
        ceiling=lambda n,d:(n+d-1)//d
        pods=min(ceiling(c['replicas']*c['surge_percent'],100),c['free_cpu']//c['cpu_per_pod'],c['free_ram']//c['ram_per_pod'])
        if c.get('zone_slots'):
            while any((pods+2-z)//3>slots for z,slots in enumerate(c['zone_slots'])):pods-=1
        assert expected=={'max_new_pods':pods,'peak_cpu':pods*c['cpu_per_pod'],'peak_ram':pods*c['ram_per_pod'],'available_floor':c['replicas']-(c['replicas']*c['unavailable_percent']//100)}
    elif f=='failed_dag':
        def paths(name):
            row=c['jobs'][name]
            return [[name]] if not row['needs'] else [p+[name] for parent in row['needs'] for p in paths(parent)]
        paths_to_release=paths('release');duration=max(sum(c['jobs'][n]['duration'] for n in p) for p in paths_to_release)
        assert expected=={'critical_seconds':duration,'release_allowed':not any(set(p)&set(c['failed']) for p in paths_to_release)}
    elif f=='threshold_cost':
        viable=[(r['fp']*c['fp_cost']+r['fn']*c['fn_cost'],r['threshold']) for r in c['candidates'] if Fraction(r['tp'],r['tp']+r['fn'])>=Fraction(str(c['min_recall'])) and r['tp']+r['fp']<=c['review_budget']]
        minimum=min(viable);assert expected=={'threshold':minimum[1],'cost':minimum[0]}
    elif f=='event_ledger':
        with sqlite3.connect(':memory:') as conn:
            conn.execute('CREATE TABLE seen(id TEXT PRIMARY KEY)');balance=c['initial'];accepted=[]
            for row in c['events']:
                inserted=conn.execute('INSERT OR IGNORE INTO seen VALUES(?)',(row['id'],)).rowcount
                if inserted and balance+row['amount']>=0 and balance+row['amount']<=c.get('maximum',10**9):balance+=row['amount'];accepted.append(row['id'])
        assert expected=={'balance':balance,'accepted_ids':accepted}

def main():
    p=argparse.ArgumentParser();p.add_argument('--seeds',type=int,default=80);p.add_argument('--output',default='artifacts/assessment-v2-validation.json');a=p.parse_args()
    results=[];start=time.perf_counter()
    for spec,families in FAMILIES.items():
        for grade in GRADES:
            for family in families:
                per_split={}
                for split,interval in [('development',range(a.seeds//2)),('holdout',range(a.seeds//2,a.seeds))]:
                    passed=negative_rejected=stable=0;signatures=set()
                    for seed in interval:
                        item=generate_case(spec,grade,random.Random(seed+901),family);independent(item)
                        answer=item['private']['reference_answer'];gold=evaluate(item,answer);assert gold['all_checks_passed'];passed+=1
                        wrong=json.loads(answer)
                        key=next(iter(wrong));value=wrong[key]
                        if item['private']['condition']['family']=='mutation_suite':wrong['tests'][0]['accepted']=not wrong['tests'][0]['accepted']
                        elif isinstance(value,list):wrong[key]=value[1:] if value else ['unknown-id']
                        elif isinstance(value,dict):wrong[key]={**value,'unknown':{'state':'wrong'}}
                        elif type(value) is bool:wrong[key]=not value
                        elif type(value) is int:wrong[key]=value+1
                        else:wrong[key]='wrong'
                        rejected=evaluate(item,json.dumps(wrong));assert not rejected['all_checks_passed'],(spec,grade,family,seed);negative_rejected+=1
                        reordered=json.dumps(json.loads(answer),sort_keys=True,separators=(',',':'))
                        assert evaluate(item,reordered)['score']==gold['score'];stable+=1
                        signatures.add(hashlib.sha256(json.dumps(item['private']['condition'],sort_keys=True).encode()).hexdigest())
                    per_split[split]={'reference_solutions_accepted':passed,'known_defects_rejected':negative_rejected,'json_format_invariant':stable,'distinct_conditions':len(signatures),'cases':len(interval)}
                results.append({'specialization':spec,'grade':grade,'family':family,'splits':per_split})
    report={'version':'team-reference-validation-v2','origin':'controlled_synthetic_team_reference','human_or_independent_expert_study':False,'grade_mapping':'team_proposal_pilot_unvalidated','families':12,'role_grade_family_groups':len(results),'conditions_tested':sum(sum(v['cases'] for v in r['splits'].values()) for r in results),'independent_oracles':['SQL cursor ordering','SQL unique-event processing','integer rollout arithmetic','enumerated DAG paths','rational threshold constraints'],'results':results,'seconds':round(time.perf_counter()-start,2),'limits':'Separation of known correct/defective model solutions, not measured professional ability or market validity. Other families use declared reference conditions; answer formatting invariance is not arbitrary semantic paraphrase equivalence.'}
    path=ROOT/a.output;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({k:report[k] for k in ['families','conditions_tested','seconds','human_or_independent_expert_study']}))
if __name__=='__main__':main()
