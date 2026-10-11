"""Same controlled API lifecycle matrix on any supplied source checkout.

Uses only a disposable PostgreSQL schema and synthetic fixture accounts. It sends
no mail, runs no competitor code and changes no deployed data.
"""
import argparse
import copy
import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source-root',required=True);parser.add_argument('--output',required=True);args=parser.parse_args()
    sys.path.insert(0,str(Path(args.source_root).resolve()))
    os.environ.update(FSP_DEMO='1',FSP_TEST_LOGIN='1',FSP_JOB_AUTOSTART='0',FSP_ENABLE_ML='0',
                      FSP_ASSESSMENT_PATH='work_sample_v3_balanced',FSP_MATCH_POLICY='category-evidence-v2',
                      FSP_SMTP_HOST='',FSP_DELIVERY_API_URL='')
    from scripts.postgres_test_context import postgres_test_schema
    from fastapi.testclient import TestClient
    from backend.main import app
    from backend import db
    from backend.assessment_catalog import SPECIALIZATIONS,GRADES
    states=['usable','stale_valid','revoked','expired','inactive','malformed','not_looking',
            'onboarding_incomplete','publication_withdrawn','blocked_organization','account_disabled','explicit_expiry']
    rows=[]
    @contextmanager
    def scoped_schema():
        # Also isolates a frozen baseline whose older context helper sets only env.
        with postgres_test_schema():
            cached=db.DATABASE_URL;db.DATABASE_URL=os.environ['FSP_DATABASE_URL']
            try:
                with db.connection() as conn:
                    assert conn.execute('SELECT current_schema()').fetchone()[0].startswith('testbrowser_')
                yield
            finally:db.DATABASE_URL=cached
    with scoped_schema(),TestClient(app,raise_server_exceptions=False) as employer:
        response=employer.post('/api/auth/demo',json={'account':'u_employer'});assert response.status_code==200
        employer.headers['X-CSRF-Token']=response.json()['csrf']
        with db.connection() as conn:
            original=dict(conn.execute("SELECT * FROM candidates WHERE id='c_anna'").fetchone())
            certificate=dict(conn.execute("SELECT * FROM grade_certifications WHERE candidate_id='c_anna' AND active=1 LIMIT 1").fetchone())
            org=conn.execute("SELECT org_id FROM users WHERE id='u_employer'").fetchone()[0]
            conn.execute("UPDATE grade_certifications SET active=0 WHERE candidate_id!='c_anna'")
        for spec,definition in SPECIALIZATIONS.items():
            for grade in GRADES:
                need={'title':'Изолированная проверка API '+spec+' '+grade,'description':'Контрольные задачи продукта и разрешённые условия найма',
                      'team':'Синтетическая команда проверки контракта','specialization':spec,'grade':grade,
                      'skills':definition['skills'][:2],'salary_min':100000,'salary_max':250000,
                      'employer_contact':'fixture@example.test'}
                response=employer.post('/api/needs',json=need);assert response.status_code==201,response.text
                need_id=response.json()['id']
                for state in states:
                    with db.connection() as conn:
                        payload=json.loads(original['payload']);payload.update(role=spec,skills=definition['skills'],assessment_onboarding_done=True,looking=True)
                        proof=json.loads(certificate['payload']);proof.update(tested_skills=definition['skills'][:2])
                        if state=='not_looking':payload['looking']=False
                        if state=='onboarding_incomplete':payload['assessment_onboarding_done']=False
                        if state=='explicit_expiry':proof['expires_at']='2020-01-01T00:00:00+00:00'
                        conn.execute("UPDATE candidates SET payload=?,visible=1 WHERE id='c_anna'",(db.dumps(payload),))
                        conn.execute("UPDATE users SET active=? WHERE candidate_id='c_anna'",(int(state!='account_disabled'),))
                        conn.execute("DELETE FROM employer_blocks WHERE candidate_id='c_anna'")
                        if state=='blocked_organization':
                            conn.execute('INSERT INTO employer_blocks(candidate_id,org_id,created_at) VALUES(?,?,?)',('c_anna',org,db.now()))
                        conn.execute("UPDATE consent_events SET granted=? WHERE candidate_id='c_anna' AND kind='publication'",(int(state!='publication_withdrawn'),))
                        conn.execute("UPDATE grade_certifications SET active=? WHERE candidate_id='c_anna'",(int(state!='inactive'),))
                        conn.execute('UPDATE grade_certifications SET specialization=?,grade=?,status=?,payload=?,created_at=? WHERE id=?',
                            (spec,grade,state if state in {'revoked','expired'} else 'demo',
                             '[]' if state=='malformed' else db.dumps(proof),
                             '2020-01-01T00:00:00+00:00' if state=='stale_valid' else db.now(),certificate['id']))
                    response=employer.post('/api/search-sessions',json={'need_id':need_id,'limit':1})
                    data=response.json() if response.status_code<400 else {}
                    # A legacy malformed proof may throw 500; captured as a defect.
                    detail=employer.get('/api/candidates/c_anna')
                    expected=1 if state in {'usable','stale_valid'} else 0
                    returned=len(data.get('items',[]));total=data.get('page',{}).get('total_at_snapshot')
                    rows.append({'specialization':spec,'grade':grade,'state':state,'expected':expected,
                                 'search_status':response.status_code,'returned':returned,'snapshot_total':total,
                                 'profile_status':detail.status_code,'algorithm':data.get('algorithm'),
                                 'accepted':response.status_code==201 and returned==expected and total==expected
                                     and detail.status_code==(200 if expected else 404) and data.get('algorithm')=='category-evidence-v2'})
        # Restore no real records: the schema is dropped on context exit.
    report={'source_version':app.version,'scope':'Actual API on disposable synthetic PostgreSQL data; no people or market hiring quality tested.',
            'queries':len(rows),'states':states,'rows':rows,
            'summary':{'contract_passed':sum(r['accepted'] for r in rows),'forbidden_profiles_returned':sum(r['returned'] for r in rows if not r['expected']),
                       'wrong_page_totals':sum(r['snapshot_total']!=r['expected'] for r in rows),
                       'server_errors':sum(r['search_status']>=500 or r['profile_status']>=500 for r in rows),
                       'wrong_algorithm_labels':sum(r['algorithm']!='category-evidence-v2' for r in rows)}}
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report['summary']))

if __name__=='__main__':main()
