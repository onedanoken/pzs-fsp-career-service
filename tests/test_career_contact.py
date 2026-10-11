"""Synthetic contract checks: privacy, stale hiring and mutually recorded contact."""
import json
import random
import secrets
import time
from datetime import datetime, timezone
import pytest
from backend import db
from backend.contact_policy import hiring_activity, contact_state
from backend.competency_feedback import feedback
from backend.case_bank import diagnostic_case
from backend.item_generator import grade_answer, generate, public_item

NEED = {'title':'Career API team','description':'Build reliable infrastructure and API integrations','team':'Product infrastructure team','specialization':'backend','grade':'Middle','skills':['python','sql'],'salary_min':120000,'salary_max':180000,'employer_contact':'hr@example.org'}

def invitation(employer):
    need = employer.post('/api/needs', json=NEED).json()
    body = {'need_id':need['id'], 'candidate_id':'c_anna', 'message':'Discuss our product and team objectives', 'next_step':'Meet the team and discuss agreed salary conditions', 'response_days':2, 'idempotency_key':secrets.token_hex(12)}
    response = employer.post('/api/invitations', json=body)
    assert response.status_code == 201, response.text
    return need, body, response.json()

def accept(candidate, item):
    response = candidate.post('/api/invitations/'+item['id']+'/respond', json={'action':'accepted','version':item['version']})
    assert response.status_code == 200, response.text
    return response.json()

def test_own_overview_and_no_other_persons_answers(clients):
    employer,candidate,admin,sign = clients
    result = candidate.get('/api/career/overview')
    assert result.status_code == 200
    assert result.json()['role'] == 'candidate'
    assert all(cert['demo'] for cert in result.json()['certifications'])
    assert 'answers' not in result.text and 'private' not in result.text
    assert admin.get('/api/career/overview').status_code == 403
    assert employer.get('/api/career/overview').json()['counts']['accepted_invitations'] == 0

def test_ready_candidate_next_step_changes_when_invitation_arrives(clients):
    employer,candidate,admin,sign=clients
    assert candidate.get('/api/career/overview').json()['next_action']['route']=='assessment'
    _,_,item=invitation(employer)
    overview=candidate.get('/api/career/overview').json()
    assert overview['pending_invitations']==1 and overview['next_action']['route']=='offers'
    accepted=accept(candidate,item)
    assert candidate.get('/api/career/overview').json()['contacts_awaiting_action']==1
    assert candidate.post('/api/invitations/'+accepted['id']+'/contact-progress',json={'version':accepted['version'],'action':'contact_confirmed'}).status_code==200
    assert candidate.get('/api/career/overview').json()['next_action']['route']=='assessment'

def test_sql_example_table_is_public_but_answers_and_hidden_cases_are_private():
    generated=generate('backend','Junior',allow_neural=False)
    task=next(item for item in generated['items'] if item['kind']=='sql')
    public=public_item(task)
    assert public['example_table']['rows']==task['private']['rows']
    assert public['example_table']['columns']==['team','amount','status']
    assert 'private' not in public and 'expected' not in public and 'hidden_tests' not in public
    assert task['private']['hidden_tests'] and json.dumps(task['private']['rows'],ensure_ascii=False) not in public['prompt']

def test_reopening_search_reads_current_privacy_without_mutating_history(clients):
    employer,candidate,other,sign=clients
    need=employer.post('/api/needs',json=NEED).json()
    created=employer.post('/api/search-sessions',json={'need_id':need['id']}).json()
    identity=created['id']
    restored=employer.get('/api/search-sessions/'+identity).json()
    assert restored['version']==created['version'] and len(restored['payload']['history'])==1
    anna=next(item for item in restored['items'] if item['candidate']['id']=='c_anna')
    assert anna['candidate']['contacts']=={} and not anna['candidate']['contacts_available']
    assert candidate.post('/api/consents',json={'kind':'publication','granted':False}).status_code==200
    updated=employer.get('/api/search-sessions/'+identity).json()
    assert not any(item['candidate']['id']=='c_anna' for item in updated['items'])
    assert updated['version']==created['version'] and len(updated['payload']['history'])==1
    sign(other,'u_employer2')
    assert other.get('/api/search-sessions/'+identity).status_code==404
    assert candidate.get('/api/search-sessions/'+identity).status_code==403

def test_stale_hiring_blocks_new_invitation_and_acceptance_until_reconfirmed(clients):
    employer,candidate,admin,sign = clients
    need,body,item = invitation(employer)
    with db.connection() as conn:
        row = conn.execute('SELECT payload FROM employer_needs WHERE id=?',(need['id'],)).fetchone()
        payload=json.loads(row[0]);payload['last_verified_at']=datetime.fromtimestamp(time.time()-15*86400,timezone.utc).isoformat()
        conn.execute('UPDATE employer_needs SET payload=? WHERE id=?',(db.dumps(payload),need['id']))
    body['idempotency_key']=secrets.token_hex(12)
    assert employer.post('/api/invitations',json=body).status_code == 409
    assert candidate.post('/api/invitations/'+item['id']+'/respond',json={'action':'accepted','version':item['version']}).status_code == 409
    assert employer.get('/api/career/overview').json()['counts']['needs_reconfirmation'] == 1
    path='/api/needs/'+need['id']+'/confirm-hiring'
    confirmation={'version':need['version'],'active_hiring':True,'salary_approved':True}
    assert candidate.post(path,json=confirmation).status_code == 403
    assert employer.post(path,json=dict(confirmation,salary_approved=False)).status_code == 422
    refreshed=employer.post(path,json=confirmation)
    assert refreshed.status_code == 200
    assert refreshed.json()['hiring_activity']['salary_approved_by_employer']
    assert employer.post(path,json=confirmation).status_code == 409
    assert employer.post('/api/invitations',json=body).status_code == 201
    assert accept(candidate,item)['contact_progress']['status'] == 'waiting'

def test_contact_commitment_idempotency_and_no_unaccepted_progress(clients):
    employer,candidate,admin,sign=clients
    need,body,item=invitation(employer)
    duplicate=employer.post('/api/invitations',json=body)
    assert duplicate.status_code == 201 and duplicate.json()['id']==item['id']
    assert employer.post('/api/invitations',json=dict(body,response_days=4)).status_code == 409
    update={'action':'contacted','version':item['version']}
    assert employer.post('/api/invitations/'+item['id']+'/contact-progress',json=update).status_code == 409
    assert candidate.post('/api/invitations/'+item['id']+'/contact-progress',json=update).status_code == 403
    assert item['contact_progress']['deadline'] is None

def test_contact_is_a_statement_until_candidate_confirmation_and_revocation_blocks_updates(clients):
    employer,candidate,admin,sign=clients
    need,body,item=invitation(employer);item=accept(candidate,item)
    path='/api/invitations/'+item['id']+'/contact-progress'
    sign(admin,'u_employer2')
    assert admin.post(path,json={'version':item['version'],'action':'contacted'}).status_code == 404
    result=employer.post(path,json={'version':item['version'],'action':'contacted'})
    assert result.status_code == 200 and result.json()['contact_progress']['status']=='employer_reported'
    item=result.json()
    result=candidate.post(path,json={'version':item['version'],'action':'contact_missing','message':'No contact has taken place'})
    assert result.json()['contact_progress']['status']=='disputed'
    assert employer.get('/api/career/overview').json()['counts']['contact_disputes']==1
    item=result.json()
    result=candidate.post(path,json={'version':item['version'],'action':'contact_confirmed'})
    assert result.json()['contact_progress']['status']=='confirmed'
    assert employer.get('/api/career/overview').json()['counts']['candidate_confirmed_contacts']==1
    item=result.json()
    assert candidate.post('/api/contacts/revoke',json={}).status_code==200
    assert next(r for r in candidate.get('/api/invitations').json()['items'] if r['id']==item['id'])['contact_progress']['can_record'] is False
    assert employer.post(path,json={'version':item['version'],'action':'interview_scheduled'}).status_code==403

def test_freshness_boundaries_and_legacy_invitation_does_not_invent_agreement():
    observed='2026-10-05T00:00:00+00:00';clock=datetime.fromisoformat(observed).timestamp()
    need={'state':'active','created_at':observed,'payload':'{}','expires':clock+30*86400}
    assert hiring_activity(need,clock+14*86400-1)['status']=='current'
    assert hiring_activity(need,clock+14*86400)['status']=='reconfirmation_required'
    assert hiring_activity(need,clock+30*86400)['status']=='expired'
    assert contact_state({'payload':'{}'},clock)['status']=='not_agreed'
    item={'status':'accepted','payload':json.dumps({'contact_plan':{'next_step':'Discuss the team','response_days':2}}),'history':json.dumps([{'status':'accepted','at':observed}])}
    assert contact_state(item,clock+2*86400+1)['status']=='overdue'

def test_feedback_aggregates_observations_and_never_exposes_private_answers():
    result={'items':[{'competency':'state','score':100,'item_id':'one'},{'competency':'state','score':0,'item_id':'two'},{'competency':'tradeoffs','score':None,'rubric':['secret rubric']}]}
    rows=feedback(result);assert rows[0]['score']==50 and rows[0]['observations']==2
    assert rows[1]['pending_expert'] and rows[1]['score'] is None
    assert 'secret' not in json.dumps(rows) and 'item_id' not in json.dumps(rows)
    result['items'][2]['item_id']='open'
    result['expert_review']={'item_id':'open','score':85}
    reviewed=feedback(result)
    assert reviewed[1]['score'] is None and reviewed[1]['expert_score']==85 and not reviewed[1]['pending_expert']
    assert reviewed[0]['score']==50

@pytest.mark.parametrize('spec',['backend','frontend','qa'])
def test_different_diagnostic_invariants_have_independent_numeric_oracles(spec):
    families=set()
    for seed in range(30):
        family,competency,prompt,expected=diagnostic_case(spec,'Middle',random.Random(seed))
        families.add(family)
        item={'kind':'debug','private':{'expected':expected}}
        assert grade_answer(item,str(expected))['score']==100
        assert grade_answer(item,str(expected+1))['score']==0
    assert len(families)==3
    data=generate(spec,'Middle',allow_neural=False)
    from backend.assessment_catalog import BLUEPRINT_VERSION
    assert data['blueprint_version']==BLUEPRINT_VERSION
    assert any(i.get('family','').startswith(spec+':') for i in data['items'])

def test_new_next_step_cannot_bypass_existing_scam_moderation(clients):
    employer,candidate,admin,sign=clients
    need,body,item=invitation(employer)
    body.update(idempotency_key=secrets.token_hex(12),next_step='Сначала внесите предоплату за трудоустройство')
    assert employer.post('/api/invitations',json=body).status_code==422
    body.update(next_step=' '*20,idempotency_key=secrets.token_hex(12))
    assert employer.post('/api/invitations',json=body).status_code==422
