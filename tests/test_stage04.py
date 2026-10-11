"""Extended product journeys and regressions against real SQLite/PostgreSQL."""
import copy,json,time
import pytest
from backend import db
from backend.item_generator import generate,grade_answer,public_item
from backend.live_vacancies import normalize,import_items
from test_stage03 import create_need,invite

VACANCY={'title':'Python API developer','description':'Develop reliable services and handle integration failures','role':'backend','required_skills':['python','sql'],'salary_min':100000,'salary_max':180000}

def published(employer):
    r=employer.post('/api/vacancies',json=VACANCY);assert r.status_code==201,r.text
    v=r.json();assert v['state']=='draft'
    r=employer.post('/api/vacancies/'+v['id']+'/publish',json={'version':v['version']});assert r.status_code==200,r.text
    return r.json()

def test_vacancy_draft_publish_edit_scope_archive(clients):
    employer,candidate,other,sign=clients
    draft=employer.post('/api/vacancies',json=VACANCY).json()
    assert draft['id'] not in {v['id'] for v in candidate.get('/api/vacancies').json()['items']}
    sign(other,'u_employer2')
    assert other.put('/api/vacancies/'+draft['id'],json=VACANCY|{'version':1}).status_code==404
    v=employer.post('/api/vacancies/'+draft['id']+'/publish',json={'version':1}).json()
    assert v['id'] in {v['id'] for v in candidate.get('/api/vacancies').json()['items']}
    assert employer.put('/api/vacancies/'+v['id'],json=VACANCY|{'title':'Changed API role','version':1}).status_code==409
    r=employer.put('/api/vacancies/'+v['id'],json=VACANCY|{'title':'Changed API role','version':v['version']});assert r.status_code==200
    assert r.json()['state']=='draft' and r.json()['id'] not in {v['id'] for v in candidate.get('/api/vacancies').json()['items']}
    assert employer.post('/api/vacancies/'+v['id']+'/archive',json={}).status_code==200
    assert candidate.post('/api/applications',json={'vacancy_id':v['id'],'share_contacts':True}).status_code==409

def test_application_consent_idempotency_recipient_and_withdrawal(clients):
    employer,candidate,other,sign=clients;v=published(employer);sign(other,'u_employer2')
    assert employer.get('/api/candidates/c_anna').json()['contacts']=={}
    assert candidate.post('/api/applications',json={'vacancy_id':v['id']}).status_code==422
    r=candidate.post('/api/applications',json={'vacancy_id':v['id'],'message':'My email secret@example.test','share_contacts':True});assert r.status_code==201,r.text
    a=r.json()
    assert candidate.post('/api/applications',json={'vacancy_id':v['id'],'share_contacts':True}).json()['id']==a['id']
    assert employer.get('/api/applications').json()['items'][0]['candidate']['contacts']['email']=='c_anna@example.org'
    assert other.get('/api/applications').json()['items']==[]
    assert other.post('/api/applications/'+a['id']+'/status',json={'version':1,'status':'reviewing'}).status_code==404
    assert candidate.post('/api/applications/'+a['id']+'/status',json={'version':1,'status':'hired'}).status_code==422
    assert candidate.post('/api/applications/'+a['id']+'/status',json={'version':1,'status':'withdrawn'}).status_code==200
    view=employer.get('/api/applications').json()['items'][0]
    assert view['candidate']['contacts']=={} and 'secret@example.test' not in view['payload']['message']
    assert employer.post('/api/applications/'+a['id']+'/status',json={'version':2,'status':'reviewing'}).status_code==409

def test_application_processing_and_existing_invitation_grant(clients):
    employer,candidate,other,sign=clients;v=published(employer)
    invitation=invite(employer,create_need(employer))
    assert candidate.post('/api/invitations/'+invitation['id']+'/respond',json={'version':invitation['version'],'action':'accepted'}).status_code==200
    a=candidate.post('/api/applications',json={'vacancy_id':v['id'],'share_contacts':True}).json()
    a=employer.post('/api/applications/'+a['id']+'/status',json={'version':1,'status':'reviewing'}).json()
    assert employer.post('/api/applications/'+a['id']+'/status',json={'version':1,'status':'interview'}).status_code==409
    a=employer.post('/api/applications/'+a['id']+'/status',json={'version':2,'status':'interview'}).json()
    assert candidate.post('/api/applications/'+a['id']+'/status',json={'version':a['version'],'status':'withdrawn'}).status_code==200
    assert employer.get('/api/candidates/c_anna').json()['contacts'] # Independent accepted invitation remains active.

def test_vacancy_risk_moderation(clients):
    employer,candidate,admin,sign=clients
    v=employer.post('/api/vacancies',json=VACANCY|{'description':'Переведите взнос за трудоустройство для получения доступа к работе'}).json()
    v=employer.post('/api/vacancies/'+v['id']+'/publish',json={'version':1}).json()
    assert v['state']=='pending'
    assert v['id'] not in {v['id'] for v in candidate.get('/api/vacancies').json()['items']}
    assert candidate.post('/api/admin/vacancy-reviews/'+v['id'],json={'version':v['version'],'approve':True,'rationale':'Fake approval reason'}).status_code==403
    assert admin.post('/api/admin/vacancy-reviews/'+v['id'],json={'version':v['version'],'approve':False,'rationale':'Payment demanded from applicant'}).status_code==200

CASE={'title':'Reliable API case','specialization':'backend','task':'Design retries for a booking API and explain invariants during duplicate requests.','rubric':['Atomic record creation','Explicit measurable error checks'],'interval_days':7}
def approved_case(clients):
    employer,candidate,admin,sign=clients
    r=employer.post('/api/regular-cases',json=CASE);assert r.status_code==201,r.text
    identity=r.json()['id']
    assert admin.post('/api/admin/regular-cases/'+identity+'/review',json={'version':1,'approve':True,'rationale':'The task has clear independent criteria'}).status_code==200
    return identity

@pytest.mark.parametrize('passed',[False,True])
def test_regular_case_voluntary_moderated_no_grade_mutation(clients,passed):
    employer,candidate,admin,sign=clients
    with db.connection() as conn:before=[tuple(r) for r in conn.execute("SELECT * FROM grade_certifications WHERE candidate_id='c_anna'")]
    pending=employer.post('/api/regular-cases',json=CASE).json()['id']
    assert pending not in {c['id'] for c in candidate.get('/api/regular-cases').json()['items']}
    identity=approved_case(clients)
    assert candidate.put('/api/regular-cases/'+identity+'/subscription',json={'enabled':True}).status_code==200
    assert candidate.put('/api/regular-cases/'+identity+'/subscription',json={'enabled':False}).status_code==200
    assert candidate.post('/api/regular-cases/'+identity+'/attempts',json={'answer':'A complete solution'}).status_code==422
    r=candidate.post('/api/regular-cases/'+identity+'/attempts',json={'answer':'A complete solution, contact secret@example.test','share_answer':True});assert r.status_code==201,r.text
    a=r.json();assert 'secret@example.test' not in employer.get('/api/regular-case-attempts').text
    assert candidate.post('/api/regular-cases/'+identity+'/attempts',json={'answer':'Another complete solution','share_answer':True}).status_code==409
    sign(admin,'u_employer2')
    assert admin.post('/api/regular-case-attempts/'+a['id']+'/review',json={'version':1,'passed':passed,'rationale':'A detailed review against both criteria'}).status_code==404
    assert employer.post('/api/regular-case-attempts/'+a['id']+'/review',json={'version':1,'passed':passed,'rationale':'A detailed review against both criteria'}).status_code==200
    assert candidate.post('/api/regular-cases/'+identity+'/attempts',json={'answer':'Another complete solution','share_answer':True}).status_code==429
    with db.connection() as conn:
        after=[tuple(r) for r in conn.execute("SELECT * FROM grade_certifications WHERE candidate_id='c_anna'")]
        assert before==after
        profile=json.loads(conn.execute("SELECT payload FROM candidates WHERE id='c_anna'").fetchone()[0])
        assert bool(profile.get('freshness'))==passed

@pytest.mark.parametrize('spec',['devops','ml','security'])
@pytest.mark.parametrize('grade',['Intern','Junior','Middle','Senior'])
def test_new_professional_case_keys_and_private_scope(spec,grade):
    variants=[generate(spec,grade,allow_neural=False) for _ in range(2)]
    assert variants[0]['condition_signature']!=variants[1]['condition_signature']
    assert variants[0]['calibration']=='pilot_unvalidated'
    for task in variants[0]['items']:
        assert 'private' not in public_item(task)
        key=task['private'].get('expected')
        if task['kind']=='explanation':assert grade_answer(task,'Good explanation')['score'] is None
        else:
            answer=json.dumps(key) if task['kind']=='multi' else str(key)
            assert grade_answer(task,answer)['score']==100
            assert grade_answer(task,'invalid')['score']==0

def raw_vacancy():return {'id':'real-1','job-name':'Python API developer','vac_url':'https://trudvsem.ru/vacancy/card/company/real-1','creation-date':'2026-10-01','date_modify':'2026-10-03T12:01:24+0300','company':{'name':'Example Org','email':'private@example.test'},'contact_person':'Private Name','contact_list':[{'contact_value':'private@example.test'}],'duty':'Develop reliable API services with Python','salary_min':100000,'salary_max':150000}

def test_live_snapshot_normalization_idempotency_provenance_expiry(clients):
    item=normalize(raw_vacancy(),'2026-10-05T09:00:00+00:00')
    assert 'private@example.test' not in json.dumps(item) and 'contact_person' not in item
    assert item['currency']=='RUB' and not item['demo'] and item['employer_verification']=='external_source_only'
    meta={'observed_at':'2026-10-05T09:00:00+00:00'}
    assert import_items([item],meta)['inserted']==1
    assert import_items([item],meta)['unchanged']==1
    item['description']='Changed requirements after publication'
    assert import_items([item],meta)['updated']==1
    duplicate=copy.deepcopy(item);duplicate['id']='trud_other';assert import_items([duplicate],meta)['duplicates']==1
    with db.connection() as conn:
        payload=json.loads(conn.execute('SELECT payload FROM vacancies WHERE id=?',(item['id'],)).fetchone()[0]);payload['expires']=time.time()-1
        conn.execute('UPDATE vacancies SET payload=? WHERE id=?',(db.dumps(payload),item['id']))
    assert import_items([],meta)['expired']==1
    assert item['id'] not in {v['id'] for v in clients[1].get('/api/vacancies').json()['items']}
    assert import_items([item],meta)['updated']==1
    assert item['id'] in {v['id'] for v in clients[1].get('/api/vacancies').json()['items']}
    with db.connection() as conn:
        assert conn.execute("SELECT 1 FROM search_index WHERE entity_type='vacancy' AND entity_id=?",(item['id'],)).fetchone()

def test_external_vacancy_links_and_dates_rejected():
    for changed in [{'vac_url':'https://evil.test/x'},{'date_modify':'2030-01-01'},{'date_modify':'broken'}]:
        with pytest.raises(ValueError):normalize(raw_vacancy()|changed,'2026-10-05T09:00:00+00:00')
