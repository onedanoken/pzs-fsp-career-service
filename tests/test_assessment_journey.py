"""New three-stage verification, immutable passports and authoritative access."""
import copy,json,random,re
import pytest
from backend import db
from backend.assessment_catalog import SPECIALIZATIONS,POLICY
from backend.item_generator import generate,grade_answer
from backend.work_samples import generate_case,add_journey,RUBRIC
from backend.jobs import Worker

@pytest.fixture(autouse=True)
def path_enabled(monkeypatch):monkeypatch.setenv('FSP_ASSESSMENT_PATH','work_sample_v1')

def drain():
    worker=Worker()
    for _ in range(100):
        if not worker.run_once():return
    raise AssertionError('Queue did not drain')

def attempt(candidate,spec='devops',mode='practice',valid=True):
    response=candidate.post('/api/assessments',json={'specialization':spec,'grade':'Junior','mode':mode});assert response.status_code==202,response.text
    identity=response.json()['id'];drain();current=candidate.get('/api/assessments/'+identity).json()
    assert len(current['items'])==5 and [x['stage'] for x in current['items']]==['screening']*3+['work_sample','review']
    with db.connection() as conn:data=json.loads(conn.execute('SELECT payload FROM assessment_sessions WHERE id=?',(identity,)).fetchone()[0])
    for item in data['items']:
        if item['kind']=='work_sample':answer=item['private']['reference_answer'] if valid else item['starter']
        elif item['kind']=='explanation':answer='Синтетическое объяснение: сохраняем инвариант, проверяем положительный и отрицательный сценарии. При изменении условия нужны дополнительные ограничения и измерения.'
        elif item['kind']=='sql':
            threshold=re.search(r'amount ≥ (\d+)',item['prompt'])[1];answer=f"SELECT team,SUM(amount) FROM events WHERE status='ok' AND amount>={threshold} GROUP BY team"
        elif item['kind']=='multi':answer=json.dumps(item['private']['expected'])
        else:answer=str(item['private']['expected'])
        response=candidate.put('/api/assessments/'+identity+'/answer',json={'item_id':item['id'],'answer':answer,'version':current['version']});assert response.status_code==200,response.text;current=response.json()
    response=candidate.post('/api/assessments/'+identity+'/finish',json={'version':current['version']});assert response.status_code==202,response.text
    drain();return candidate.get('/api/assessments/'+identity).json()

def rating(expert,session,levels=None):
    queued=next(x for x in expert.get('/api/admin/assessment-reviews').json()['items'] if x['session_id']==session['id'])
    body={'item_id':queued['item_id'],'version':queued['version'],'rationale':'Синтетический разбор для проверки маршрута. В нём не проводилась независимая экспертная калибровка.','criteria':[{'id':x['id'],'level':(levels or {}).get(x['id'],3),'note':'Наблюдение тестового сценария; оценка задана синтетически.'} for x in RUBRIC]}
    return expert.post('/api/admin/assessment-reviews/'+session['id'],json=body),body

@pytest.mark.parametrize('spec',list(SPECIALIZATIONS))
@pytest.mark.parametrize('grade',['Junior','Senior'])
def test_owned_work_models_accept_repairs_and_reject_starters(spec,grade):
    item=generate_case(spec,grade,random.Random(9))
    assert grade_answer(item,item['private']['reference_answer'])['all_checks_passed'] is True
    assert grade_answer(item,item['starter'])['score']<100
    for malformed in ['print("unsafe code")','{}','{"rule":true,"rule":false}','[1,2,3]','x'*5001]:assert grade_answer(item,malformed)['score']==0

def test_issued_signature_ignores_unissued_questions_and_unrelated_legacy_signature():
    original=generate('devops','Junior',allow_neural=False);altered=copy.deepcopy(original)
    altered['condition_signature']='different-unissued-pool'
    altered['items'][-1]['prompt']='Changed unused legacy open question'
    assert add_journey(original,'devops','Junior')['condition_signature']==add_journey(altered,'devops','Junior')['condition_signature']

@pytest.mark.parametrize('spec',list(SPECIALIZATIONS))
def test_all_six_paths_keep_scores_separate_and_do_not_issue_grade_before_review(clients,spec):
    employer,candidate,expert,sign=clients
    with db.connection() as conn:before=conn.execute("SELECT COUNT(*) FROM grade_certifications WHERE candidate_id='c_anna'").fetchone()[0]
    session=attempt(candidate,spec)
    path=session['result']['assessment_path'];assert path['screening_passed'] and path['work_passed'] and path['review_status']=='pending'
    assert session['result']['passed'] is False and session['result']['score']==100
    assert session['skill_passport']['snapshot']['review']['score'] is None
    with db.connection() as conn:assert conn.execute("SELECT COUNT(*) FROM grade_certifications WHERE candidate_id='c_anna'").fetchone()[0]==before
    response,body=rating(expert,session);assert response.status_code==200,response.text
    reviewed=candidate.get('/api/assessments/'+session['id']).json();assert reviewed['result']['passed'] and reviewed['skill_passport']['revision']==2
    assert not reviewed['skill_passport']['snapshot']['category_issued']
    with db.connection() as conn:assert conn.execute("SELECT COUNT(*) FROM grade_certifications WHERE candidate_id='c_anna'").fetchone()[0]==before

def test_category_requires_three_gates_and_review_is_not_a_free_score(clients):
    employer,candidate,expert,sign=clients
    session=attempt(candidate,'devops','certification');identity=session['skill_passport']['id']
    with db.connection() as conn:assert not conn.execute('SELECT 1 FROM grade_certifications WHERE session_id=?',(session['id'],)).fetchone()
    queue=next(x for x in expert.get('/api/admin/assessment-reviews').json()['items'] if x['session_id']==session['id'])
    assert expert.post('/api/admin/assessment-reviews/'+session['id'],json={'item_id':queue['item_id'],'version':queue['version'],'score':100,'rationale':'Free overall number must not replace a structured rubric.'}).status_code==422
    assert candidate.post('/api/assessments',json={'specialization':'devops','grade':'Senior','mode':'certification'}).status_code==409
    response,body=rating(expert,session);assert response.status_code==200,response.text
    assert expert.post('/api/admin/assessment-reviews/'+session['id'],json=body).status_code==409
    reviewed=candidate.get('/api/assessments/'+session['id']).json()
    assert reviewed['result']['certificate_id'] and reviewed['skill_passport']['snapshot']['category_issued']
    old=candidate.get('/api/skill-passports/'+identity).json()
    assert old['current']['superseded'] and old['snapshot']['review']['score'] is None
    assert any(x['kind']=='assessment_review' for x in candidate.get('/api/notifications').json()['items'])

def test_bad_artifact_cannot_be_overridden_by_high_review_score(clients):
    employer,candidate,expert,sign=clients
    session=attempt(candidate,'security','certification',False)
    assert not session['result']['assessment_path']['automatic_passed']
    response,body=rating(expert,session);assert response.status_code==200,response.text
    reviewed=candidate.get('/api/assessments/'+session['id']).json();assert not reviewed['result']['passed']
    with db.connection() as conn:assert not conn.execute('SELECT 1 FROM grade_certifications WHERE session_id=?',(session['id'],)).fetchone()

def test_critical_rubric_minimum_cannot_be_hidden_by_average(clients):
    employer,candidate,expert,sign=clients;session=attempt(candidate)
    response,body=rating(expert,session,{'verification':0});assert response.status_code==200,response.text
    result=candidate.get('/api/assessments/'+session['id']).json()['result']
    assert result['expert_review']['score']==75 and not result['passed']

def test_passport_owner_visibility_access_revoke_and_safe_download(clients):
    employer,candidate,expert,sign=clients;session=attempt(candidate);identity=session['skill_passport']['id']
    original=candidate.get('/api/skill-passports/'+identity).json()
    assert employer.get('/api/skill-passports/'+identity).status_code==404
    assert candidate.put('/api/skill-passports/'+identity+'/visibility',json={'published':True}).status_code==200
    exported=employer.get('/api/skill-passports/'+identity+'/download');assert exported.status_code==200
    assert 'private' not in exported.text and 'reference_answer' not in exported.text and 'answers' not in exported.text and 'contacts' not in exported.text
    assert exported.json()['integrity_sha256']==original['integrity_sha256']
    sign(expert,'u_timur');assert expert.get('/api/skill-passports/'+identity).status_code==404
    assert expert.get('/api/skill-passports?candidate_id=c_anna').status_code==404
    assert employer.put('/api/skill-passports/'+identity+'/visibility',json={'published':True}).status_code==403
    assert candidate.put('/api/skill-passports/'+identity+'/visibility',json={'published':False}).status_code==200
    assert employer.get('/api/skill-passports/'+identity+'/download').status_code==404

def test_snapshot_has_no_reviewer_free_text_and_detects_storage_tampering(clients):
    employer,candidate,expert,sign=clients;session=attempt(candidate)
    queue=next(x for x in expert.get('/api/admin/assessment-reviews').json()['items'] if x['session_id']==session['id'])
    body={'item_id':queue['item_id'],'version':queue['version'],'rationale':'Private reviewer contact secret-review@example.test','criteria':[{'id':x['id'],'level':3,'note':'Private note secret-review@example.test'} for x in RUBRIC]}
    assert expert.post('/api/admin/assessment-reviews/'+session['id'],json=body).status_code==200
    current=candidate.get('/api/assessments/'+session['id']).json()['skill_passport'];identity=current['id']
    assert 'secret-review@example.test' not in candidate.get('/api/skill-passports/'+identity+'/download').text
    with db.connection() as conn:conn.execute('UPDATE assessment_passports SET snapshot=? WHERE id=?',(db.dumps({'changed':True}),identity))
    assert candidate.get('/api/skill-passports/'+identity).status_code==409

def test_public_request_cannot_choose_legacy_and_policy_is_frozen(clients,monkeypatch):
    employer,candidate,expert,sign=clients
    assert candidate.post('/api/assessments',json={'specialization':'qa','grade':'Junior','mode':'practice','assessment_path_version':'legacy'}).status_code==422
    session=attempt(candidate,'qa');monkeypatch.setitem(POLICY,'pass_score',99)
    response,body=rating(expert,session,{'limits':2});assert response.status_code==200,response.text
    result=candidate.get('/api/assessments/'+session['id']).json()['result'];assert result['policy']['pass_score']==70 and result['passed']

def test_accepted_appeal_reopens_review_and_keeps_existing_category(clients):
    employer,candidate,expert,sign=clients;session=attempt(candidate,'devops','certification');rating(expert,session)
    before=candidate.get('/api/assessments/'+session['id']).json();cert=before['result']['certificate_id']
    appeal=candidate.post('/api/assessments/'+session['id']+'/appeal',json={'message':'Прошу повторно рассмотреть аргументацию по сохранённой рубрике.'}).json()
    assert expert.post('/api/admin/assessment-appeals/'+appeal['id']+'/resolve',json={'state':'accepted','resolution':'Принято для повторного разбора по тем же замороженным критериям.'}).status_code==200
    reopened=candidate.get('/api/assessments/'+session['id']).json();assert reopened['skill_passport']['revision']==3
    assert reopened['result']['assessment_path']['review_status']=='pending'
    with db.connection() as conn:assert conn.execute('SELECT active FROM grade_certifications WHERE id=?',(cert,)).fetchone()[0]==1
    response,body=rating(expert,reopened);assert response.status_code==200
    assert candidate.get('/api/assessments/'+session['id']).json()['skill_passport']['revision']==4

def test_late_review_of_old_attempt_cannot_replace_newer_active_category(clients):
    from datetime import datetime,timezone,timedelta
    employer,candidate,expert,sign=clients;session=attempt(candidate,'devops','certification');rating(expert,session)
    with db.connection() as conn:
        conn.execute("UPDATE grade_certifications SET active=0 WHERE candidate_id='c_anna' AND specialization='devops'")
        conn.execute('INSERT INTO grade_certifications VALUES(?,?,?,?,?,?,?,?,?,1)',('newer-active-cert','c_anna','devops','Senior',88,None,'provisional',db.dumps({'calibration':'pilot_unvalidated'}),(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()))
    appeal=candidate.post('/api/assessments/'+session['id']+'/appeal',json={'message':'Повторный разбор прежнего результата не должен заменять новую категорию.'}).json()
    expert.post('/api/admin/assessment-appeals/'+appeal['id']+'/resolve',json={'state':'accepted','resolution':'Повторный разбор прежней попытки с сохранением новой действующей категории.'})
    response,body=rating(expert,session);assert response.status_code==200,response.text
    result=candidate.get('/api/assessments/'+session['id']).json()['result'];assert result['category_update']=='historical_only'
    with db.connection() as conn:
        active=conn.execute("SELECT id,grade FROM grade_certifications WHERE candidate_id='c_anna' AND specialization='devops' AND active=1").fetchall()
        assert len(active)==1 and active[0]['id']=='newer-active-cert' and active[0]['grade']=='Senior'

def test_profile_revocation_and_company_block_close_passport_access(clients):
    employer,candidate,expert,sign=clients;session=attempt(candidate);identity=session['skill_passport']['id']
    candidate.put('/api/skill-passports/'+identity+'/visibility',json={'published':True})
    assert employer.get('/api/skill-passports/'+identity).status_code==200
    candidate.post('/api/employer-blocks',json={'org_id':'org_sigma','blocked':True})
    assert employer.get('/api/skill-passports/'+identity).status_code==404
    candidate.post('/api/employer-blocks',json={'org_id':'org_sigma','blocked':False})
    assert employer.get('/api/skill-passports/'+identity).status_code==200
    candidate.post('/api/consents',json={'kind':'publication','granted':False})
    assert employer.get('/api/skill-passports/'+identity+'/download').status_code==404
    assert candidate.get('/api/skill-passports/'+identity).status_code==200
    assert candidate.put('/api/skill-passports/'+identity+'/visibility',json={'published':True}).status_code==403

@pytest.mark.parametrize('invalid',['duplicate','bad_id','bad_level','blank_note','extra'])
def test_invalid_rubric_does_not_create_a_review_or_passport_revision(clients,invalid):
    employer,candidate,expert,sign=clients;session=attempt(candidate)
    queued=next(x for x in expert.get('/api/admin/assessment-reviews').json()['items'] if x['session_id']==session['id'])
    criteria=[{'id':x['id'],'level':3,'note':'Синтетическое наблюдение в тестовом сценарии.'} for x in RUBRIC]
    if invalid=='duplicate':criteria[1]['id']=criteria[0]['id']
    elif invalid=='bad_id':criteria[0]['id']=[]
    elif invalid=='bad_level':criteria[0]['level']=True
    elif invalid=='blank_note':criteria[0]['note']=' '*20
    else:criteria[0]['unchecked']=True
    response=expert.post('/api/admin/assessment-reviews/'+session['id'],json={'item_id':queued['item_id'],'version':queued['version'],'rationale':'Общий текст не заменяет заполненную валидную рубрику.','criteria':criteria})
    assert response.status_code==422,response.text
    assert candidate.get('/api/assessments/'+session['id']).json()['skill_passport']['revision']==1
