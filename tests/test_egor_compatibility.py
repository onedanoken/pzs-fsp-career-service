"""Peer-authored API regressions for historical work_sample_v1 compatibility.

Production defaults remain work_sample_v3_balanced. These synthetic checks cover
subject-specific frozen rubrics, technical pauses, critical gates and immutable
passport history; they do not establish human calibration or hiring accuracy.
"""
import json
import re

import pytest
from backend import db
from backend.assessment_catalog import BLUEPRINT_VERSION
from backend.jobs import Worker


@pytest.fixture(autouse=True)
def path_enabled(monkeypatch):monkeypatch.setenv('FSP_ASSESSMENT_PATH','work_sample_v1')

def drain():
    worker=Worker()
    for _ in range(100):
        if not worker.run_once():return
    raise AssertionError('Queue did not drain')

def attempt(candidate,spec='devops',mode='practice',valid=True,grade='Junior',artifact_transform=None):
    response=candidate.post('/api/assessments',json={'specialization':spec,'grade':grade,'mode':mode});assert response.status_code==202,response.text
    identity=response.json()['id'];drain();current=candidate.get('/api/assessments/'+identity).json()
    assert len(current['items'])==5 and [x['stage'] for x in current['items']]==['screening']*3+['work_sample','review']
    with db.connection() as conn:data=json.loads(conn.execute('SELECT payload FROM assessment_sessions WHERE id=?',(identity,)).fetchone()[0])
    for item in data['items']:
        if item['kind']=='work_sample':
            answer=item['private']['reference_answer'] if valid else item['starter']
            if artifact_transform:answer=artifact_transform(answer)
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
    body={'item_id':queued['item_id'],'version':queued['version'],'rationale':'Синтетический разбор для проверки маршрута. В нём не проводилась независимая экспертная калибровка.','criteria':[{'id':x['id'],'level':(levels or {}).get(x['id'],3),'note':'Наблюдение тестового сценария; оценка задана синтетически.'} for x in queued['rubric_criteria']]}
    return expert.post('/api/admin/assessment-reviews/'+session['id'],json=body),body

@pytest.mark.parametrize('grade,variant,valid,mode',[
    ('Junior','quantity_boundary',True,'certification'),
    ('Junior','write_before_check',False,'certification'),
    ('Middle','lost_update',False,'certification'),
    ('Middle','partial_commit',True,'certification'),
    ('Middle','lost_update',True,'practice'),
])
def test_stock_family_full_journey_uses_frozen_subject_rubric_and_passports(clients,monkeypatch,grade,variant,valid,mode):
    from backend import work_samples,stock_reservation
    employer,candidate,expert,sign=clients
    monkeypatch.setattr(work_samples,'generate_case',lambda spec,level,rng:stock_reservation.generate(level,rng,variant))
    session=attempt(candidate,'backend',mode,valid,grade)
    rubric=stock_reservation.rubric(grade)
    assert session['assessment_path']['rubric']==rubric
    assert session['result']['assessment_path']['work_family']==stock_reservation.FAMILY
    assert session['result']['passed'] is False
    assert session['skill_passport']['revision']==1
    assert session['skill_passport']['snapshot']['work_sample']['version']==stock_reservation.VERSION
    assert session['skill_passport']['snapshot']['review']['rubric_version']==stock_reservation.RUBRIC_VERSION
    # Simulate a later deployment with changed global content before the review.
    monkeypatch.setattr(work_samples,'RUBRIC',[])
    monkeypatch.setattr(stock_reservation,'rubric',lambda _:[])
    queued=next(x for x in expert.get('/api/admin/assessment-reviews').json()['items'] if x['session_id']==session['id'])
    assert queued['rubric_criteria']==rubric
    with db.connection() as conn:assert not conn.execute('SELECT 1 FROM grade_certifications WHERE session_id=?',(session['id'],)).fetchone()
    response,body=rating(expert,session);assert response.status_code==200,response.text
    reviewed=candidate.get('/api/assessments/'+session['id']).json()
    assert reviewed['result']['passed']==valid
    assert reviewed['skill_passport']['revision']==2
    with db.connection() as conn:
        cert=conn.execute('SELECT grade FROM grade_certifications WHERE session_id=? AND active=1',(session['id'],)).fetchone()
        assert bool(cert)==(valid and mode=='certification')
        if cert:assert cert['grade']==grade
    first=candidate.get('/api/skill-passports/'+session['skill_passport']['id']).json()
    assert first['snapshot']['status']=='awaiting_review' and first['current']['superseded']
    assert employer.get('/api/skill-passports/'+reviewed['skill_passport']['id']).status_code==404
    assert candidate.put('/api/skill-passports/'+reviewed['skill_passport']['id']+'/visibility',json={'published':True}).status_code==200
    exported=employer.get('/api/skill-passports/'+reviewed['skill_passport']['id']+'/download')
    assert exported.status_code==200 and exported.json()['revision']==2
    assert 'reference_answer' not in exported.text and 'quantity_boundary' not in exported.text

@pytest.mark.parametrize('malformed_answer',[False,True])
def test_unsupported_stock_version_pauses_checking_without_a_failed_grade(clients,monkeypatch,malformed_answer):
    from backend import work_samples,stock_reservation
    employer,candidate,expert,sign=clients
    before=candidate.get('/api/assessments').json()['certifications']
    def unsupported(spec,grade,rng):
        item=stock_reservation.generate(grade,rng)
        item['version']='stock-reservation-future'
        if malformed_answer:item['private']['reference_answer']='{'
        return item
    monkeypatch.setattr(work_samples,'generate_case',unsupported)
    session=attempt(candidate,'backend','certification',True,'Middle')
    assert session['status']=='active' and session['technical_error']
    assert session['result'] is None and session['skill_passport'] is None
    assert len(session['answers'])==5
    assert candidate.get('/api/assessments').json()['certifications']==before

@pytest.mark.parametrize('grade,variant,valid,mode',[
    ('Junior','page_collision',True,'certification'),
    ('Junior','cached_failure',False,'certification'),
    ('Middle','global_stream',False,'certification'),
    ('Middle','obsolete_generation',True,'certification'),
    ('Middle','global_stream',True,'practice'),
])
def test_frontend_cache_full_journey_keeps_rubric_grading_and_passports_separate(clients,monkeypatch,grade,variant,valid,mode):
    from backend import work_samples,resource_cache
    employer,candidate,expert,sign=clients
    monkeypatch.setattr(work_samples,'generate_case',lambda spec,level,rng:resource_cache.generate(level,rng,variant))
    before=candidate.get('/api/assessments').json()['certifications']
    session=attempt(candidate,'frontend',mode,valid,grade)
    rubric=resource_cache.rubric(grade)
    assert session['assessment_path']['rubric']==rubric
    assert session['result']['passed'] is False and session['result']['assessment_path']['work_family']==resource_cache.FAMILY
    assert session['skill_passport']['revision']==1 and session['skill_passport']['snapshot']['work_sample']['version']==resource_cache.VERSION
    assert session['skill_passport']['snapshot']['review']['rubric_version']==resource_cache.RUBRIC_VERSION
    with db.connection() as conn:assert not conn.execute('SELECT 1 FROM grade_certifications WHERE session_id=?',(session['id'],)).fetchone()
    monkeypatch.setattr(resource_cache,'rubric',lambda _:[])
    queued=next(x for x in expert.get('/api/admin/assessment-reviews').json()['items'] if x['session_id']==session['id'])
    assert queued['rubric_criteria']==rubric
    response,body=rating(expert,session);assert response.status_code==200,response.text
    reviewed=candidate.get('/api/assessments/'+session['id']).json()
    assert reviewed['result']['passed']==valid and reviewed['skill_passport']['revision']==2
    issued=valid and mode=='certification'
    assert reviewed['skill_passport']['snapshot']['category_issued']==issued
    if not issued:assert candidate.get('/api/assessments').json()['certifications']==before
    passport=reviewed['skill_passport']['id']
    assert employer.get('/api/skill-passports/'+passport).status_code==404
    assert candidate.put('/api/skill-passports/'+passport+'/visibility',json={'published':True}).status_code==200
    exported=employer.get('/api/skill-passports/'+passport+'/download')
    assert exported.status_code==200 and 'reference_answer' not in exported.text
    old=candidate.get('/api/skill-passports/'+session['skill_passport']['id']).json()
    assert old['snapshot']['status']=='awaiting_review' and old['current']['superseded']

@pytest.mark.parametrize('malformed_answer',[False,True])
def test_unsupported_cache_version_preserves_answers_without_grading(clients,monkeypatch,malformed_answer):
    from backend import work_samples,resource_cache
    employer,candidate,expert,sign=clients
    before=candidate.get('/api/assessments').json()['certifications']
    def unsupported(spec,grade,rng):
        item=resource_cache.generate(grade,rng)
        item['version']='resource-cache-future'
        if malformed_answer:item['private']['reference_answer']='{'
        return item
    monkeypatch.setattr(work_samples,'generate_case',unsupported)
    session=attempt(candidate,'frontend','certification',True,'Middle')
    assert session['status']=='active' and session['technical_error']
    assert session['result'] is None and session['skill_passport'] is None and len(session['answers'])==5
    assert candidate.get('/api/assessments').json()['certifications']==before

@pytest.mark.parametrize('grade,variant,valid,mode',[
    ('Junior','success_only',True,'certification'),
    ('Junior','copied_expectations',False,'certification'),
    ('Middle','isolated_requests',False,'certification'),
    ('Middle','copied_crash_state',True,'certification'),
    ('Middle','isolated_requests',True,'practice'),
])
def test_qa_suite_full_journey_requires_truthful_tests_and_frozen_rubric(clients,monkeypatch,grade,variant,valid,mode):
    from backend import work_samples,reservation_test_suite
    employer,candidate,expert,sign=clients
    monkeypatch.setattr(work_samples,'generate_case',lambda spec,level,rng:reservation_test_suite.generate(level,rng,variant))
    before=candidate.get('/api/assessments').json()['certifications']
    session=attempt(candidate,'qa',mode,valid,grade)
    rubric=reservation_test_suite.rubric(grade)
    assert session['assessment_path']['rubric']==rubric
    assert session['result']['passed'] is False and session['result']['assessment_path']['work_family']==reservation_test_suite.FAMILY
    assert session['skill_passport']['revision']==1
    assert session['skill_passport']['snapshot']['work_sample']['version']==reservation_test_suite.VERSION
    assert session['skill_passport']['snapshot']['review']['rubric_version']==reservation_test_suite.RUBRIC_VERSION
    assert candidate.get('/api/assessments').json()['certifications']==before
    monkeypatch.setattr(reservation_test_suite,'rubric',lambda _:[])
    queued=next(x for x in expert.get('/api/admin/assessment-reviews').json()['items'] if x['session_id']==session['id'])
    assert queued['rubric_criteria']==rubric
    response,body=rating(expert,session);assert response.status_code==200,response.text
    reviewed=candidate.get('/api/assessments/'+session['id']).json()
    assert reviewed['result']['passed']==valid and reviewed['skill_passport']['revision']==2
    issued=valid and mode=='certification'
    assert reviewed['skill_passport']['snapshot']['category_issued']==issued
    if not issued:assert candidate.get('/api/assessments').json()['certifications']==before
    passport=reviewed['skill_passport']['id']
    assert employer.get('/api/skill-passports/'+passport).status_code==404
    assert candidate.put('/api/skill-passports/'+passport+'/visibility',json={'published':True}).status_code==200
    exported=employer.get('/api/skill-passports/'+passport+'/download')
    assert exported.status_code==200 and 'reference_answer' not in exported.text
    old=candidate.get('/api/skill-passports/'+session['skill_passport']['id']).json()
    assert old['snapshot']['status']=='awaiting_review' and old['current']['superseded']

def test_qa_missing_race_cannot_be_overridden_by_high_review(clients,monkeypatch):
    from backend import work_samples,reservation_test_suite
    employer,candidate,expert,sign=clients
    monkeypatch.setattr(work_samples,'generate_case',lambda spec,level,rng:reservation_test_suite.generate(level,rng))
    def omit_race(answer):
        suite=json.loads(answer)
        suite['tests']=[case for case in suite['tests'] if case['schedule']!='parallel']
        return json.dumps(suite)
    session=attempt(candidate,'qa','certification',True,'Middle',omit_race)
    assert session['result']['assessment_path']['work_score']==85.71
    assert session['result']['assessment_path']['work_passed'] is False
    response,body=rating(expert,session);assert response.status_code==200,response.text
    reviewed=candidate.get('/api/assessments/'+session['id']).json()
    assert reviewed['result']['expert_review']['score']==100 and reviewed['result']['passed'] is False
    assert not reviewed['skill_passport']['snapshot']['category_issued']

@pytest.mark.parametrize('malformed_answer',[False,True])
def test_unsupported_qa_suite_version_preserves_answers_without_assessment(clients,monkeypatch,malformed_answer):
    from backend import work_samples,reservation_test_suite
    employer,candidate,expert,sign=clients
    before=candidate.get('/api/assessments').json()['certifications']
    def unsupported(spec,grade,rng):
        item=reservation_test_suite.generate(grade,rng)
        item['version']='reservation-tests-future'
        if malformed_answer:item['private']['reference_answer']='{'
        return item
    monkeypatch.setattr(work_samples,'generate_case',unsupported)
    session=attempt(candidate,'qa','certification',True,'Middle')
    assert session['status']=='active' and session['technical_error']
    assert session['result'] is None and session['skill_passport'] is None and len(session['answers'])==5
    assert candidate.get('/api/assessments').json()['certifications']==before

@pytest.mark.parametrize('grade,variant,valid,mode',[
    ('Junior','missing_scan',True,'certification'),
    ('Junior','foreign_artifact',False,'certification'),
    ('Middle','premature_drain',False,'certification'),
    ('Middle','destructive_rollback',True,'certification'),
    ('Middle','premature_drain',True,'practice'),
])
def test_release_full_journey_requires_safe_trace_and_frozen_rubric(clients,monkeypatch,grade,variant,valid,mode):
    from backend import work_samples,release_rollout
    employer,candidate,expert,sign=clients
    monkeypatch.setattr(work_samples,'generate_case',lambda spec,level,rng:release_rollout.generate(level,rng,variant))
    before=candidate.get('/api/assessments').json()['certifications']
    session=attempt(candidate,'devops',mode,valid,grade)
    rubric=release_rollout.rubric(grade)
    assert session['assessment_path']['rubric']==rubric
    assert session['result']['passed'] is False and session['result']['assessment_path']['work_family']==release_rollout.FAMILY
    assert session['skill_passport']['revision']==1
    assert session['skill_passport']['snapshot']['work_sample']['version']==release_rollout.VERSION
    assert session['skill_passport']['snapshot']['review']['rubric_version']==release_rollout.RUBRIC_VERSION
    assert candidate.get('/api/assessments').json()['certifications']==before
    if grade=='Middle' and not valid and variant=='premature_drain':assert session['result']['assessment_path']['work_score']==87.5
    monkeypatch.setattr(release_rollout,'rubric',lambda _:[])
    queued=next(x for x in expert.get('/api/admin/assessment-reviews').json()['items'] if x['session_id']==session['id'])
    assert queued['rubric_criteria']==rubric
    response,body=rating(expert,session);assert response.status_code==200,response.text
    reviewed=candidate.get('/api/assessments/'+session['id']).json()
    assert reviewed['result']['passed']==valid and reviewed['result']['expert_review']['score']==100
    assert reviewed['skill_passport']['revision']==2
    issued=valid and mode=='certification'
    assert reviewed['skill_passport']['snapshot']['category_issued']==issued
    if not issued:assert candidate.get('/api/assessments').json()['certifications']==before
    passport=reviewed['skill_passport']['id']
    assert employer.get('/api/skill-passports/'+passport).status_code==404
    assert candidate.put('/api/skill-passports/'+passport+'/visibility',json={'published':True}).status_code==200
    exported=employer.get('/api/skill-passports/'+passport+'/download')
    assert exported.status_code==200 and 'reference_answer' not in exported.text
    old=candidate.get('/api/skill-passports/'+session['skill_passport']['id']).json()
    assert old['snapshot']['status']=='awaiting_review' and old['current']['superseded']

@pytest.mark.parametrize('malformed_answer',[False,True])
def test_unsupported_release_version_preserves_answers_without_assessment(clients,monkeypatch,malformed_answer):
    from backend import work_samples,release_rollout
    employer,candidate,expert,sign=clients
    before=candidate.get('/api/assessments').json()['certifications']
    def unsupported(spec,grade,rng):
        item=release_rollout.generate(grade,rng)
        item['version']='release-rollout-future'
        if malformed_answer:item['private']['reference_answer']='{'
        return item
    monkeypatch.setattr(work_samples,'generate_case',unsupported)
    session=attempt(candidate,'devops','certification',True,'Middle')
    assert session['status']=='active' and session['technical_error']
    assert session['result'] is None and session['skill_passport'] is None and len(session['answers'])==5
    assert candidate.get('/api/assessments').json()['certifications']==before

@pytest.mark.parametrize('grade,variant,valid,mode',[
    ('Junior','fit_holdout',True,'certification'),
    ('Junior','future_feature',False,'certification'),
    ('Middle','test_threshold',False,'certification'),
    ('Middle','late_label',True,'certification'),
    ('Middle','test_threshold',True,'practice'),
])
def test_temporal_ml_journey_preserves_dataset_rubric_and_passports(clients,monkeypatch,grade,variant,valid,mode):
    from backend import work_samples,temporal_validation
    employer,candidate,expert,sign=clients
    assert employer.get('/api/admin/assessment-reviews').status_code==403
    assert candidate.get('/api/admin/assessment-reviews').status_code==403
    monkeypatch.setattr(work_samples,'generate_case',lambda spec,level,rng:temporal_validation.generate(level,rng,variant))
    before=candidate.get('/api/assessments').json()['certifications']
    session=attempt(candidate,'ml',mode,valid,grade)
    rubric=temporal_validation.rubric(grade)
    assert session['assessment_path']['rubric']==rubric
    assert session['result']['passed'] is False and session['result']['assessment_path']['work_family']==temporal_validation.FAMILY
    assert session['skill_passport']['revision']==1
    assert session['skill_passport']['snapshot']['work_sample']['version']==temporal_validation.VERSION
    assert session['skill_passport']['snapshot']['review']['rubric_version']==temporal_validation.RUBRIC_VERSION
    assert candidate.get('/api/assessments').json()['certifications']==before
    monkeypatch.setattr(temporal_validation,'rubric',lambda _:[])
    queued=next(x for x in expert.get('/api/admin/assessment-reviews').json()['items'] if x['session_id']==session['id'])
    assert queued['rubric_criteria']==rubric
    assert len(queued['work_sample']['example_table']['rows'])==8
    case=next(x for x in session['items'] if x['kind']=='work_sample')
    assert queued['work_sample']['example_table']==case['example_table']
    response,body=rating(expert,session);assert response.status_code==200,response.text
    reviewed=candidate.get('/api/assessments/'+session['id']).json()
    assert reviewed['result']['passed']==valid and reviewed['skill_passport']['revision']==2
    issued=valid and mode=='certification'
    assert reviewed['skill_passport']['snapshot']['category_issued']==issued
    if not issued:assert candidate.get('/api/assessments').json()['certifications']==before
    passport=reviewed['skill_passport']['id']
    assert employer.get('/api/skill-passports/'+passport).status_code==404
    assert candidate.put('/api/skill-passports/'+passport+'/visibility',json={'published':True}).status_code==200
    exported=employer.get('/api/skill-passports/'+passport+'/download')
    assert exported.status_code==200 and 'reference_answer' not in exported.text
    assert 'example_table' not in exported.text and 'probability' not in exported.text
    old=candidate.get('/api/skill-passports/'+session['skill_passport']['id']).json()
    assert old['snapshot']['status']=='awaiting_review' and old['current']['superseded']

def test_ml_using_test_for_threshold_does_not_pass_even_if_cost_is_correct(clients,monkeypatch):
    from backend import work_samples,temporal_validation
    employer,candidate,expert,sign=clients
    monkeypatch.setattr(work_samples,'generate_case',lambda spec,level,rng:temporal_validation.generate(level,rng))
    def use_test(answer):
        plan=json.loads(answer);plan['threshold_rows']=plan['test']
        return json.dumps(plan)
    session=attempt(candidate,'ml','certification',True,'Middle',use_test)
    assert session['result']['assessment_path']['work_score']==85.71
    assert not session['result']['assessment_path']['work_passed']
    response,body=rating(expert,session);assert response.status_code==200,response.text
    reviewed=candidate.get('/api/assessments/'+session['id']).json()
    assert reviewed['result']['expert_review']['score']==100 and not reviewed['result']['passed']
    assert not reviewed['skill_passport']['snapshot']['category_issued']

@pytest.mark.parametrize('malformed_answer',[False,True])
def test_unsupported_temporal_version_preserves_answers_without_grade(clients,monkeypatch,malformed_answer):
    from backend import work_samples,temporal_validation
    employer,candidate,expert,sign=clients
    before=candidate.get('/api/assessments').json()['certifications']
    def unsupported(spec,grade,rng):
        item=temporal_validation.generate(grade,rng)
        item['version']='temporal-validation-future'
        if malformed_answer:item['private']['reference_answer']='{'
        return item
    monkeypatch.setattr(work_samples,'generate_case',unsupported)
    session=attempt(candidate,'ml','certification',True,'Middle')
    assert session['status']=='active' and session['technical_error']
    assert session['result'] is None and session['skill_passport'] is None and len(session['answers'])==5
    assert candidate.get('/api/assessments').json()['certifications']==before

@pytest.mark.parametrize('grade,variant,valid,mode',[
    ('Junior','or_policy',True,'certification'),
    ('Junior','missing_object',False,'certification'),
    ('Middle','cached_allow',False,'certification'),
    ('Middle','stale_use',False,'certification'),
    ('Middle','stale_use',True,'certification'),
    ('Middle','cached_allow',True,'practice'),
])
def test_object_access_journey_requires_all_security_gates_and_frozen_rubric(clients,monkeypatch,grade,variant,valid,mode):
    from backend import work_samples,object_access
    employer,candidate,expert,sign=clients
    monkeypatch.setattr(work_samples,'generate_case',lambda spec,level,rng:object_access.generate(level,rng,variant))
    before=candidate.get('/api/assessments').json()['certifications']
    session=attempt(candidate,'security',mode,valid,grade)
    rubric=object_access.rubric(grade)
    assert session['assessment_path']['rubric']==rubric
    assert session['result']['passed'] is False and session['result']['assessment_path']['work_family']==object_access.FAMILY
    assert session['skill_passport']['revision']==1
    assert session['skill_passport']['snapshot']['work_sample']['version']==object_access.VERSION
    assert session['skill_passport']['snapshot']['review']['rubric_version']==object_access.RUBRIC_VERSION
    assert candidate.get('/api/assessments').json()['certifications']==before
    if not valid:assert session['result']['assessment_path']['work_score']=={'missing_object':80,'cached_allow':75,'stale_use':87.5}[variant]
    monkeypatch.setattr(object_access,'rubric',lambda _:[])
    queued=next(x for x in expert.get('/api/admin/assessment-reviews').json()['items'] if x['session_id']==session['id'])
    assert queued['rubric_criteria']==rubric
    response,body=rating(expert,session);assert response.status_code==200,response.text
    reviewed=candidate.get('/api/assessments/'+session['id']).json()
    assert reviewed['result']['passed']==valid and reviewed['result']['expert_review']['score']==100
    assert reviewed['skill_passport']['revision']==2
    issued=valid and mode=='certification'
    assert reviewed['skill_passport']['snapshot']['category_issued']==issued
    if not issued:assert candidate.get('/api/assessments').json()['certifications']==before
    passport=reviewed['skill_passport']['id']
    assert employer.get('/api/skill-passports/'+passport).status_code==404
    assert candidate.put('/api/skill-passports/'+passport+'/visibility',json={'published':True}).status_code==200
    exported=employer.get('/api/skill-passports/'+passport+'/download')
    assert exported.status_code==200 and 'reference_answer' not in exported.text
    old=candidate.get('/api/skill-passports/'+session['skill_passport']['id']).json()
    assert old['snapshot']['status']=='awaiting_review' and old['current']['superseded']

@pytest.mark.parametrize('malformed_answer',[False,True])
def test_unsupported_object_access_version_preserves_answers_without_grade(clients,monkeypatch,malformed_answer):
    from backend import work_samples,object_access
    employer,candidate,expert,sign=clients
    before=candidate.get('/api/assessments').json()['certifications']
    def unsupported(spec,grade,rng):
        item=object_access.generate(grade,rng)
        item['version']='object-access-future'
        if malformed_answer:item['private']['reference_answer']='{'
        return item
    monkeypatch.setattr(work_samples,'generate_case',unsupported)
    session=attempt(candidate,'security','certification',True,'Middle')
    assert session['status']=='active' and session['technical_error']
    assert session['result'] is None and session['skill_passport'] is None and len(session['answers'])==5
    assert candidate.get('/api/assessments').json()['certifications']==before

def test_matching_appeal_uses_latest_revision_and_preserves_old_need_snapshot(clients):
    from test_stage03 import create_need,invite
    from backend.recruiting import digest
    employer,candidate,expert,sign=clients
    session=attempt(candidate,'backend','certification',grade='Middle')
    response,_=rating(expert,session);assert response.status_code==200
    reviewed=candidate.get('/api/assessments/'+session['id']).json()
    assert candidate.put('/api/skill-passports/'+reviewed['skill_passport']['id']+'/visibility',json={'published':True}).status_code==200
    need=create_need(employer);invitation=invite(employer,need);npid=invitation['payload']['passport_id']
    # Historical v1 ranking snapshots are verified as saved, and marked outdated.
    with db.connection() as conn:
        row=conn.execute('SELECT snapshot FROM need_passports WHERE id=?',(npid,)).fetchone()
        historical=json.loads(row[0]);historical['match']['algorithm']='category-evidence-v1'
        conn.execute('UPDATE need_passports SET snapshot=?,integrity_sha256=? WHERE id=?',(db.dumps(historical),digest(historical),npid))
    old=employer.get('/api/need-passports/'+npid).json();assert old['integrity_valid'] and old['stale'] and old['snapshot']==historical
    appeal=candidate.post('/api/assessments/'+session['id']+'/appeal',json={'message':'Прошу повторно проверить аргументацию по сохранённой рубрике.'}).json()
    assert expert.post('/api/admin/assessment-appeals/'+appeal['id']+'/resolve',json={'state':'accepted','resolution':'Принимаем апелляцию для повторного разбора замороженного результата.'}).status_code==200
    def evidence():
        items=employer.post('/api/search-sessions',json={'need_id':need}).json()['items']
        return next(x['match']['assessment'] for x in items if x['candidate']['id']=='c_anna')
    pending=evidence()
    assert pending['passport']['revision']==3 and pending['passport']['status']=='awaiting_review'
    assert pending['stages']['review']['score'] is None
    assert not pending['passport']['category_confirmed_by_this_revision']
    reopened=candidate.get('/api/assessments/'+session['id']).json()
    response,_=rating(expert,reopened,{'correctness':0});assert response.status_code==200
    failed=evidence()
    assert failed['passport']['revision']==4 and failed['passport']['status']=='reviewed_needs_work'
    assert not failed['passport']['category_confirmed_by_this_revision']
    assert employer.get('/api/need-passports/'+npid).json()['snapshot']==historical

def test_legacy_and_synthetic_scores_keep_their_original_meaning(clients,monkeypatch):
    from test_stage03 import create_need,fill_assessment
    employer,candidate,expert,sign=clients
    need=create_need(employer)
    def match():
        items=employer.post('/api/search-sessions',json={'need_id':need}).json()['items']
        return next(x['match'] for x in items if x['candidate']['id']=='c_anna')
    synthetic=match()
    assert synthetic['assessment']['score_semantics']=='synthetic_example'
    assert synthetic['assessment']['stages'] is None
    monkeypatch.setenv('FSP_ASSESSMENT_PATH','legacy')
    started=candidate.post('/api/assessments',json={'specialization':'backend','grade':'Middle'});assert started.status_code==202,started.text
    drain();legacy=fill_assessment(candidate,started.json()['id'])
    assert legacy['result']['passed']
    real=match()
    assert real['assessment']['score_semantics']=='legacy_objective_score'
    assert real['assessment']['path_version']=='legacy' and real['assessment']['stages'] is None
    with db.connection() as conn:
        frozen=json.loads(conn.execute('SELECT payload FROM assessment_sessions WHERE id=?',(legacy['id'],)).fetchone()[0])
    assert real['assessment']['blueprint_version']==frozen['blueprint_version']
    assert real['certification']['score']==legacy['result']['score']

@pytest.mark.parametrize('variant,valid,mode', [
    ('late_payment',True,'certification'), ('lost_refund',True,'certification'),
    ('late_payment',False,'certification'), ('lost_refund',False,'certification'),
    ('late_payment',True,'practice'), ('lost_refund',True,'practice'),
])
def test_senior_settlement_full_journey_freezes_rubric_and_protects_category(clients,monkeypatch,variant,valid,mode):
    from backend import work_samples,reservation_settlement as settlement
    from test_stage03 import NEED
    employer,candidate,expert,sign=clients
    monkeypatch.setattr(work_samples,'generate_case',lambda spec,grade,rng:settlement.generate(grade,rng,variant))
    before=candidate.get('/api/assessments').json()['certifications']
    session=attempt(candidate,'backend',mode,valid,grade='Senior')
    assert session['assessment_path']['rubric']==settlement.rubric()
    assert session['result']['passed'] is False
    assert session['result']['assessment_path']['work_passed']==valid
    if not valid and variant=='lost_refund':assert session['result']['assessment_path']['work_score']==87.5
    first=session['skill_passport']
    assert first['revision']==1 and first['snapshot']['work_sample']['version']==settlement.VERSION
    assert first['snapshot']['review']['rubric_version']==settlement.RUBRIC_VERSION
    assert first['snapshot']['blueprint_version']==BLUEPRINT_VERSION
    assert candidate.get('/api/assessments').json()['certifications']==before
    assert 'reference_answer' not in json.dumps(session) and 'late_payment' not in json.dumps(session)
    frozen=settlement.rubric();monkeypatch.setattr(settlement,'rubric',lambda:[])
    queue=next(x for x in expert.get('/api/admin/assessment-reviews').json()['items'] if x['session_id']==session['id'])
    assert queue['rubric_criteria']==frozen
    response,_=rating(expert,session);assert response.status_code==200,response.text
    reviewed=candidate.get('/api/assessments/'+session['id']).json()
    assert reviewed['result']['passed']==valid and reviewed['skill_passport']['revision']==2
    issued=valid and mode=='certification'
    assert reviewed['skill_passport']['snapshot']['category_issued']==issued
    if not issued:assert candidate.get('/api/assessments').json()['certifications']==before
    with db.connection() as conn:
        cert=conn.execute('SELECT grade FROM grade_certifications WHERE session_id=? AND active=1',(session['id'],)).fetchone()
        assert bool(cert)==issued
        if cert:assert cert['grade']=='Senior'
    passport=reviewed['skill_passport']['id']
    assert employer.get('/api/skill-passports/'+passport).status_code==404
    assert candidate.put('/api/skill-passports/'+passport+'/visibility',json={'published':True}).status_code==200
    exported=employer.get('/api/skill-passports/'+passport+'/download');assert exported.status_code==200
    assert exported.json()['snapshot']['calibration']=='pilot_unvalidated'
    assert 'reference_answer' not in exported.text and 'rationale' not in exported.text
    if issued:
        need=employer.post('/api/needs',json=NEED|{'grade':'Senior'});assert need.status_code==201,need.text
        items=employer.post('/api/search-sessions',json={'need_id':need.json()['id']}).json()['items']
        match=next(x['match'] for x in items if x['candidate']['id']=='c_anna')
        assert match['assessment']['session_id']==session['id']
        assert match['assessment']['stages']['work_sample']['family']==settlement.FAMILY
        assert match['assessment']['stages']['review']['rubric_version']==settlement.RUBRIC_VERSION
    assert candidate.put('/api/skill-passports/'+passport+'/visibility',json={'published':False}).status_code==200
    assert employer.get('/api/skill-passports/'+passport).status_code==404

@pytest.mark.parametrize('malformed_answer',[False,True])
def test_unknown_senior_model_pauses_grading_and_keeps_answers(clients,monkeypatch,malformed_answer):
    from backend import work_samples,reservation_settlement as settlement
    employer,candidate,expert,sign=clients
    before=candidate.get('/api/assessments').json()['certifications']
    def unsupported(spec,grade,rng):
        item=settlement.generate(grade,rng)
        item['private']['condition']['model_version']='future-model'
        if malformed_answer:item['private']['reference_answer']='{'
        return item
    monkeypatch.setattr(work_samples,'generate_case',unsupported)
    session=attempt(candidate,'backend','certification',grade='Senior')
    assert session['status']=='active' and session['technical_error']
    assert session['result'] is None and session['skill_passport'] is None
    assert len(session['answers'])==5
    assert candidate.get('/api/assessments').json()['certifications']==before
