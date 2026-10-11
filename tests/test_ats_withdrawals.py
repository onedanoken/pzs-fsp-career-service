"""Exercise durable privacy withdrawal and pagination on both database backends."""
import json
import pytest
from backend import db
from test_stage03 import create_need,invite


def ats_headers(employer):
    response=employer.post('/api/ats-connections',json={'name':'Privacy reference ATS','scopes':['profiles.read','events.read','stages.write']})
    assert response.status_code==201,response.text
    return {'Authorization':'Bearer '+response.json()['token']}


def accepted(employer,candidate):
    item=invite(employer,create_need(employer))
    response=candidate.post('/api/invitations/'+item['id']+'/respond',json={'action':'accepted','version':item['version']})
    assert response.status_code==200,response.text
    return item


def permit(candidate,item):
    response=candidate.post('/api/invitations/'+item['id']+'/ats-consent',json={'granted':True,'fields':['name','contacts']})
    assert response.status_code==200,response.text


@pytest.mark.parametrize('withdrawal',['ats','contacts','processing','candidate_block','company_block'])
def test_withdrawal_is_durable_private_and_idempotently_acknowledged(clients,withdrawal):
    employer,candidate,other,sign=clients
    headers=ats_headers(employer);item=accepted(employer,candidate);permit(candidate,item)
    assert other.get('/api/ats/v1/profiles',headers=headers).json()['items'][0]['profile']['contacts']
    if withdrawal=='ats':response=candidate.post('/api/invitations/'+item['id']+'/ats-consent',json={'granted':False})
    elif withdrawal=='contacts':response=candidate.post('/api/contacts/revoke',json={})
    elif withdrawal=='processing':response=candidate.post('/api/consents',json={'kind':'processing','granted':False})
    elif withdrawal=='candidate_block':response=candidate.post('/api/employer-blocks',json={'org_id':'org_sigma','blocked':True})
    else:
        with db.connection() as conn:version=conn.execute("SELECT version FROM company_profiles WHERE org_id='org_sigma'").fetchone()[0]
        response=other.post('/api/admin/companies/org_sigma/review',json={'state':'blocked','version':version,'organization_evidence':'Independent company verification record','authority_evidence':'Independent authorized representative record'})
    assert response.status_code==200,response.text
    with db.connection() as conn:
        payload=json.loads(conn.execute('SELECT payload FROM invitations WHERE id=?',(item['id'],)).fetchone()[0])
        assert payload['ats_consent']['granted'] is False
        events=conn.execute("SELECT * FROM integration_events WHERE direction='outbound'").fetchall()
        assert len(events)==1 and events[0]['status']=='awaiting_ack'
        assert 'contacts' not in events[0]['payload'] and 'example.org' not in events[0]['payload']
        if withdrawal=='company_block':conn.execute("UPDATE company_profiles SET verification='demo_verified' WHERE org_id='org_sigma'")
    assert other.get('/api/ats/v1/profiles',headers=headers).json()['items']==[]
    event=other.get('/api/ats/v1/events',headers=headers).json()['items'][0]
    assert event['withdrawal']['invitation_id']==item['id']
    assert other.post('/api/ats/v1/events/'+event['id']+'/ack',json={'deleted':False},headers=headers).status_code==422
    acknowledgement=other.post('/api/ats/v1/events/'+event['id']+'/ack',json={'deleted':True,'note':'Reference copy removed'},headers=headers)
    assert acknowledgement.status_code==200,acknowledgement.text
    assert other.post('/api/ats/v1/events/'+event['id']+'/ack',json={'deleted':True,'note':'Repeated delivery'},headers=headers).json()==acknowledgement.json()
    assert other.get('/api/ats/v1/events',headers=headers).json()['items'][0]['status']=='acknowledged'
    sign(other,'u_employer2');foreign=ats_headers(other)
    assert other.get('/api/ats/v1/events',headers=foreign).json()['items']==[]
    assert other.post('/api/ats/v1/events/'+event['id']+'/ack',json={'deleted':True},headers=foreign).status_code==404


def test_profile_cursor_does_not_skip_after_empty_visibility_page(clients):
    employer,candidate,other,sign=clients;headers=ats_headers(employer)
    hidden=accepted(employer,candidate);visible=accepted(employer,candidate);permit(candidate,visible)
    with db.connection() as conn:
        # Fix ordering rather than relying on random invitation IDs or the clock.
        conn.execute('UPDATE invitations SET created_at=? WHERE id=?',('2026-01-01T00:00:00+00:00',hidden['id']))
        conn.execute('UPDATE invitations SET created_at=? WHERE id=?',('2026-01-02T00:00:00+00:00',visible['id']))
    first=other.get('/api/ats/v1/profiles',params={'limit':1},headers=headers).json()
    assert first['items']==[] and first['has_more'] and first['next_cursor']
    second=other.get('/api/ats/v1/profiles',params={'limit':1,'cursor':first['next_cursor']},headers=headers).json()
    assert not second['has_more'] and second['items'][0]['invitation_id']==visible['id']
    for cursor in ['invalid!','e30','a'*401]:
        assert other.get('/api/ats/v1/profiles',params={'cursor':cursor},headers=headers).status_code==422
        assert other.get('/api/ats/v1/events',params={'cursor':cursor},headers=headers).status_code==422
    assert other.get('/api/ats/v1/profiles',params={'cursor':first['next_cursor'],'offset':1},headers=headers).status_code==422


def test_restricting_transfer_fields_invalidates_previous_external_copy(clients):
    employer,candidate,other,sign=clients;headers=ats_headers(employer)
    item=accepted(employer,candidate);permit(candidate,item)
    response=candidate.post('/api/invitations/'+item['id']+'/ats-consent',json={'granted':True,'fields':['name']})
    assert response.status_code==200,response.text
    page=other.get('/api/ats/v1/profiles',headers=headers).json()
    assert list(page['items'][0]['profile'])==['name']
    events=other.get('/api/ats/v1/events',headers=headers).json()['items']
    assert len(events)==1 and events[0]['withdrawal']['reason']=='ats_fields_restricted'
