import sys,tarfile
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.package_submission import allowed,package
from backend.pilots import weighted_kappa,report

def test_submission_rejects_private_state_and_path_escape():
    for name in ['../private.txt','backend/../../secret.txt','.local/notes.txt','backend/runtime/token.json','docs/FSP_IMPLEMENTATION_PLAN.md','data/app.db','frontend/key.pem','docs/ChatExport.json','AGENTS.md','artifacts/other-project.apk']:
        assert not allowed(name)
    assert allowed('backend/product.py')
    assert allowed('docs/JURY_ACCESS.md')

def test_release_excludes_existing_databases_secrets_and_git(tmp_path):
    target=tmp_path/'package.tar.gz';package(target)
    with tarfile.open(target) as archive:
        names=archive.getnames()
        assert 'backend/product.py' in names and 'deploy/compose.https.yaml' in names
        assert not any('.git/' in x or '/runtime/' in x or x.endswith(('.db','.env','id_ed25519','.tfstate')) or x.startswith('data/outbox') for x in names)

def test_pilot_metrics_no_humans_invented():
    assert weighted_kappa([(0,0),(1,1),(2,2),(3,3)])==1
    assert weighted_kappa([(0,3),(3,0)])==-1
    assert weighted_kappa([(1,1)]) is None
    r=report([])
    assert r['calibration']=='pilot_unvalidated'
    assert all(g['participants']==0 and g['ux_walkthroughs']==0 and g['weighted_kappa'] is None for g in r['groups'])

def test_public_demo_blocks_real_registration(clients,monkeypatch):
    employer,candidate,admin,sign=clients
    monkeypatch.setenv('FSP_ALLOW_REGISTRATION','0')
    assert candidate.get('/api/health').json()['registration_enabled'] is False
    body={'email':'new@example.test','password':'ComplexPassword!12','name':'New Person','role':'candidate','processing_consent':True}
    assert candidate.post('/api/auth/register',json=body).status_code==403
    from backend import db
    with db.connection() as conn:
        assert not conn.execute('SELECT id FROM users WHERE email=?',(body['email'],)).fetchone()

def test_pilot_capture_permissions_and_provenance(clients):
    employer,candidate,admin,sign=clients
    body={'kind':'ux','source':'synthetic_fixture','participant_ref':'synthetic-test','assessor_ref':'automation-test','protocol_ref':'test-v1','route':'candidate_application','seconds':30,'completed':True,'feedback':'Automated regression, not a human pilot'}
    assert employer.post('/api/admin/pilot',json=body).status_code==403
    assert admin.post('/api/admin/pilot',json=body).status_code==201
    r=admin.get('/api/admin/pilot').json()['report']
    assert next(g for g in r['groups'] if g['source']=='human_observation')['ux_walkthroughs']==0
    assert next(g for g in r['groups'] if g['source']=='synthetic_fixture')['ux_walkthroughs']==1
