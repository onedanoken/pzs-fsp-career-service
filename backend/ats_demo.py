"""Reviewer-owned synthetic ATS sandbox. Business actions call the real API."""
import hashlib,hmac,json,secrets,time,os
from typing import Literal
import httpx
from fastapi import APIRouter,Depends,HTTPException,Request,Response
from pydantic import Field
from . import db
from .auth import session_user,create_session,COOKIE
from .db import connection,dumps,now,password_hash
from .schemas import StrictModel
from .ats_gateway import key_auth,scoped,accepted_profile
from .ats_diagnostics import ObservedATSRoute

router=APIRouter(prefix='/api/demo/ats',tags=['Synthetic ATS demonstration'])
mock_router=APIRouter(prefix='/api/ats/v1/demo-mock',tags=['Synthetic external ATS substitute'],route_class=ObservedATSRoute)
RUN_COOKIE='fsp_ats_demo';TTL=4*3600
STAGES={'interview':'Собеседование','offer':'Предложение работы','hired':'Найм завершён'}

def enabled():
    if not db.DEMO:raise HTTPException(404,'Демонстрация недоступна')

def digest(value):return hashlib.sha256(value.encode()).hexdigest()
def machine_key(raw):return hmac.new(raw.encode(),b'FSP-ATS-demo-client-v1',hashlib.sha256).hexdigest()
def read_state(conn,identity,allow_expired=False):
    row=conn.execute('SELECT value FROM meta WHERE key=?',('ats_demo:'+identity,)).fetchone()
    if not row:raise HTTPException(404,'Демонстрация не найдена')
    state=json.loads(row[0])
    if state['expires']<time.time() and not allow_expired:raise HTTPException(410,'Демонстрация завершилась. Начните новый сценарий.')
    return state

def write_state(conn,state):conn.execute('UPDATE meta SET value=? WHERE key=?',(dumps(state),'ats_demo:'+state['id']))

def browser_state(request,user,allow_expired=False):
    enabled();raw=request.cookies.get(RUN_COOKIE)
    if not raw:raise HTTPException(404,'Начните демонстрацию')
    with connection() as conn:state=read_state(conn,digest(raw),allow_expired)
    if user['id'] not in {state['original_user'],state['employer_user'],state['candidate_user']}:raise HTTPException(403,'Этот сценарий принадлежит другой сессии')
    return raw,state

def synthetic_user(conn,user):
    if user['candidate_id']:
        row=conn.execute('SELECT payload FROM candidates WHERE id=?',(user['candidate_id'],)).fetchone()
        return bool(row and json.loads(row[0]).get('demo'))
    if user['org_id']:
        row=conn.execute('SELECT payload FROM company_profiles WHERE org_id=?',(user['org_id'],)).fetchone()
        return bool(row and json.loads(row[0]).get('demo'))
    return user['role']=='admin' and user['email'].endswith('.demo')

def public_state(state,user):
    with connection() as conn:
        need=conn.execute('SELECT state,payload FROM employer_needs WHERE id=?',(state.get('need_id',''),)).fetchone()
        invitation=conn.execute('SELECT status,version,payload FROM invitations WHERE id=?',(state.get('invitation_id',''),)).fetchone()
        stage=conn.execute('SELECT stage FROM recruitment_stages WHERE invitation_id=?',(state.get('invitation_id',''),)).fetchone()
        pending=conn.execute("SELECT COUNT(*) AS count FROM integration_events WHERE connection_id=? AND status='awaiting_ack'",(state['connection_id'],)).fetchone()['count']
    payload=json.loads(invitation['payload']) if invitation else {}
    return {'id':state['id'],'role':'employer' if user['id']==state['employer_user'] else 'candidate' if user['id']==state['candidate_user'] else 'observer','need':{'title':json.loads(need['payload'])['title'],'state':need['state']} if need else None,'invitation_status':invitation['status'] if invitation else None,'ats_consent':bool(payload.get('ats_consent',{}).get('granted')),'ats_stage':stage['stage'] if stage else None,'record':state.get('record'),'last_exchange':state.get('last_exchange'),'last_error':state.get('last_error'),'attempts':state.get('attempts',0),'awaiting_ack':pending,'failure_seen':state.get('failure_seen',False),'removed':state.get('removed',False),'busy':state.get('busy_until',0)>time.time(),'expires':state['expires'],'notice':'Изолированная симуляция внешней ATS. Компания, кандидат и результат проверки синтетические. Правила согласия и обмена выполняет рабочий backend.'}

@router.get('')
def current(request:Request,user=Depends(session_user)):
    enabled()
    if not request.cookies.get(RUN_COOKIE):return {'state':None}
    _,state=browser_state(request,user);return {'state':public_state(state,user)}

class Start(StrictModel):reset:bool=False
@router.post('/start')
def start(body:Start,request:Request,response:Response,user=Depends(session_user)):
    enabled()
    # Reuse an owned scenario; "reset" retires only this sandbox's synthetic records.
    old=None
    if request.cookies.get(RUN_COOKIE):
        try:_,old=browser_state(request,user,True)
        except HTTPException:pass
    if old and old['expires']>time.time() and not body.reset:
        with connection() as conn:actor=dict(conn.execute('SELECT * FROM users WHERE id=?',(old['employer_user'],)).fetchone())
        result=create_session(response,dict(actor,org_role='owner'));return {'user':result,'state':public_state(old,actor)}
    with connection() as conn:
        if not synthetic_user(conn,user):raise HTTPException(403,'Демонстрация доступна только тестовым аккаунтам')
        conn.execute('BEGIN IMMEDIATE')
        original=old['original_user'] if old else user['id']
        if old:
            if old.get('busy_until',0)>time.time():raise HTTPException(409,'Дождитесь окончания текущего действия')
            conn.execute('UPDATE candidates SET visible=0 WHERE id=?',(old['candidate_id'],))
            conn.execute("UPDATE employer_needs SET state='closed' WHERE org_id=?",(old['org_id'],))
            row=conn.execute('SELECT payload FROM ats_connections WHERE id=?',(old['connection_id'],)).fetchone();settings=json.loads(row[0]);settings['active']=False;conn.execute('UPDATE ats_connections SET payload=? WHERE id=?',(dumps(settings),old['connection_id']))
            old.update(expires=0,record=None);write_state(conn,old)
        raw=secrets.token_urlsafe(32);identity=digest(raw);suffix=secrets.token_hex(10)
        org='demoats_org_'+suffix;candidate='demoats_candidate_'+suffix;employer='demoats_employer_'+suffix;person='demoats_person_'+suffix;client='demoats_client_'+suffix
        conn.execute('INSERT INTO organizations VALUES(?,?)',(org,'Демокомпания ATS'))
        company={'demo':True,'description':'Изолированная синтетическая компания','team':'Тестовая команда API','industry':'IT','domain':'example.invalid','contact':'ats-demo@example.invalid','sandbox':identity}
        conn.execute("INSERT INTO company_profiles(org_id,payload,verification) VALUES(?,?,'demo_verified')",(org,dumps(company)))
        profile={'id':candidate,'name':'Тестовый кандидат ATS','initials':'ТА','role':'backend','title':'Backend разработчик','city':'Екатеринбург','experience_years':2,'salary_min':110000,'skills':['python','sql'],'soft_skills':[],'career_history':[],'bio':'Синтетический профиль для проверки обмена.','demo':True,'industry':'IT','contacts':{'email':'candidate-ats@example.invalid','phone':'','telegram':''},'work_modes':['remote'],'looking':True,'relocate':False,'assessment_onboarding_done':True,'sandbox_org_id':org}
        conn.execute('INSERT INTO candidates(id,payload,visible) VALUES(?,?,1)',(candidate,dumps(profile)))
        for actor,name,role,cand,organization in [(employer,'Демоработодатель ATS','employer',None,org),(person,'Тестовый кандидат ATS','candidate',candidate,None)]:
            conn.execute('INSERT INTO users(id,email,name,password,role,candidate_id,org_id,email_verified) VALUES(?,?,?,?,?,?,?,1)',(actor,actor+'@example.demo',name,password_hash(secrets.token_urlsafe(32)),role,cand,organization))
        conn.execute("INSERT INTO organization_members VALUES(?,?,'owner',?)",(employer,org,now()))
        for kind in ['processing','publication']:conn.execute('INSERT INTO consent_events VALUES(?,?,?,?,?,?)',('demoats_'+kind+'_'+suffix,candidate,kind,1,'synthetic-demo',now()))
        conn.execute('INSERT INTO grade_certifications VALUES(?,?,?,?,?,NULL,?,?,?,1)',('demoats_cert_'+suffix,candidate,'backend','Middle',82,'demo',dumps({'demo':True,'calibration':'synthetic_fixture','tested_skills':[],'notice':'Тестовый результат, не человеческая аттестация'}),now()))
        state={'id':identity,'original_user':original,'org_id':org,'candidate_id':candidate,'employer_user':employer,'candidate_user':person,'connection_id':client,'expires':time.time()+TTL,'created_at':now(),'record':None,'attempts':0}
        settings={'name':'ATS-эмулятор · тестовые данные','owner_id':employer,'scopes':['needs.write','profiles.read','stages.write','events.read'],'token_hash':digest(machine_key(raw)),'active':True,'expires':state['expires'],'contract_version':'ats-v1','demo_run':identity}
        conn.execute("INSERT INTO ats_connections VALUES(?,?,'universal-v1',?,'',?,?)",(client,org,client,dumps(settings),now()))
        conn.execute('INSERT INTO meta VALUES(?,?)',('ats_demo:'+identity,dumps(state)))
        actor=dict(conn.execute('SELECT * FROM users WHERE id=?',(employer,)).fetchone())
    response.set_cookie(RUN_COOKIE,raw,max_age=TTL,httponly=True,samesite='lax',secure=os.environ.get('FSP_SECURE_COOKIE','0')=='1',path='/')
    return {'user':create_session(response,dict(actor,org_role='owner')),'state':public_state(state,actor)}

class Role(StrictModel):role:Literal['employer','candidate','original']
@router.post('/role')
def role(body:Role,request:Request,response:Response,user=Depends(session_user)):
    _,state=browser_state(request,user,body.role=='original')
    identity=state['employer_user'] if body.role=='employer' else state['candidate_user'] if body.role=='candidate' else state['original_user']
    with connection() as conn:actor=dict(conn.execute('SELECT * FROM users WHERE id=?',(identity,)).fetchone())
    return {'user':create_session(response,dict(actor,org_role='owner' if actor['role']=='employer' else None)),'state':public_state(state,actor)}

def mock_state(client,conn):
    enabled();scoped(client,'profiles.read');identity=client['payload'].get('demo_run')
    if not identity:raise HTTPException(403,'Это не подключение демомока')
    state=read_state(conn,identity)
    if state['connection_id']!=client['id'] or state['org_id']!=client['org_id']:raise HTTPException(403,'Чужой сценарий')
    return state

class Record(StrictModel):
    invitation_id:str;need_id:str;candidate_id:str;profile:dict;consent:dict;schema_version:str

@mock_router.post('/records')
def receive(body:Record,client=Depends(key_auth)):
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');state=mock_state(client,conn)
        if body.invitation_id!=state.get('invitation_id'):raise HTTPException(404,'Приглашение не принадлежит сценарию')
        actual=accepted_profile(body.invitation_id,client)
        if body.model_dump()!=actual:raise HTTPException(409,'Разрешённый профиль изменился')
        failure=state.pop('fail_once',False)
        if failure:state['failure_seen']=True
        if not failure:
            old=state.get('record') or {};state['record']={'id':'mock_'+state['id'][:16],'profile':body.profile,'stage':old.get('stage','Получен профиль'),'version':old.get('version',0)+1,'received_at':now()};state['removed']=False
        write_state(conn,state)
    if failure:raise HTTPException(503,'Демонстрационный временный сбой внешней ATS')
    return {'id':state['record']['id'],'received':True,'simulation':True}

@mock_router.delete('/records/{identity}')
def remove(identity:str,client=Depends(key_auth)):
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');state=mock_state(client,conn)
        if identity!=state.get('invitation_id'):raise HTTPException(404,'Запись не принадлежит сценарию')
        existed=bool(state.get('record'));state['record']=None;state['removed']=True;write_state(conn,state)
    return {'deleted':True,'existed':existed,'simulation':True}

class Action(StrictModel):
    action:Literal['import','invite','accept','consent','withdraw','sync','fail','stage','close']
    stage:Literal['interview','offer','hired']='interview'

@router.post('/action')
async def action(body:Action,request:Request,user=Depends(session_user)):
    raw,state=browser_state(request,user)
    expected=state['candidate_user'] if body.action in {'accept','consent','withdraw'} else state['employer_user']
    if user['id']!=expected:raise HTTPException(403,'Переключитесь на нужную роль в демонстрации')
    lock=secrets.token_hex(12)
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');state=read_state(conn,state['id'])
        if state.get('busy_until',0)>time.time():raise HTTPException(409,'Предыдущее действие ещё выполняется')
        state.update(busy_id=lock,busy_until=time.time()+30);write_state(conn,state)
    transport=httpx.ASGITransport(app=request.app)
    machine={'Authorization':'Bearer '+machine_key(raw)}
    browser={'Cookie':COOKIE+'='+request.cookies[COOKIE],'X-CSRF-Token':user['csrf']}
    async with httpx.AsyncClient(transport=transport,base_url='http://ats-demo-internal',timeout=20,follow_redirects=False) as api:
        async def call(method,path,data=None,headers=None,key=None):
            current_headers=dict(headers or machine)
            if key:current_headers['Idempotency-Key']=key
            response=await api.request(method,'/api'+path,json=data,headers=current_headers)
            if response.status_code>=400:raise HTTPException(response.status_code,response.json().get('detail','Действие не выполнено'))
            return response.json()
        try:
            if body.action=='import':
                need={'title':'Тестовая позиция Backend API','description':'Разработка надёжных API и обмена данными в тестовой команде.','team':'Синтетическая продуктовая команда','specialization':'backend','grade':'Middle','skills':['python','sql'],'salary_min':120000,'salary_max':180000,'employer_contact':'ats-demo@example.invalid'}
                result=await call('POST','/ats/v1/needs',{'external_id':'DEMO-'+state['id'][:20],'need':need},key='demo-import-'+state['id']);state['need_id']=result['id']
            elif body.action=='invite':
                if not state.get('need_id'):raise HTTPException(409,'Сначала импортируйте тестовую позицию')
                result=await call('POST','/invitations',{'need_id':state['need_id'],'candidate_id':state['candidate_id'],'message':'Синтетическое приглашение для проверки обмена с ATS.','next_step':'Тестовая встреча с командой после принятия приглашения.','response_days':2,'idempotency_key':'demo-invite-'+state['id']},headers=browser);state['invitation_id']=result['id']
            elif body.action in {'accept','consent','withdraw'}:
                identity=state.get('invitation_id')
                if not identity:raise HTTPException(409,'Сначала работодатель отправляет приглашение')
                if body.action=='accept':
                    items=(await call('GET','/invitations',headers=browser))['items'];invitation=next(row for row in items if row['id']==identity)
                    if invitation['status']!='accepted':await call('POST','/invitations/'+identity+'/respond',{'action':'accepted','version':invitation['version']},headers=browser)
                else:await call('POST','/invitations/'+identity+'/ats-consent',{'granted':body.action=='consent','fields':['name','contacts','categories']},headers=browser)
            elif body.action=='stage':
                with connection() as conn:
                    fresh=read_state(conn,state['id']);record=fresh.get('record')
                if not record:raise HTTPException(409,'Сначала передайте разрешённый профиль в ATS')
                value=STAGES[body.stage]
                await call('POST','/ats/v1/stages',{'invitation_id':state['invitation_id'],'external_candidate_id':record['id'],'stage':value},key='demo-stage-'+secrets.token_hex(16))
                record={**record,'stage':value};state['record']=record
            elif body.action=='close':
                if not state.get('need_id'):raise HTTPException(409,'Позиция ещё не импортирована')
                await call('DELETE','/ats/v1/needs/DEMO-'+state['id'][:20],key='demo-close-'+state['id'])
            elif body.action in {'sync','fail'}:
                if body.action=='fail':
                    with connection() as conn:
                        fresh=read_state(conn,state['id']);fresh['fail_once']=True;write_state(conn,fresh)
                state['attempts']=state.get('attempts',0)+1
                run=secrets.token_hex(16);counts={'profiles':0,'deleted':0}
                await call('POST','/ats/v1/sync-report',{'run_id':run,'provider':'demo_mock','status':'running'})
                try:
                    events=(await call('GET','/ats/v1/events'))['items']
                    for event in events:
                        if event.get('withdrawal') and event['status']=='awaiting_ack':
                            await call('DELETE','/ats/v1/demo-mock/records/'+event['withdrawal']['invitation_id'])
                            await call('POST','/ats/v1/events/'+event['id']+'/ack',{'deleted':True,'note':'Демомок удалил свою синтетическую копию'});counts['deleted']+=1
                    profiles=(await call('GET','/ats/v1/profiles'))['items']
                    for profile in profiles:
                        await call('POST','/ats/v1/demo-mock/records',profile);counts['profiles']+=1
                    await call('POST','/ats/v1/sync-report',{'run_id':run,'provider':'demo_mock','status':'completed','counts':counts})
                    state['last_exchange']={'status':'completed','counts':counts,'at':now()};state['last_error']=None
                except HTTPException as error:
                    await call('POST','/ats/v1/sync-report',{'run_id':run,'provider':'demo_mock','status':'failed','counts':counts,'error_code':'remote_error'})
                    state['last_exchange']={'status':'failed','at':now()};state['last_error']='Мок ATS временно ответил 503. Повторите обмен: данные и разрешения сохранены.'
                    if error.status_code!=503:raise
        except HTTPException:raise
        finally:
            with connection() as conn:
                conn.execute('BEGIN IMMEDIATE');fresh=read_state(conn,state['id'])
                if fresh.get('busy_id')==lock:
                    # The mock endpoint owns its record; merge its committed changes.
                    record=state['record'] if body.action=='stage' else fresh.get('record')
                    state.update(record=record,busy_until=0,failure_seen=fresh.get('failure_seen',False),removed=fresh.get('removed',False));state.pop('busy_id',None);state.pop('fail_once',None);write_state(conn,state)
    return {'state':public_state(state,user)}
