"""Paired assembly coverage/overlap, with actual frozen baseline modules.

Overlap measures repeated condition+key, not memorization or human test stability.
"""
import argparse
import importlib.util
import json
import os
import random
import sys
import types
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ.setdefault('FSP_DATABASE_URL','host=localhost dbname=unused')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--baseline-directory',required=True);parser.add_argument('--output',required=True);args=parser.parse_args()
    from backend.assessment_forms import CORE,screening,signature
    from backend.assessment_catalog import GRADES
    folder=Path(args.baseline_directory)
    ops=types.ModuleType('backend.top5baseline_operations');ops.__file__=str(folder/'screening_operations.py')
    sys.modules[ops.__name__]=ops
    ops_code=(folder/'screening_operations.py').read_text(encoding='utf-8-sig').replace("Path(__file__).resolve().parents[1]/'data/screening_core_operations.json'", "Path(__file__).with_name('screening_core_operations.json')")
    exec(compile(ops_code,ops.__file__,'exec'),ops.__dict__)
    old=types.ModuleType('backend.top5baseline_forms');old.__package__='backend'
    code=(folder/'assessment_forms.py').read_text(encoding='utf-8').replace('from .screening_operations import operations','from .top5baseline_operations import operations')
    exec(compile(code,str(folder/'assessment_forms.py'),'exec'),old.__dict__)
    # Confirm baseline uses old binary invariant bank, not changed production code.
    assert all(len(i['choices'])==2 for i in ops.operations('qa','Middle',random.Random(0)))
    rows=[]
    for spec in CORE:
        for grade in GRADES:
            sets={};metrics={}
            for policy,assembler in [('baseline',old.screening),('current',screening)]:
                forms=[assembler(spec,grade,seed)[0] for seed in range(20)]
                sets[policy]=[{signature(i) for i in form} for form in forms]
                choices=[i for form in forms for i in form if i['kind']=='choice']
                overlaps=[len(sets[policy][a]&sets[policy][b])/9 for a in range(20) for b in range(a)]
                metrics[policy]={'mean_exact_item_overlap':sum(overlaps)/len(overlaps),
                                 'mean_choice_options':sum(len(i['choices']) for i in choices)/len(choices),
                                 'grade_decisions_per_form':sum(i.get('version')=='grade-decisions-2026-v1' for f in forms for i in f)/20,
                                 'distinct_condition_keys':len(set.union(*sets[policy]))}
            rows.append({'specialization':spec,'grade':grade,**metrics})
    report={'baseline_commit':'a72dc9a53ea8c4497a4f52a82cbde6eecf54800f','seed_range':[0,19],
            'forms':960,'rows':rows,'summary':{p:{k:round(sum(r[p][k] for r in rows)/24,4) for k in rows[0][p]} for p in ['baseline','current']},
            'limits':['Deterministic item exposure/coverage checks only; no people tested.',
                      'Low exact overlap is not proof against learning a public finite family.',
                      'New grade scenarios are authored constraints, not calibrated IRT difficulty.']}
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report['summary']))

if __name__=='__main__':main()
