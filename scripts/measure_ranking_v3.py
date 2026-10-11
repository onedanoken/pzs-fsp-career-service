"""Freeze scenario-family labels before comparing both policies on identical records."""
import argparse,copy,hashlib,json,math,random,statistics,sys
from datetime import datetime,timedelta,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

DEVELOPMENT=['direct_proof','recency','source_vs_claim','permitted_neighbors']
EVALUATION=['partial_proof_mix','adversarial_resume','joint_conditions','mixed_stale_sources','unknown_metadata']

def corpus():
    from backend.assessment_catalog import SPECIALIZATIONS,GRADES
    anchor=datetime(2026,10,8,12,tzinfo=timezone.utc);rng=random.Random(20261008);cases=[]
    for family in DEVELOPMENT+EVALUATION:
        for spec,definition in SPECIALIZATIONS.items():
            for grade in GRADES:
                for variant in range(3):
                    skills=definition['skills'][:2 if family in DEVELOPMENT else 3]
                    city='Пермь';mode='hybrid' if family=='joint_conditions' else 'remote'
                    need={'specialization':spec,'grade':grade,'skills':skills,'salary_type':'net','salary_max':150000,'city':city,'work_mode':mode}
                    profiles=[]
                    kinds=['full','full_close','claim_high','partial','stale','source','neighbor','wrong_specialization','salary_conflict','mode_conflict','missing_salary','team_claim']
                    rng.shuffle(kinds)
                    for kind in kinds:
                        tested=skills[:] if kind in ['full','full_close','stale','neighbor','wrong_specialization','salary_conflict','mode_conflict','missing_salary'] else skills[:1] if kind=='partial' else []
                        score={'full':90,'full_close':83,'claim_high':99,'partial':95,'stale':99,'source':85,'neighbor':95,'team_claim':97}.get(kind,90)+rng.randint(-2,2)
                        score=min(100,score);age=420 if kind=='stale' else rng.randint(0,30)
                        actual_grade=GRADES[(GRADES.index(grade)+1)%4] if kind=='neighbor' else grade
                        if kind=='neighbor' and grade=='Senior':actual_grade='Middle'
                        actual_spec=next(s for s in SPECIALIZATIONS if s!=spec) if kind=='wrong_specialization' else spec
                        proof=[{'active':True,'individual_evidence':True,'task_skills':skills}] if kind=='source' else [{'active':True,'individual_evidence':False,'task_skills':skills} for _ in range(8)] if kind=='team_claim' else []
                        candidate={'id':kind,'role':actual_spec,'all_skills':skills[:],'salary_min':240000 if kind=='salary_conflict' else None if kind=='missing_salary' else 110000,'work_modes':['office'] if kind=='mode_conflict' else [mode],'city':'Москва' if family=='joint_conditions' and kind=='team_claim' else city,'relocate':False,'achievements':proof}
                        date=(anchor-timedelta(days=age)).isoformat()
                        if family=='unknown_metadata' and kind in ['partial','stale']:date='unknown-date'
                        if family=='adversarial_resume' and kind in ['claim_high','team_claim']:candidate.update(resume_text=' '.join(skills*60),all_skills=skills+['unrelated']*30)
                        if family=='mixed_stale_sources' and kind=='source':date=(anchor-timedelta(days=210)).isoformat()
                        cert={'id':'cert_'+kind,'candidate_id':kind,'specialization':actual_spec,'grade':actual_grade,'score':score,'status':'demo','created_at':date,'payload':json.dumps({'calibration':'synthetic_reference','tested_skills':tested,'demo':True})}
                        # Judgments refer to evidence supplied to this scenario, not a score formula.
                        label=3 if kind in ['full','full_close','source'] else 2 if kind in ['partial','neighbor','missing_salary'] else 1
                        neighbors=family in ['permitted_neighbors','partial_proof_mix','joint_conditions','unknown_metadata']
                        if kind in ['wrong_specialization','salary_conflict','mode_conflict'] or kind=='neighbor' and not neighbors or mode!='remote' and candidate['city']!=city:label=0
                        if family=='mixed_stale_sources' and kind=='source':label=2
                        if family=='unknown_metadata' and kind=='partial':label=1
                        profiles.append({'id':kind,'candidate':candidate,'certificates':[cert],'reference_relevance':label})
                    controls=['wrong_specialization','salary_conflict','mode_conflict']
                    keep={'direct_proof':['full','claim_high','partial'],
                          'recency':['full_close','stale','missing_salary'],
                          'source_vs_claim':['source','team_claim','claim_high','partial'],
                          'permitted_neighbors':['full_close','neighbor','source'],
                          'partial_proof_mix':['full','partial','source'],
                          'adversarial_resume':['full_close','claim_high','team_claim','source'],
                          'joint_conditions':['full','missing_salary','neighbor'],
                          'mixed_stale_sources':['stale','source','partial'],
                          'unknown_metadata':['missing_salary','partial','claim_high']}[family]
                    by={p['id']:p for p in profiles};profiles=[p for p in profiles if p['id'] in keep+controls]
                    def clone(kind,identity,label):
                        value=copy.deepcopy(by[kind]);value['id']=identity;value['candidate']['id']=identity;value['reference_relevance']=label
                        for cert in value['certificates']:cert['id']='cert_'+identity;cert['candidate_id']=identity
                        return value
                    if family=='partial_proof_mix':
                        mixed=clone('full_close','mixed_partial_source',3)
                        mixed['certificates'][0]['payload']=json.dumps({'calibration':'synthetic_reference','tested_skills':skills[:1],'demo':True})
                        mixed['candidate']['achievements']=[{'active':True,'individual_evidence':True,'task_skills':skills[1:]}]
                        profiles.append(mixed)
                    elif family=='adversarial_resume':
                        wrong=clone('team_claim','wrong_scope_individual',1);wrong['candidate']['achievements']=[{'active':True,'individual_evidence':True,'task_skills':['unrelated']}]
                        lower=clone('full_close','lower_score_full_proof',2);lower['certificates'][0]['score']=74
                        profiles.extend([wrong,lower])
                    elif family=='joint_conditions':
                        relocated=clone('full_close','relocation_allowed',3);relocated['candidate'].update(city='Москва',relocate=True)
                        city_conflict=clone('full_close','city_conflict',0);city_conflict['candidate'].update(city='Москва',relocate=False)
                        unconfirmed=clone('claim_high','unconfirmed',0);unconfirmed['certificates']=[]
                        profiles.extend([relocated,city_conflict,unconfirmed])
                    elif family=='mixed_stale_sources':
                        mixed=clone('full_close','fresh_partial_with_sources',3);mixed['certificates'][0]['score']=86
                        mixed['certificates'][0]['payload']=json.dumps({'calibration':'synthetic_reference','tested_skills':skills[:1],'demo':True})
                        mixed['candidate']['achievements']=[{'active':True,'individual_evidence':True,'task_skills':skills[1:]}]
                        profiles.append(mixed)
                    elif family=='unknown_metadata':
                        double=clone('full_close','double_cert',3);adjacent=copy.deepcopy(double['certificates'][0]);adjacent.update(id='prior_neighbor',grade='Middle' if grade=='Senior' else GRADES[GRADES.index(grade)+1],score=99)
                        double['certificates'].insert(0,adjacent)
                        missing_date=clone('full_close','missing_date_full',2);missing_date['certificates'][0].update(score=88,created_at='unknown-date')
                        profiles.extend([double,missing_date])
                    cases.append({'id':family+':'+spec+':'+grade+':'+str(variant),'family':family,'split':'development' if family in DEVELOPMENT else 'evaluation','need':need,'filters':{'adjacent_grades':neighbors},'profiles':profiles})
    return {'version':'ranking-scenario-reference-v3','anchor':anchor.timestamp(),'development_families':DEVELOPMENT,'evaluation_families':EVALUATION,'labels':'Team reference:3 current full evidence;2 partial/permitted-neighbor/uncertain conditions;1 declared or stale evidence;0 explicit incompatible category/conditions. No score weights used to assign labels.','real_people':False,'independent_experts':False,'cases':cases}

def dcg(values):return sum((2**v-1)/math.log2(i+2) for i,v in enumerate(values))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='.local/ranking-v3-experiment.json');args=parser.parse_args()
    data=corpus();assert not set(data['development_families'])&set(data['evaluation_families'])
    raw=json.dumps(data,ensure_ascii=False,sort_keys=True).encode();digest=hashlib.sha256(raw).hexdigest()
    frozen=ROOT/'.local/ranking-v3-frozen-corpus.json';frozen.parent.mkdir(exist_ok=True);frozen.write_bytes(raw)
    # Policies are loaded only after the frozen reference has been written.
    from backend.recruiting import match_need
    from backend.evidence_ranking import WEIGHTS
    records=[]
    for case in data['cases']:
        need={'payload':json.dumps(case['need'])}
        gold={p['id']:p['reference_relevance'] for p in case['profiles']}
        ideal=dcg(sorted(gold.values(),reverse=True)[:3])
        for policy in ['category-evidence-v1','category-evidence-v2']:
            scored=[]
            for p in case['profiles']:
                result=match_need(None,p['candidate'],need,case['filters'],p['certificates'],policy=policy,reference_time=data['anchor'])
                if result:scored.append((p['id'],result['score']))
            order=[identity for identity,score in sorted(scored,key=lambda x:(-x[1],x[0]))];labels=[gold[i] for i in order]
            positions={identity:i for i,identity in enumerate(order)}
            pairs=[(a,b) for a in gold for b in gold if gold[a]>gold[b]>0]
            pair_accuracy=sum(positions.get(a,len(order))<positions.get(b,len(order)) for a,b in pairs)/max(1,len(pairs))
            records.append({'case':case['id'],'family':case['family'],'split':case['split'],'policy':policy,'ndcg_at_3':dcg(labels[:3])/ideal,'precision_at_3':sum(v>=2 for v in labels[:3])/3,'pairwise_preference_accuracy':pair_accuracy,'forbidden_returned':sum(v==0 for v in labels),'order':order})
    summary=[]
    for split in ['development','evaluation']:
        for policy in ['category-evidence-v1','category-evidence-v2']:
            selected=[r for r in records if r['split']==split and r['policy']==policy]
            summary.append({'split':split,'policy':policy,'queries':len(selected),'ndcg_at_3':round(statistics.mean(r['ndcg_at_3'] for r in selected),4),'precision_at_3':round(statistics.mean(r['precision_at_3'] for r in selected),4),'pairwise_preference_accuracy':round(statistics.mean(r['pairwise_preference_accuracy'] for r in selected),4),'forbidden_returned':sum(r['forbidden_returned'] for r in selected)})
    held={r['policy']:r for r in summary if r['split']=='evaluation'};gain=held['category-evidence-v2']['ndcg_at_3']-held['category-evidence-v1']['ndcg_at_3']
    accepted=all(r['forbidden_returned']==0 for r in summary) and gain>=.03 and held['category-evidence-v2']['precision_at_3']>=held['category-evidence-v1']['precision_at_3']
    report={'baseline_commit':'b05ea0e4f9352d05741c333b355bee34d3cd9a9b','dataset_sha256':digest,'queries':len(data['cases']),'profiles':sum(len(c['profiles']) for c in data['cases']),'split':'Disjoint scenario-family sets, not new numeric variants of one family','weights':WEIGHTS,'labels_frozen_before_policy_loading':True,'summary':summary,'held_ndcg_gain':round(gain,4),'acceptance_passed':accepted,'per_query':records,'limits':['Controlled team-reference evidence, not human qualification or market hiring quality.','No comparison to competitors on unrelated metrics.','Synthetic certificate fields are assumed valid; current task bank does not verify every whole technology stack.','No parameter tuning on the evaluation families.']}
    target=ROOT/args.output;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({k:report[k] for k in ['queries','profiles','summary','held_ndcg_gain','acceptance_passed']}),flush=True)
    if not accepted:raise SystemExit('Keep production v1: registered acceptance failed')
if __name__=='__main__':main()
