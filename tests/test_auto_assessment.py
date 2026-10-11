import json,random,re
import pytest
from backend import db
from backend.jobs import Worker
from backend.work_cases_v2 import FAMILIES,generate_case,evaluate
from backend.assessment_catalog import GRADES

@pytest.fixture(autouse=True)
def auto_path(monkeypatch):monkeypatch.setenv('FSP_ASSESSMENT_PATH','work_sample_v2_auto')

def drain():
    worker=Worker()
    for _ in range(120):
        if not worker.run_once():return
    raise AssertionError('Unfinished jobs')

def complete(candidate,spec='devops',mode='certification',bad=False):
    start=candidate.post('/api/assessments',json={'specialization':spec,'grade':'Junior','mode':mode});assert start.status_code==202,start.text
    identity=start.json()['id'];drain();state=candidate.get('/api/assessments/'+identity).json()
    with db.connection() as conn:private=json.loads(conn.execute('SELECT payload FROM assessment_sessions WHERE id=?',(identity,)).fetchone()[0])
    for item in private['items']:
        if item['kind']=='explanation':continue
        if item['kind']=='work_sample':answer='{}' if bad else item['private']['reference_answer']
        elif item['kind']=='sql':
            threshold=re.search(r'amount ≥ (\d+)',item['prompt'])[1];answer=f"SELECT team,SUM(amount) FROM events WHERE status='ok' AND amount>={threshold} GROUP BY team"
        elif item['kind']=='multi':answer=json.dumps(item['private']['expected'])
        else:answer=str(item['private']['expected'])
        response=candidate.put('/api/assessments/'+identity+'/answer',json={'version':state['version'],'item_id':item['id'],'answer':answer});assert response.status_code==200,response.text;state=response.json()
    assert candidate.post('/api/assessments/'+identity+'/finish',json={'version':state['version']}).status_code==202
    drain();return candidate.get('/api/assessments/'+identity).json()

@pytest.mark.parametrize('spec',list(FAMILIES))
@pytest.mark.parametrize('grade',GRADES)
def test_each_grade_family_has_reference_and_rejects_bad_types(spec,grade):
    for family in FAMILIES[spec]:
        item=generate_case(spec,grade,random.Random(8),family)
        assert evaluate(item,item['private']['reference_answer'])['all_checks_passed']
        assert not evaluate(item,item['starter'])['all_checks_passed']
        for answer in ['{}','[]','{"x":1,"x":2}','exec(unsafe)', 'x'*5001]:assert not evaluate(item,answer)['all_checks_passed']

@pytest.mark.parametrize('spec',list(FAMILIES))
def test_category_and_passport_are_automatic_without_explanation(clients,spec):
    employer,candidate,expert,sign=clients;state=complete(candidate,spec)
    assert state['result']['passed'] and state['result']['assessment_path']['review_required'] is False
    assert state['result']['assessment_path']['state']=='automatic_pass'
    passport=state['skill_passport'];assert passport['current']['category_confirmed_by_this_revision']
    assert passport['snapshot']['review']['method']=='not_performed_optional'
    assert passport['snapshot']['category_issued'] and passport['revision']==1
    assert 'private' not in json.dumps(state['items'])
    with db.connection() as conn:assert conn.execute('SELECT COUNT(*) FROM grade_certifications WHERE session_id=?',(state['id'],)).fetchone()[0]==1
    assert candidate.post('/api/assessments/onboarding/complete',json={}).status_code==200

def test_invalid_case_cannot_pass_and_keeps_existing_category(clients):
    _,candidate,_,_=clients
    with db.connection() as conn:before=[tuple(x) for x in conn.execute('SELECT id,active FROM grade_certifications WHERE candidate_id=\'c_anna\'')]
    state=complete(candidate,bad=True);assert not state['result']['passed'] and not state['skill_passport']['snapshot']['category_issued']
    with db.connection() as conn:assert before==[tuple(x) for x in conn.execute('SELECT id,active FROM grade_certifications WHERE candidate_id=\'c_anna\'')]

def test_practice_does_not_issue_category(clients):
    _,candidate,_,_=clients;state=complete(candidate,mode='practice')
    assert state['result']['passed'] and not state['skill_passport']['snapshot']['category_issued']

def test_optional_review_never_overrides_automatic_result(clients):
    _,candidate,expert,_=clients;state=complete(candidate)
    queued=next(x for x in expert.get('/api/admin/assessment-reviews').json()['items'] if x['session_id']==state['id'])
    criteria=[{'id':x['id'],'level':0,'note':'Дополнительная низкая оценка объяснения.'} for x in queued['rubric_criteria']]
    response=expert.post('/api/admin/assessment-reviews/'+state['id'],json={'item_id':queued['item_id'],'version':queued['version'],'criteria':criteria,'rationale':'Дополнительный разбор не отменяет объективные основания категории.'});assert response.status_code==200,response.text
    refreshed=candidate.get('/api/assessments/'+state['id']).json();assert refreshed['result']['passed'] and refreshed['skill_passport']['revision']==2
    assert refreshed['skill_passport']['current']['category_confirmed_by_this_revision']
