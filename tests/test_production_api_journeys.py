"""API journeys with real username accounts and production authentication flags.

Disposable PostgreSQL schemas retain seeded historic rows to exercise migration isolation.
Assessment success uses a test-only oracle; it is not a human qualification pilot.
"""
import sys, json, os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend.main import app
from test_balanced_lifecycle import issue, drain
from test_career_contact import NEED

PASSWORD = 'Isolated acceptance phrase 2026!'


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


@pytest.fixture
def normal(clients, monkeypatch):
    from scripts.create_admin import create
    for key, value in {'FSP_DEMO':'0', 'FSP_TEST_LOGIN':'0',
                       'FSP_LEGACY_EMAIL_AUTH':'0', 'FSP_ALLOW_REGISTRATION':'1',
                       'FSP_ASSESSMENT_PATH':'work_sample_v3_balanced',
                       'FSP_MATCH_POLICY':'category-evidence-v2'}.items():
        monkeypatch.setenv(key, value)
    for name, module in list(sys.modules.items()):
        if name.startswith('backend.') and hasattr(module, 'DEMO'):
            monkeypatch.setattr(module, 'DEMO', False)
    employer, candidate, admin, _ = clients
    for client in (employer, candidate, admin):
        client.cookies.clear()
        client.headers.pop('X-CSRF-Token', None)
    people = {}
    def register(client, username, role='candidate'):
        data = ok(client.post('/api/auth/register', json={
            'username':username, 'name':'Проверка API', 'role':role,
            'password':PASSWORD, 'company':'Изолированная компания' if role=='employer' else None,
            'processing_consent':True}), 201)
        client.headers['X-CSRF-Token'] = data['csrf']
        people[username] = data
        return data
    register(candidate, 'api.candidate')
    register(employer, 'api.employer', 'employer')
    create('api.owner', 'Изолированный владелец', PASSWORD)
    login = ok(admin.post('/api/auth/login', json={'username':'api.owner', 'password':PASSWORD}))
    admin.headers['X-CSRF-Token'] = login['csrf']
    login = ok(admin.post('/api/auth/password', json={'current_password':PASSWORD, 'password':PASSWORD+'changed'}))
    admin.headers['X-CSRF-Token'] = login['csrf']
    with TestClient(app, raise_server_exceptions=True) as outsider:
        register(outsider, 'api.outsider', 'employer')
        yield employer, candidate, admin, outsider, people


def trusted(normal):
    employer, _, admin, _, people = normal
    body = {'description':'Разработка программных продуктов и инфраструктуры',
            'industry':'IT', 'domain':'isolated.example.org', 'contact':'hr@isolated.example.org',
            'legal_name':'Изолированный API стенд', 'tax_id':'1234567890',
            'team':'Команда разработки продукта', 'version':0}
    company = ok(employer.put('/api/company', json=body))
    ok(admin.post('/api/admin/companies/'+people['api.employer']['org_id']+'/review', json={
        'state':'verified', 'version':company['version'],
        'organization_evidence':'Synthetic fixture evidence; not actual verification',
        'authority_evidence':'Synthetic fixture authority; not actual verification'}))


def ready(normal):
    employer, candidate, _, _, people = normal
    state = issue(candidate, spec='qa')
    assert state['result']['passed']
    ok(candidate.post('/api/assessments/onboarding/complete', json={}))
    profile = ok(candidate.get('/api/profile'))
    body = {'name':'API специалист', 'title':'QA инженер', 'role':'qa', 'city':'Пермь',
            'skills':['python','sql'], 'bio':'Проверка полного пути пользователя через API',
            'visible':True, 'work_modes':['remote'], 'contacts':{'telegram':'@isolated_check'},
            'version':profile['version']}
    ok(candidate.put('/api/profile', json=body))
    ok(candidate.post('/api/consents', json={'kind':'publication','granted':True}))
    return state


def invited(normal):
    employer, candidate, _, _, people = normal
    trusted(normal); ready(normal)
    need = ok(employer.post('/api/needs', json=NEED | {'specialization':'qa'}), 201)
    body = {'need_id':need['id'], 'candidate_id':people['api.candidate']['candidate_id'],
            'message':'Обсудим продукт и задачи команды',
            'next_step':'Встреча с командой и обсуждение условий',
            'idempotency_key':'production-invite-api-0001'}
    item = ok(employer.post('/api/invitations', json=body), 201)
    return need, body, item


@pytest.mark.parametrize('role,paths', [
    ('candidate',['/api/career/overview','/api/profile','/api/assessments','/api/skill-passports',
                  '/api/vacancies','/api/invitations','/api/applications','/api/consents',
                  '/api/complaints','/api/notifications','/api/regular-cases','/api/fsp/identities']),
    ('employer',['/api/career/overview','/api/company','/api/company/members','/api/needs',
                 '/api/candidates','/api/vacancies','/api/invitations','/api/applications',
                 '/api/ats-connections','/api/regular-case-attempts']),
    ('owner',['/api/admin/overview','/api/admin/users','/api/admin/audit','/api/admin/companies',
              '/api/admin/needs','/api/admin/complaints','/api/admin/assessment-reviews',
              '/api/admin/vacancy-reviews','/api/admin/integration-status'])])
def test_normal_role_first_pass(normal, role, paths):
    employer, candidate, admin, _, _ = normal
    person = {'candidate':candidate,'employer':employer,'owner':admin}[role]
    for path in paths:
        response = person.get(path)
        assert response.status_code == 200, (path, response.text)
        assert response.headers.get('cache-control') == 'no-store'


def test_registration_identity_and_production_guards(normal):
    employer, candidate, admin, outsider, people = normal
    assert people['api.candidate']['email'] is None
    assert candidate.post('/api/auth/demo', json={'account':'u_admin'}).status_code == 404
    assert candidate.post('/api/auth/login', json={'email':'anna@example.org','password':'demo'}).status_code == 401
    duplicate = {'username':'API.CANDIDATE','name':'Дубликат', 'role':'candidate',
                 'password':PASSWORD,'processing_consent':True}
    assert outsider.post('/api/auth/register', json=duplicate).status_code == 409
    for patch in [{'role':'admin'}, {'processing_consent':False}, {'password':'short'}, {'username':'root'}]:
        assert outsider.post('/api/auth/register', json=duplicate | patch).status_code == 422
    assert candidate.get('/api/admin/users').status_code == 403
    assert employer.get('/api/profile').status_code == 403
    assert candidate.get('/api/candidates').status_code == 403
    anonymous = TestClient(app)
    assert anonymous.get('/api/profile').status_code == 401
    assert anonymous.get('/api/admin/users').status_code == 401


def test_csrf_origin_profile_conflict_and_self_claims(normal):
    _, candidate, _, outsider, people = normal
    profile = ok(candidate.get('/api/profile'))
    body = {'name':'Самоописание API','title':'QA инженер','role':'qa','city':'',
            'skills':['python'], 'bio':'','visible':True,'work_modes':['remote'],
            'version':profile['version'], 'declared_grade':'Senior'}
    token = candidate.headers.pop('X-CSRF-Token')
    assert candidate.put('/api/profile', json=body).status_code == 403
    candidate.headers['X-CSRF-Token'] = token
    assert candidate.put('/api/profile', json=body, headers={'Origin':'https://untrusted.invalid'}).status_code == 403
    ok(candidate.put('/api/profile', json=body))
    assert candidate.put('/api/profile', json=body).status_code == 409
    assert outsider.get('/api/candidates/'+people['api.candidate']['candidate_id']).status_code == 404
    assert not ok(candidate.get('/api/assessments'))['certifications']


def test_username_recovery_rotation_logout_and_session_invalidation(normal):
    _, candidate, _, outsider, people = normal
    old = people['api.candidate']['recovery_code']
    fresh = ok(candidate.post('/api/auth/recovery-code', json={'current_password':PASSWORD}))['recovery_code']
    body = {'username':'api.candidate','recovery_code':old,'password':PASSWORD+'reset'}
    assert outsider.post('/api/auth/recover', json=body).status_code == 400
    ok(outsider.post('/api/auth/recover', json=body | {'recovery_code':fresh}))
    assert candidate.get('/api/auth/me').status_code == 401
    assert outsider.post('/api/auth/recover', json=body | {'recovery_code':fresh}).status_code == 400
    assert candidate.post('/api/auth/login', json={'username':'api.candidate','password':PASSWORD}).status_code == 401
    login = ok(candidate.post('/api/auth/login', json={'username':'api.candidate','password':PASSWORD+'reset'}))
    candidate.headers['X-CSRF-Token'] = login['csrf']
    ok(candidate.post('/api/auth/logout', json={}))
    assert candidate.get('/api/auth/me').status_code == 401


@pytest.mark.parametrize('spec',['backend','frontend','qa','devops','ml','security'])
def test_normal_balanced_certification_and_history(normal, spec):
    employer, candidate, _, outsider, _ = normal
    state = issue(candidate, spec=spec)
    assert state['result']['passed'] and state['skill_passport']['snapshot']['category_issued']
    assert state['assembly_plan']['count'] == 9
    assert employer.get('/api/assessments/'+state['id']).status_code == 403
    assert outsider.get('/api/skill-passports/'+state['skill_passport']['id']).status_code == 404
    exported = candidate.get('/api/skill-passports/'+state['skill_passport']['id']+'/download')
    assert exported.status_code == 200 and exported.headers['content-disposition'].startswith('attachment;')
    assert len(ok(candidate.get('/api/assessments'))['certifications']) == 1
    assert 'private' not in candidate.get('/api/assessments/'+state['id']).text


def test_normal_autosave_cancel_processing_and_wrong_grade(normal):
    _, candidate, _, outsider, _ = normal
    assert candidate.post('/api/assessments',json={'specialization':'unknown','grade':'Middle'}).status_code == 422
    started = ok(candidate.post('/api/assessments',json={'specialization':'qa','grade':'Junior','mode':'practice'}),202)
    assert candidate.post('/api/assessments',json={'specialization':'qa','grade':'Junior','mode':'practice'}).status_code == 409
    drain(); state = ok(candidate.get('/api/assessments/'+started['id']))
    path = '/api/assessments/'+state['id']
    body = {'version':state['version'],'item_id':state['items'][0]['id'],'answer':'Сохранённый черновик'}
    updated = ok(candidate.put(path+'/answer',json=body))
    assert candidate.put(path+'/answer',json=body).status_code == 409
    assert ok(candidate.get(path))['answers'][body['item_id']] == body['answer']
    assert outsider.get('/api/jobs/'+started['job_id']).status_code == 404
    ok(candidate.post(path+'/cancel',json={'version':updated['version']}))
    assert candidate.put(path+'/answer',json=body | {'version':updated['version']}).status_code == 409
    assert not ok(candidate.get('/api/assessments'))['certifications']
    ok(candidate.post('/api/consents',json={'kind':'processing','granted':False}))
    assert candidate.post('/api/assessments',json={'specialization':'qa','grade':'Junior'}).status_code == 403


@pytest.mark.parametrize('bad_transfer',[False,True])
def test_normal_practice_and_failed_transfer_do_not_issue_category(normal,bad_transfer):
    _, candidate, _, _, _ = normal
    state = issue(candidate, mode='certification' if bad_transfer else 'practice', bad_transfer=bad_transfer)
    assert not state['skill_passport']['snapshot']['category_issued']
    assert not ok(candidate.get('/api/assessments'))['certifications']


def test_normal_invitation_contact_gate_idempotency_and_org_isolation(normal):
    employer, candidate, _, outsider, people = normal
    need, body, item = invited(normal)
    identity = people['api.candidate']['candidate_id']
    assert ok(employer.get('/api/candidates/'+identity))['contacts'] == {}
    assert ok(employer.post('/api/invitations',json=body),201)['id'] == item['id']
    assert employer.post('/api/invitations',json=body | {'message':'Изменённое сообщение'}).status_code == 409
    assert outsider.get('/api/need-passports/'+item['payload']['passport_id']).status_code == 404
    assert outsider.get('/api/invitations').json()['items'] == []
    accepted = ok(candidate.post('/api/invitations/'+item['id']+'/respond',json={'action':'accepted','version':item['version']}))
    assert ok(employer.get('/api/candidates/'+identity))['contacts']['telegram'] == '@isolated_check'
    assert ok(outsider.get('/api/candidates/'+identity))['contacts'] == {}
    ok(candidate.post('/api/contacts/revoke',json={}))
    assert ok(employer.get('/api/candidates/'+identity))['contacts'] == {}


def test_normal_ats_exchange_consent_stage_withdrawal_ack_and_rotate(normal):
    employer, candidate, _, outsider, _ = normal
    _, _, item = invited(normal)
    key = ok(employer.post('/api/ats-connections',json={'name':'Isolated API integration','scopes':['needs.write','profiles.read','stages.write','events.read']}),201)
    auth = {'Authorization':'Bearer '+key['token']}
    assert ok(outsider.get('/api/ats/v1/profiles',headers=auth))['items'] == []
    ok(candidate.post('/api/invitations/'+item['id']+'/respond',json={'action':'accepted','version':item['version']}))
    assert ok(outsider.get('/api/ats/v1/profiles',headers=auth))['items'] == []
    ok(candidate.post('/api/invitations/'+item['id']+'/ats-consent',json={'granted':True,'fields':['name']}))
    profile = ok(outsider.get('/api/ats/v1/profiles/'+item['id'],headers=auth))
    assert set(profile['profile']) == {'name'}
    stage = {'invitation_id':item['id'],'external_candidate_id':'isolated-17','stage':'Interview'}
    stage_headers = auth | {'Idempotency-Key':'normal-stage-request-0001'}
    first = ok(outsider.post('/api/ats/v1/stages',json=stage,headers=stage_headers))
    assert ok(outsider.post('/api/ats/v1/stages',json=stage,headers=stage_headers)) == first
    assert ok(candidate.get('/api/invitations/'+item['id']+'/recruitment-stage'))['stage'] == 'Interview'
    ok(candidate.post('/api/invitations/'+item['id']+'/ats-consent',json={'granted':False}))
    assert outsider.get('/api/ats/v1/profiles/'+item['id'],headers=auth).status_code == 404
    events = ok(outsider.get('/api/ats/v1/events',headers=auth))['items']
    withdrawal = next(x for x in events if x.get('withdrawal'))
    ack_path = '/api/ats/v1/events/'+withdrawal['id']+'/ack'
    assert outsider.post(ack_path,json={'deleted':False},headers=auth).status_code == 422
    ack = ok(outsider.post(ack_path,json={'deleted':True},headers=auth))
    assert ok(outsider.post(ack_path,json={'deleted':True},headers=auth)) == ack
    diagnostics = ok(employer.get('/api/ats-connections/'+key['id']+'/diagnostics'))
    assert diagnostics['awaiting_ack'] == 0 and key['token'] not in str(diagnostics)
    rotated = ok(employer.post('/api/ats-connections/'+key['id']+'/rotate',json={}))
    assert outsider.get('/api/ats/v1/events',headers=auth).status_code == 401
    assert outsider.get('/api/ats/v1/events',headers={'Authorization':'Bearer '+rotated['token']}).status_code == 200


def test_normal_complaint_duplicate_history_moderation_and_block(normal):
    employer, candidate, admin, outsider, people = normal
    need, _, _ = invited(normal)
    body = {'org_id':people['api.employer']['org_id'],'need_id':need['id'],
            'kind':'misleading_terms','message':'Условия приглашения требуют дополнительной проверки'}
    complaint = ok(candidate.post('/api/complaints',json=body),201)
    assert ok(candidate.post('/api/complaints',json=body),201)['id'] == complaint['id']
    assert outsider.get('/api/complaints').json()['items'] == []
    path = '/api/admin/complaints/'+complaint['id']+'/resolve'
    resolution = {'state':'dismissed','resolution':'Изолированный сценарий: условия проверены по тестовым материалам'}
    assert employer.post(path,json=resolution).status_code == 403
    ok(admin.post(path,json=resolution))
    assert admin.post(path,json=resolution).status_code == 409
    assert ok(candidate.get('/api/complaints'))['items'][0]['resolution'] == resolution['resolution']
    ok(candidate.post('/api/employer-blocks',json={'org_id':body['org_id'],'blocked':True}))
    blocked_profile = next((x for x in ok(employer.get('/api/candidates'))['items'] if x['id']==people['api.candidate']['candidate_id']), None)
    if blocked_profile:
        assert blocked_profile['contacts'] == {}
    search = ok(employer.post('/api/search-sessions',json={'need_id':need['id']}),201)
    assert not any(x['candidate']['id']==people['api.candidate']['candidate_id'] for x in search['items'])


def test_normal_vacancy_application_bookmark_archive(normal):
    employer, candidate, _, outsider, people = normal
    trusted(normal)
    body = {'title':'QA инженер API','description':'Проверка интерфейсов и интеграций продукта',
            'role':'qa','required_skills':['python'],'salary_min':100000,'salary_max':160000}
    vacancy = ok(employer.post('/api/vacancies',json=body),201)
    path = '/api/vacancies/'+vacancy['id']
    assert not any(x['id']==vacancy['id'] for x in ok(candidate.get('/api/vacancies'))['items'])
    published = ok(employer.post(path+'/publish',json={'version':vacancy['version']}))
    assert outsider.post(path+'/publish',json={'version':published['version']}).status_code == 404
    ok(candidate.post('/api/bookmarks',json={'entity_type':'vacancy','entity_id':vacancy['id'],'saved':True}))
    application = {'vacancy_id':vacancy['id'],'share_contacts':True,'message':'Рассмотрите профиль'}
    assert candidate.post('/api/applications',json=application | {'share_contacts':False}).status_code == 422
    applied = ok(candidate.post('/api/applications',json=application),201)
    assert ok(candidate.post('/api/applications',json=application),201)['id'] == applied['id']
    assert outsider.get('/api/applications').json()['items'] == []
    assert any(x['id']==applied['id'] for x in ok(employer.get('/api/applications'))['items'])
    ok(employer.post(path+'/archive',json={}))
    assert candidate.post('/api/applications',json=application).status_code == 409


def test_normal_regular_case_voluntary_feedback_no_grade(normal):
    employer, candidate, admin, outsider, _ = normal
    trusted(normal)
    case = ok(employer.post('/api/regular-cases',json={'title':'Проверка API контракта','specialization':'qa',
        'task':'Опишите отрицательные проверки объектной авторизации в API продукта.',
        'rubric':['Проверки прав доступа','Проверки повторного запроса']}),201)
    path = '/api/regular-cases/'+case['id']
    ok(admin.post('/api/admin/regular-cases/'+case['id']+'/review',json={'version':1,'approve':True,'rationale':'Изолированный технический пример для проверки API'}))
    ok(candidate.put(path+'/subscription',json={'enabled':True}))
    attempt = ok(candidate.post(path+'/attempts',json={'answer':'Проверю чужой объект и повторный запрос без полномочий','share_answer':True}),201)
    review = {'version':1,'passed':True,'rationale':'Изолированный разбор: указаны необходимые отрицательные проверки'}
    assert outsider.post('/api/regular-case-attempts/'+attempt['id']+'/review',json=review).status_code == 404
    ok(employer.post('/api/regular-case-attempts/'+attempt['id']+'/review',json=review))
    assert not ok(candidate.get('/api/assessments'))['certifications']


def test_normal_admin_block_reset_operator_and_signup(normal):
    employer, candidate, admin, outsider, people = normal
    identity = people['api.candidate']['id']
    def action(kind, **kwargs):
        detail = ok(admin.get('/api/admin/users/'+identity))
        return admin.post('/api/admin/users/'+identity+'/actions',json={
            'action':kind,'version':detail['account_version'],
            'reason':'Изолированная проверка полномочий','current_password':PASSWORD+'changed',**kwargs})
    ok(action('block'))
    assert candidate.get('/api/auth/me').status_code == 401
    assert candidate.post('/api/auth/login',json={'username':'api.candidate','password':PASSWORD}).status_code == 401
    ok(action('unblock'));ok(action('access',access='operator'))
    login = ok(candidate.post('/api/auth/login',json={'username':'api.candidate','password':PASSWORD}))
    candidate.headers['X-CSRF-Token'] = login['csrf']
    assert candidate.get('/api/admin/complaints').status_code == 200
    assert candidate.get('/api/admin/users').status_code == 403
    reset = ok(action('reset_password'))
    assert candidate.get('/api/auth/me').status_code == 401
    assert candidate.post('/api/auth/login',json={'username':'api.candidate','password':PASSWORD}).status_code == 401
    ok(outsider.post('/api/auth/recover',json={'username':'api.candidate','recovery_code':reset['recovery_code'],'password':PASSWORD+'restored'}))
    audit = admin.get('/api/admin/audit').text
    assert reset['recovery_code'] not in audit and PASSWORD not in audit
    ok(admin.put('/api/admin/settings',json={'registration_enabled':False,'current_password':PASSWORD+'changed'}))
    assert employer.get('/api/health').json()['registration_enabled'] is False
    assert employer.post('/api/auth/register',json={'username':'closed.signup','name':'Закрытая регистрация',
        'role':'candidate','password':PASSWORD,'processing_consent':True}).status_code == 403


def test_normal_resume_upload_approve_and_private_pdf(normal):
    employer, candidate, _, outsider, people = normal
    upload = ok(candidate.post('/api/profile/resumes',files={'file':('resume.txt',
        'Опыт работы: Python SQL; тестирование API и проверка контрактов'.encode(),'text/plain')}),202)
    assert outsider.get('/api/profile/resumes/'+upload['id']).status_code == 403
    drain(); doc = ok(candidate.get('/api/profile/resumes/'+upload['id']))
    assert doc['state'] == 'ready' and 'python' in doc['draft']['fields']['skills']
    assert not ok(candidate.get('/api/profile'))['skills']
    profile = ok(candidate.get('/api/profile'))
    body = {'name':'API специалист','title':'QA инженер','role':'qa','city':'',
        'skills':['python','sql'],'bio':'Навыки из резюме являются самоописанием',
        'visible':False,'work_modes':['remote'],'version':profile['version']}
    ok(candidate.post('/api/profile/resumes/'+doc['id']+'/approve',json={'version':doc['version'],'profile':body}))
    assert candidate.post('/api/profile/resumes/'+doc['id']+'/approve',json={'version':doc['version'],'profile':body}).status_code == 409
    assert not ok(candidate.get('/api/assessments'))['certifications']
    pdf = candidate.get('/api/profile/pdf')
    assert pdf.status_code == 200 and pdf.content.startswith(b'%PDF-')
    assert employer.get('/api/candidates/'+people['api.candidate']['candidate_id']+'/pdf').status_code == 404
    assert candidate.post('/api/profile/resumes',files={'file':('bad.exe',b'not allowed','application/octet-stream')}).status_code == 415


def test_normal_search_refinement_revocation_and_fsp_proof(normal):
    employer, candidate, admin, outsider, people = normal
    trusted(normal);ready(normal)
    need = ok(employer.post('/api/needs',json=NEED | {'specialization':'qa'}),201)
    search = ok(employer.post('/api/search-sessions',json={'need_id':need['id']}),201)
    assert any(x['candidate']['id']==people['api.candidate']['candidate_id'] for x in search['items'])
    assert outsider.get('/api/search-sessions/'+search['id']).status_code == 404
    refined = ok(employer.post('/api/search-sessions',json={'need_id':need['id'],
        'session_id':search['id'],'version':search['version'],'skills':['sql']}),201)
    assert len(ok(employer.get('/api/search-sessions/'+refined['id']))['payload']['history']) == 2
    ok(candidate.post('/api/consents',json={'kind':'publication','granted':False}))
    assert not ok(employer.get('/api/search-sessions/'+search['id']))['items']
    ok(candidate.post('/api/fsp/link',json={'participant_id':'ISOLATED-API-17'}),201)
    proof = ok(admin.post('/api/admin/fsp/proofs',json={'candidate_id':people['api.candidate']['candidate_id'],
        'participant_id':'ISOLATED-API-17','evidence':'Isolated synthetic ownership proof; no actual FSP registry'}),201)
    assert outsider.post('/api/fsp/confirm',json={'token':proof['proof']}).status_code == 403
    linked = ok(candidate.post('/api/fsp/confirm',json={'token':proof['proof']}))
    assert candidate.post('/api/fsp/confirm',json={'token':proof['proof']}).status_code == 400
    ok(candidate.post('/api/fsp/identities/'+linked['id']+'/revoke',json={}))


def test_username_company_membership_contract(normal):
    employer, _, _, outsider, _ = normal
    invitation = ok(employer.post('/api/company/members/invite',json={'username':'api.outsider','role':'recruiter'}),201)
    ok(outsider.post('/api/company/members/accept',json={'token':invitation['token']}))


def test_blocked_company_cannot_read_candidate_directory(normal):
    employer, candidate, _, _, people = normal
    invited(normal)
    ok(candidate.post('/api/employer-blocks',json={'org_id':people['api.employer']['org_id'],'blocked':True}))
    assert not any(x['id']==people['api.candidate']['candidate_id'] for x in ok(employer.get('/api/candidates'))['items'])
    assert employer.get('/api/candidates/'+people['api.candidate']['candidate_id']).status_code == 404


def test_anonymous_cannot_use_any_protected_api_operation(normal):
    public = {'/api/health','/api/taxonomy','/api/assessments/catalog',
              '/api/auth/demo','/api/auth/login','/api/auth/register',
              '/api/auth/recover','/api/auth/verify','/api/auth/resend',
              '/api/auth/reset-request','/api/auth/reset','/api/docs','/api/redoc','/api/openapi.json',
              '/api/integration-contracts'}
    visitor = TestClient(app)
    for route in app.routes:
        if not getattr(route,'path','').startswith('/api/') or route.path in public:
            continue
        path = route.path
        for parameter in getattr(route,'param_convertors',{}):
            path = path.replace('{'+parameter+'}', 'missing-audit-object')
        for method in (getattr(route,'methods',None) or set()) - {'HEAD','OPTIONS'}:
            response = visitor.request(method,path)
            assert response.status_code == 401, (method,route.path,response.status_code,response.text)


def test_practice_all_specializations_and_levels_through_api(normal):
    _, candidate, _, _, _ = normal
    from backend.assessment_catalog import SPECIALIZATIONS,GRADES
    matrix = []
    for spec in SPECIALIZATIONS:
        for grade in GRADES:
            started = ok(candidate.post('/api/assessments',json={
                'specialization':spec,'grade':grade,'mode':'practice'}),202)
            drain()
            state = ok(candidate.get('/api/assessments/'+started['id']))
            assert state['status']=='active' and state['can_answer']
            assert state['specialization']==spec and state['grade']==grade
            assert state['assembly_plan']['count']==9 and len(state['items'])==12
            coverage = state['coverage']
            # Some criteria occur in optional reflection, not objective screening.
            # The API must disclose this rather than claim measured competence.
            assert set(coverage['not_observed']) == set(coverage['blueprint_competencies']) - set(coverage['objective_observations'])
            matrix.append({'specialization':spec,'grade':grade,
                'objective_observations':coverage['objective_observations'],
                'not_observed':coverage['not_observed'],
                'open_answer_competencies':coverage['open_answer_competencies']})
            ok(candidate.post('/api/assessments/'+started['id']+'/cancel',json={'version':state['version']}))
    if os.environ.get('FSP_AUDIT_MATRIX'):
        Path(os.environ['FSP_AUDIT_MATRIX']).write_text(json.dumps(matrix,ensure_ascii=False,indent=2),encoding='utf-8')


def test_external_signed_registry_callback_without_browser_session(normal, monkeypatch):
    import time
    from backend.delivery_gateway import signature
    _, candidate, admin, _, people = normal
    ok(candidate.post('/api/fsp/link',json={'participant_id':'ISOLATED-WEBHOOK-17'}),201)
    proof = ok(admin.post('/api/admin/fsp/proofs',json={'candidate_id':people['api.candidate']['candidate_id'],
        'participant_id':'ISOLATED-WEBHOOK-17','evidence':'Synthetic fixture; no actual FSP participant'}),201)
    linked = ok(candidate.post('/api/fsp/confirm',json={'token':proof['proof']}))
    key = 'isolated-webhook-authentication-key-2026'
    monkeypatch.setenv('FSP_REGISTRY_API_KEY',key)
    monkeypatch.setenv('FSP_REGISTRY_ISSUER',linked['issuer'])
    event = {'event_id':'isolated-webhook-event-001','issuer':linked['issuer'],
        'participant_id':'ISOLATED-WEBHOOK-17','achievement':{
            'external_id':'isolated-webhook-result-001','title':'Synthetic result',
            'contest':'Local contract fixture','date':'2026-10-10','result':'Technical fixture',
            'individual_evidence':True,'task_skills':['python'],
            'source_url':'https://isolated.example.org/registry/result'}}
    raw = json.dumps(event,separators=(',',':')).encode()
    stamp = str(int(time.time()))
    headers = {'Content-Type':'application/json','X-FSP-Timestamp':stamp,
               'X-FSP-Signature':signature(key,stamp,raw)}
    # Control: same valid signature/body is accepted with a browser session.
    ok(candidate.post('/api/integration-events/fsp-achievements',content=raw,headers=headers))
    external = TestClient(app)
    # A genuine service caller has the signature, not the candidate's cookies.
    ok(external.post('/api/integration-events/fsp-achievements',content=raw,headers=headers))
