"""Exact decision-policy sensitivity. Response probabilities are assumptions.

This compares length/core-floor policies, not the accuracy of human qualification.
It deliberately keeps difficult/boundary cases and the rejected longer policy.
"""
import argparse
import itertools
import json
import math
from pathlib import Path

PROFILES = {
    'weak': [.30,.30,.30],
    'borderline': [.70,.70,.70],
    'strong': [.95,.95,.95],
    'one_weak_core': [.95,.95,.15],
    'leaked_screening': [1.,1.,1.],
}
WORK = {'weak':.15, 'borderline':.65, 'strong':.97, 'one_weak_core':.97, 'leaked_screening':.15}

def decision(counts, quota, floor=.5):
    return sum(counts) >= math.ceil(3*quota*.7) and all(c >= math.ceil(quota*floor) for c in counts)

def exact_pass(probabilities, quota, floor=.5):
    distributions = [[math.comb(quota,c)*p**c*(1-p)**(quota-c) for c in range(quota+1)] for p in probabilities]
    return sum(math.prod(distributions[i][c] for i,c in enumerate(counts))
               for counts in itertools.product(range(quota+1),repeat=3) if decision(counts,quota,floor))

def measure():
    rows=[]
    # Equal conditions first; ±.05 per repeat models variant-difficulty uncertainty.
    for profile, probabilities in PROFILES.items():
        for length, quota in [(9,3),(12,4)]:
            p=exact_pass(probabilities,quota)*WORK[profile]**2
            harder=exact_pass([max(0,x-.05) for x in probabilities],quota)*WORK[profile]**2
            easier=exact_pass([min(1,x+.05) for x in probabilities],quota)*WORK[profile]**2
            rows.append({'profile':profile,'length':length,'pass_rate':round(p,6),
                         'repeat_disagreement_equal_probability':round(2*p*(1-p),6),
                         'pass_rate_variant_harder':round(harder,6),'pass_rate_variant_easier':round(easier,6),
                         'difficulty_spread':round(easier-harder,6)})
    by={(r['profile'],r['length']):r for r in rows}
    accepted=(by['one_weak_core',12]['pass_rate']<=by['one_weak_core',9]['pass_rate']
              and by['weak',12]['pass_rate']<=by['weak',9]['pass_rate']
              and by['strong',12]['pass_rate']>=by['strong',9]['pass_rate'])
    return {'baseline_commit':'a72dc9a53ea8c4497a4f52a82cbde6eecf54800f',
            'hypothesis':'12 instead of 9 improves stability without increasing weak-core admission or strong refusal.',
            'model':{'correct_response_probabilities_by_core':PROFILES,'independent_work_success_probability_per_gate':WORK,
                     'core_floor':.5,'overall_floor':.7,'difficulty_sensitivity':.05},
            'rows':rows,'longer_policy_accepted':accepted,'production_length':12 if accepted else 9,
            'limits':['Exact calculation under independent Bernoulli response assumptions, not human validation.',
                      'Equal probabilities do not prove that authored variants are equally difficult.',
                      'Question overlap, memorization, correlated errors and real completion time need observations.',
                      'No IRT calibration, standard error of human ability or comparison to competitor scores is claimed.']}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    result=measure();Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'accepted':result['longer_policy_accepted'],'production_length':result['production_length'],'rows':result['rows']}))

if __name__=='__main__':main()
