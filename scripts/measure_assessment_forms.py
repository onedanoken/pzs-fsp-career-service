"""Reproducible controlled response simulation; never a human calibration report."""
import argparse,collections,hashlib,json,math,random,statistics,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.assessment_forms import CORE,screening
from backend.item_generator import generate
from backend.work_samples import add_journey,AUTO_PATH_VERSION

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='.local/assessment-form-experiment.json');parser.add_argument('--samples',type=int,default=1200);args=parser.parse_args()
    rng=random.Random(20261008);rows=[];content=[]
    grades=['Intern','Junior','Middle','Senior']
    profiles=[('weak',.25,False),('borderline',.70,False),('strong',.92,False),('one_weak_domain',.92,True)]
    for spec,core in CORE.items():
        for grade in grades:
            forms={}
            for length in [3,6,9,12]:
                try:items,plan=screening(spec,grade,20261008,count=length)
                except ValueError as error:
                    content.append({'spec':spec,'grade':grade,'length':length,'available':False,'reason':str(error)})
                    if length<=9:raise
                    continue
                forms['balanced_'+str(length)]=items
                counts=collections.Counter(i['competency'] for i in items)
                content.append({'spec':spec,'grade':grade,'length':length,'available':True,'quotas_ok':dict(counts)==plan['quota'],'mean_prompt_chars':round(statistics.mean(len(i['prompt']) for i in items)),'kinds':sorted({i['kind'] for i in items})})
            base=generate(spec,grade,allow_neural=False,qa_professional=spec=='qa',seed=20261008)
            forms['actual_v2_baseline']=[i for i in add_journey(base,spec,grade,AUTO_PATH_VERSION)['items'] if i['stage']=='screening']
            for name,ability,weak in profiles:
                probabilities={c:max(.02,min(.98,ability-.02*grades.index(grade))) for c in core}
                if weak:probabilities[core[-1]]=.15
                target=sum(probabilities.values())/len(core)*100
                for method,items in forms.items():
                    scores=[];passes=[]
                    for _ in range(args.samples):
                        # Two independent forms for one hypothetical response profile.
                        decisions=[]
                        for repeat in range(2):
                            observations=collections.defaultdict(list)
                            family_shock={}
                            for item in items:
                                c=item['competency'];family=item.get('family') or item['kind']
                                family_shock.setdefault(family,rng.uniform(-.04,.04))
                                p=max(.01,min(.99,probabilities.get(c,ability)+family_shock[family]))
                                observations[c].append(100 if rng.random()<p else 0)
                            score=sum(sum(x) for x in observations.values())/len(items)
                            accepted=score>=70
                            if method.startswith('balanced_'):accepted=accepted and all(c in observations and statistics.mean(observations[c])>=50 for c in core)
                            decisions.append(accepted)
                            if repeat==0:scores.append(score)
                        passes.append((decisions[0],decisions[1]))
                    rows.append({'spec':spec,'grade':grade,'profile':name,'method':method,'samples':args.samples,'target_response_average':round(target,3),'score_rmse':round(math.sqrt(statistics.mean((s-target)**2 for s in scores)),3),'paired_category_disagreement':round(statistics.mean(a!=b for a,b in passes),4),'pass_rate':round(statistics.mean(a for a,b in passes),4)})
    summary=[]
    for method in sorted({r['method'] for r in rows}):
        selected=[r for r in rows if r['method']==method]
        summary.append({'method':method,'contexts':len(selected)//len(profiles),'score_rmse':round(statistics.mean(r['score_rmse'] for r in selected),3),'strong_disagreement':round(statistics.mean(r['paired_category_disagreement'] for r in selected if r['profile']=='strong'),4),'borderline_disagreement':round(statistics.mean(r['paired_category_disagreement'] for r in selected if r['profile']=='borderline'),4),'one_weak_domain_pass_rate':round(statistics.mean(r['pass_rate'] for r in selected if r['profile']=='one_weak_domain'),4)})
    by={r['method']:r for r in summary};reduction=1-by['balanced_9']['score_rmse']/by['balanced_3']['score_rmse']
    report={'baseline_commit':'b05ea0e4f9352d05741c333b355bee34d3cd9a9b','seed':20261008,'hypothesis':'Balanced repeated observations improve controlled score precision and expose a weak core; not a professional-grade validity claim.','model':{'response':'Bernoulli by competency with bounded correlated family shock','difficulty':'assumed .02 per authored grade level; not fitted','work_checks':'held passed to isolate the form effect','same_blueprint_length_comparison':True,'actual_v2_construct_caveat':'Old form can sample different competencies; use balanced3 vs9 for same-construct RMSE.'},'summary':summary,'content':content,'per_profile':rows,'acceptance':{'quota_coverage':all(c['quotas_ok'] for c in content if c['available']),'rmse_reduction_9_vs_3':round(reduction,4),'rmse_condition_passed':reduction>=.10,'decision_stability_is_not_uniform':True},'limits':['All responses simulated; no real test takers or empirical grade accuracy.','Near-threshold disagreement may remain or increase; longer forms do not certify a person.','IRT not fitted; dimensionality, item calibration and linkage require actual response evidence.','12-item forms lack sufficient distinct items for some role/grade combinations; compare lengths3/6/9 on all24contexts.','Total work time and fairness have not been measured on humans.']}
    target=ROOT/args.output;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'summary':summary,'acceptance':report['acceptance']}),flush=True)
    if not report['acceptance']['quota_coverage'] or not report['acceptance']['rmse_condition_passed']:raise SystemExit('Registered acceptance failed')
if __name__=='__main__':main()
