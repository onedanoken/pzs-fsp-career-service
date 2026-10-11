"""Regression boundaries for username membership, blocks and service webhooks."""
import pytest
from backend import db
from test_production_api_journeys import normal,ok,invited,PASSWORD
from test_career_contact import NEED


def test_missing_postgres_cannot_create_local_database(tmp_path,monkeypatch):
    path=tmp_path/'should-not-exist.db'
    monkeypatch.setattr(db,'DATABASE_URL','')
    monkeypatch.setenv('FSP_DB_PATH',str(path))
    with pytest.raises(RuntimeError,match='PostgreSQL required'):db.initialize()
    assert not path.exists()


def test_membership_is_bound_to_account_not_token_possession(normal):
    employer,candidate,_,outsider,people=normal
    invitation=ok(employer.post('/api/company/members/invite',json={'username':'API.OUTSIDER','role':'viewer'}),201)
    assert candidate.post('/api/company/members/accept',json={'token':invitation['token']}).status_code==403
    assert employer.post('/api/company/members/accept',json={'token':invitation['token']}).status_code==400
    ok(outsider.post('/api/company/members/accept',json={'token':invitation['token']}))
    assert outsider.post('/api/company/members/accept',json={'token':invitation['token']}).status_code==400
    roster=ok(employer.get('/api/company/members'))
    assert any(r['username']=='api.outsider' and r['role']=='viewer' for r in roster['items'])
    assert all('email' not in r for r in roster['items'])
    assert outsider.post('/api/needs',json=NEED).status_code==403
    assert employer.post('/api/company/members/invite',json={'username':'api.outsider','role':'recruiter'}).status_code==409


def test_blocked_target_pdf_and_unblocking_do_not_reopen_contacts(normal):
    employer,candidate,_,outsider,people=normal
    _,_,item=invited(normal)
    path='/api/candidates/'+people['api.candidate']['candidate_id']
    org=people['api.employer']['org_id']
    ok(candidate.post('/api/invitations/'+item['id']+'/respond',json={'action':'accepted','version':item['version']}))
    assert ok(employer.get(path))['contacts']
    ok(candidate.post('/api/employer-blocks',json={'org_id':org,'blocked':True}))
    assert employer.get(path).status_code==404 and employer.get(path+'/pdf').status_code==404
    assert outsider.get(path).status_code==200
    ok(candidate.post('/api/employer-blocks',json={'org_id':org,'blocked':False}))
    assert ok(employer.get(path))['contacts']=={}


def test_signed_callback_does_not_grant_browser_api_access(normal):
    from fastapi.testclient import TestClient
    from backend.main import app
    visitor=TestClient(app)
    assert visitor.post('/api/integration-events/fsp-achievements',json={}).status_code==401
    assert visitor.post('/api/integration-events/notifications',json={}).status_code==401
    assert visitor.post('/api/profile',json={}).status_code==401
    assert visitor.put('/api/profile',json={}).status_code==401
