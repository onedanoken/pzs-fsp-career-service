"""Complete objective journey simulation with explicit assumed response profiles."""
import argparse
import copy
import importlib.util
import json
import os
import random
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ.setdefault('FSP_DATABASE_URL','host=localhost dbname=unused')
from backend.assessment_forms import CORE, screening
from backend.item_generator import generate
from backend.work_samples import balanced_journey, evaluate
from backend.assessment_journey import result_for


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--baseline-forms',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--samples',type=int,default=200)
    args=parser.parse_args()
    module=importlib.util.spec_from_file_location('backend.frozen_baseline_forms',args.baseline_forms)
    baseline=importlib.util.module_from_spec(module);module.loader.exec_module(baseline)
    rng=random.Random(20261011);rows=[];content=[]
    profiles={'weak':(.30,.15),'borderline':(.70,.65),'strong':(.95,.97),'one_weak_core':(.95,.97),'leaked_screening':(1,.15)}
    for spec in CORE:
        for grade in ['Intern','Junior','Middle','Senior']:
            forms={}
            for policy,assembler in [('baseline',baseline.screening),('current',screening)]:
                versions=[]
                for seed in [20261011,20261012]:
                    data=balanced_journey(generate(spec,grade,allow_neural=False,qa_professional=spec=='qa',seed=seed),spec,grade)
                    questions,plan=assembler(spec,grade,seed)
                    data['items']=questions+[i for i in data['items'] if i['stage']!='screening']
                    data['assembly_plan']=plan
                    cases=[i for i in data['items'] if i['kind']=='work_sample']
                    for case in cases:
                        assert evaluate(case,case['private']['reference_answer'])['all_checks_passed']
                        assert not evaluate(case,'{}')['all_checks_passed']
                    content.append(dict(spec=spec,grade=grade,policy=policy,seed=seed,
                                        families=len({i.get('family') or i['kind'] for i in questions}),
                                        quotas={c:sum(i['competency']==c for i in questions) for c in CORE[spec]}))
                    versions.append(data)
                forms[policy]=versions
            for profile,(screen_probability,work_probability) in profiles.items():
                # Common random draws make baseline/current comparison paired.
                draws=[[(rng.random(),rng.random(),[rng.random() for _ in range(9)]) for _ in range(2)] for _ in range(args.samples)]
                for policy,versions in forms.items():
                    decisions=[]
                    for pair in draws:
                        repeat=[]
                        for data,(original_draw,transfer_draw,item_draws) in zip(versions,pair):
                            results=[];index=0
                            for item in data['items']:
                                if item['stage']=='review':continue
                                if item['stage']=='screening':
                                    probability=.15 if profile=='one_weak_core' and item['competency']==CORE[spec][-1] else screen_probability
                                    result={'score':100 if item_draws[index]<probability else 0,'method':'assumed_response'};index+=1
                                else:
                                    draw=original_draw if item['stage']=='work_sample' else transfer_draw
                                    result=evaluate(item,item['private']['reference_answer'] if draw<work_probability else '{}')
                                results.append(dict(result,item_id=item['id']))
                            repeat.append(result_for(data,results)['passed'])
                        decisions.append(repeat)
                    rate=sum(a for a,b in decisions)/args.samples
                    rows.append(dict(spec=spec,grade=grade,profile=profile,policy=policy,n=args.samples,
                                     pass_rate=round(rate,4),disagreement=round(sum(a!=b for a,b in decisions)/args.samples,4),
                                     reference_expected_pass=profile=='strong'))
    report={'seed':20261011,'baseline':'9b05443','scope':'Actual result_for and original/transfer oracles; assumed screening answers and artifact-success probabilities; no people tested',
            'model':profiles,'content':content,'rows':rows,'summary':{}}
    for policy in forms:
        selected=[r for r in rows if r['policy']==policy]
        report['summary'][policy]={p:{k:round(sum(r[k] for r in selected if r['profile']==p)/24,4) for k in ['pass_rate','disagreement']} for p in profiles}
        report['summary'][policy]['mean_distinct_families']=round(sum(r['families'] for r in content if r['policy']==policy)/48,3)
    report['limits']=['Synthetic profiles are assumptions; metrics do not estimate human qualification accuracy.',
                      'Screening content changed; identical simulated correctness draws compare decision gates, not empirical difficulty equivalence.',
                      'Strong false refusals and borderline instability must be reported, not hidden.',
                      'A leaked finite screening bank can be learned; practical gates reduce risk under the stated work-success model only.']
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report['summary']))


if __name__=='__main__':main()
