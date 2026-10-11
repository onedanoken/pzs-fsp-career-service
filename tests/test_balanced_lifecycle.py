import json,re,time
import pytest
from backend import db
from backend.jobs import Worker

@pytest.fixture(autouse=True)
def balanced(monkeypatch):monkeypatch.setenv('FSP_ASSESSMENT_PATH','work_sample_v3_balanced')

def drain():
    worker=Worker()
    for _ in range(120):
        if not worker.run_once():return
    raise AssertionError('Unfinished jobs')

def issue(candidate,spec='qa',mode='certification',bad_transfer=False):
    response=candidate.post('/api/assessments',json={'specialization':spec,'grade':'Middle','mode':mode});assert response.status_code==202,response.text
    identity=response.json()['id'];drain();state=candidate.get('/api/assessments/'+identity).json()
    assert state['assembly_plan']['count']==9 and len(state['items'])==12
    with db.connection() as conn:data=json.loads(conn.execute('SELECT payload FROM assessment_sessions WHERE id=?',(identity,)).fetchone()[0])
    for item in data['items']:
        if item['kind']=='explanation':continue
        if item['kind']=='work_sample':answer='{}' if bad_transfer and item['stage']=='transfer' else item['private']['reference_answer']
        elif item['kind']=='sql':
            threshold=re.search(r'amount ≥ (\d+)',item['prompt'])[1];answer=f"SELECT team,SUM(amount) FROM events WHERE status='ok' AND amount>={threshold} GROUP BY team"
        elif item['kind']=='multi':answer=json.dumps(item['private']['expected'])
        else:answer=str(item['private']['expected'])
        response=candidate.put('/api/assessments/'+identity+'/answer',json={'version':state['version'],'item_id':item['id'],'answer':answer});assert response.status_code==200,response.text;state=response.json()
    assert candidate.post('/api/assessments/'+identity+'/finish',json={'version':state['version']}).status_code==202
    drain();return candidate.get('/api/assessments/'+identity).json()

@pytest.mark.parametrize('spec',['backend','frontend','qa','devops','ml','security'])
def test_balanced_certificate_passport_and_optional_review_keep_the_result(clients,spec):
    _,candidate,expert,_=clients;state=issue(candidate,spec)
    assert state['result']['passed'] and state['result']['assessment_path']['transfer_passed']
    snapshot=state['skill_passport']['snapshot'];assert snapshot['schema']=='fsp-skill-passport-v2' and snapshot['screening']['item_count']==9 and snapshot['transfer']['all_checks_passed']
    assert state['skill_passport']['current']['category_confirmed_by_this_revision']
    queued=next(x for x in expert.get('/api/admin/assessment-reviews').json()['items'] if x['session_id']==state['id'])
    criteria=[{'id':r['id'],'level':0,'note':'Дополнительный разбор не меняет автоматическую категорию.'} for r in queued['rubric_criteria']]
    response=expert.post('/api/admin/assessment-reviews/'+state['id'],json={'item_id':queued['item_id'],'version':queued['version'],'criteria':criteria,'rationale':'Сохраняем дополнительный ручной разбор отдельно от объективного результата.'});assert response.status_code==200,response.text
    current=candidate.get('/api/assessments/'+state['id']).json();assert current['result']['passed'] and current['skill_passport']['revision']==2
    assert current['skill_passport']['snapshot']['transfer']['all_checks_passed'] and current['skill_passport']['current']['category_confirmed_by_this_revision']

@pytest.mark.parametrize('mode,bad_transfer',[('practice',False),('certification',True)])
def test_practice_or_invalid_transfer_preserves_old_category(clients,mode,bad_transfer):
    _,candidate,_,_=clients
    with db.connection() as conn:before=[tuple(x) for x in conn.execute("SELECT id,active FROM grade_certifications WHERE candidate_id='c_anna'")]
    state=issue(candidate,mode=mode,bad_transfer=bad_transfer)
    assert state['result']['passed'] is (not bad_transfer)
    assert not state['skill_passport']['snapshot']['category_issued']
    with db.connection() as conn:assert before==[tuple(x) for x in conn.execute("SELECT id,active FROM grade_certifications WHERE candidate_id='c_anna'")]

def test_expired_attempt_is_read_only_without_erasing_answers_or_blocking_practice(clients):
    _,candidate,_,_=clients
    start=candidate.post('/api/assessments',json={'specialization':'qa','grade':'Junior','mode':'practice'}).json();drain();identity=start['id']
    state=candidate.get('/api/assessments/'+identity).json()
    response=candidate.put('/api/assessments/'+identity+'/answer',json={'version':state['version'],'item_id':state['items'][0]['id'],'answer':'draft'});assert response.status_code==200
    state=response.json()
    with db.connection() as conn:
        conn.execute('UPDATE assessment_sessions SET expires=? WHERE id=?',(time.time()-10,identity))
        old=tuple(conn.execute('SELECT payload,version,status FROM assessment_sessions WHERE id=?',(identity,)).fetchone())
    expired=candidate.get('/api/assessments/'+identity).json();assert expired['status']=='active' and expired['can_answer'] is False and expired['answers'][state['items'][0]['id']]=='draft'
    assert candidate.put('/api/assessments/'+identity+'/answer',json={'version':state['version'],'item_id':state['items'][0]['id'],'answer':'replacement'}).status_code==409
    assert candidate.post('/api/assessments',json={'specialization':'qa','grade':'Junior','mode':'practice'}).status_code==202
    with db.connection() as conn:assert old==tuple(conn.execute('SELECT payload,version,status FROM assessment_sessions WHERE id=?',(identity,)).fetchone())

def test_compatible_rollback_defaults_still_process_a_frozen_v3_attempt(clients,monkeypatch):
    _,candidate,_,_=clients
    # Switch configuration after generation but before all answer/grade operations.
    original_drain=drain
    def flip_after_generation():
        original_drain();monkeypatch.setenv('FSP_ASSESSMENT_PATH','work_sample_v2_auto');monkeypatch.setenv('FSP_MATCH_POLICY','category-evidence-v1')
    monkeypatch.setattr(__import__(__name__,fromlist=['drain']),'drain',flip_after_generation)
    state=issue(candidate,mode='practice')
    assert state['result']['passed'] and state['assessment_path']['version']=='work_sample_v3_balanced'
    created=candidate.post('/api/assessments',json={'specialization':'qa','grade':'Junior','mode':'practice'});assert created.status_code==202
    with db.connection() as conn:
        payload=json.loads(conn.execute('SELECT payload FROM assessment_sessions WHERE id=?',(created.json()['id'],)).fetchone()[0])
    assert payload['assessment_path_version']=='work_sample_v2_auto'
