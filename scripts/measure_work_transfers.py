"""Technical reference/negative conformance; no human grade validation."""
import argparse,json,random,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.work_cases_v2 import FAMILIES,generate_case
from backend.work_projects_v3 import FAMILIES as PROJECTS,generate_case as generate_project
from backend.work_transfers import generate as changed
from backend.work_samples import evaluate,broken_starter

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='.local/work-transfer-experiment.json');args=parser.parse_args()
    counts={'pairs':0,'conditions':0,'valid_solutions':0,'negative_solutions':0,'old_reference_rejected_on_transfer':0};families=set()
    for spec in FAMILIES:
        for grade in ['Intern','Junior','Middle','Senior']:
            for family in FAMILIES[spec]+([PROJECTS[spec]] if spec in PROJECTS else []):
                for seed in range(40):
                    original=generate_project(spec,grade,random.Random(seed)) if spec in PROJECTS and family==PROJECTS[spec] else generate_case(spec,grade,random.Random(seed),family)
                    original=broken_starter(original);transfer=broken_starter(changed(original));families.add(original['family']);counts['pairs']+=1
                    assert not evaluate(transfer,original['private']['reference_answer'])['all_checks_passed'];counts['old_reference_rejected_on_transfer']+=1
                    for item in [original,transfer]:
                        counts['conditions']+=1
                        for name in ['reference_answer']+(['alternative_answer'] if 'alternative_answer' in item['private'] else []):
                            assert evaluate(item,item['private'][name])['all_checks_passed'],(spec,grade,family,seed,name)
                            counts['valid_solutions']+=1
                        for answer in [item['starter'],'{}','[]','{"a":1,"a":2}','exec("unsafe")','x'*5001]:
                            assert not evaluate(item,answer)['all_checks_passed'],(spec,grade,family,seed,answer)
                            counts['negative_solutions']+=1
    report={'baseline_commit':'b05ea0e4f9352d05741c333b355bee34d3cd9a9b','counts':counts,'families':sorted(families),'acceptance_passed':True,'independent_reference_scope':'Three new projects have separate declarative reference and replay implementations; old12models keep their original frozen keys.','limits':['Technical conformance only, not discrimination of professional grades on people.','Bounded JSON artifacts; no execution of arbitrary candidate code.','Rejecting old references applies to registered changed scenarios; valid reused solutions are not rejected simply for reuse.']}
    target=ROOT/args.output;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(counts),flush=True)
if __name__=='__main__':main()
