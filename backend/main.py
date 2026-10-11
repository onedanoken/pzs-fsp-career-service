import hmac
import json
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, Request, Response, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from . import db
from .auth import session_user, public_user, create_session, check_password, require_role, require_verified, COOKIE
from .db import connection,dumps,now,audit,bump,password_hash,ROOT,DEMO
from .seed import seed
from .domain import get_candidate,list_candidates,get_vacancy,list_vacancies,achievements_for,ensure_index,update_index,queue_warmup
from .taxonomy import SKILLS,ROLES,extract_skills
from .matching import rank_candidates,rank_vacancies,assessment
from .ml import engine
from .schemas import *
from .jobs import Worker,enqueue
from .services import router as services_router
from . import passports,telemetry

@asynccontextmanager
async def lifespan(app):
    db.initialize()
    seed()
    from .demo_stage03 import seed_domain
    seed_domain()
    ensure_index()
    def prepare():
        engine.prepare()
        if engine.state=='ready':queue_warmup()
    thread=threading.Thread(target=prepare,daemon=True)
    thread.start()
    worker=Worker()
    if os.environ.get('FSP_JOB_AUTOSTART','1')=='1':worker.start()
    try:yield
    finally:worker.stop()

app=FastAPI(title="ФСП Карьера",version="0.16.0",description="Подбор по результатам компетенций, независимые приглашения и универсальный ATS API. Шкала грейдов пилотная.",
            docs_url=None,redoc_url=None,openapi_url="/api/openapi.json",lifespan=lifespan)
allowed_origins=set(os.environ.get("FSP_ALLOWED_ORIGINS","http://localhost:8000,http://127.0.0.1:8000,http://localhost:5173,http://127.0.0.1:5173").split(","))
auth_routes={"/api/auth/login","/api/auth/register","/api/auth/demo","/api/auth/verify","/api/auth/reset-request","/api/auth/reset","/api/auth/recover"}
login_attempts={}
signed_event_routes={'/api/integration-events/fsp-achievements','/api/integration-events/notifications'}
_dummy_password_hash=password_hash(secrets.token_urlsafe(32))
app.include_router(services_router)
from .identity import router as identity_router
app.include_router(identity_router)
from .delivery_gateway import router as delivery_router
app.include_router(delivery_router)
from .assessments import router as assessments_router
app.include_router(assessments_router)
from .skill_passports import router as skill_passports_router
app.include_router(skill_passports_router)
from .recruiting import router as recruiting_router
from .trust import router as trust_router
app.include_router(recruiting_router)
app.include_router(trust_router)
from .ats_gateway import router as ats_router
app.include_router(ats_router)
from .ats_diagnostics import router as ats_diagnostics_router
app.include_router(ats_diagnostics_router)
from .ats_demo import router as ats_demo_router,mock_router as ats_demo_mock_router
if DEMO and os.environ.get('FSP_TEST_LOGIN')=='1':
    app.include_router(ats_demo_router)
    app.include_router(ats_demo_mock_router)
from .fsp_identity import router as fsp_identity_router
app.include_router(fsp_identity_router)
from .profile_pdf import router as pdf_router
app.include_router(pdf_router)
from .product import router as product_router
app.include_router(product_router)
from .regular_cases import router as cases_router
app.include_router(cases_router)
from .live_vacancies import router as live_router
app.include_router(live_router)
from .pilots import router as pilot_router
app.include_router(pilot_router)
from .career import router as career_router
app.include_router(career_router)
from .accounts import router as accounts_router,registration_enabled
app.include_router(accounts_router)

@app.middleware("http")
async def security(request,call_next):
    if request.method not in {"GET","HEAD","OPTIONS"} and request.url.path.startswith("/api/"):
        origin=request.headers.get("origin")
        if origin and origin not in allowed_origins:
            return JSONResponse({"detail":"Недопустимый источник запроса"},status_code=403)
        if request.url.path not in auth_routes and request.url.path not in signed_event_routes and not request.url.path.startswith('/api/ats/v1/'):
            try:
                user=session_user(request)
            except HTTPException as exc:
                return JSONResponse({"detail":exc.detail},status_code=exc.status_code)
            if not hmac.compare_digest(request.headers.get("X-CSRF-Token",""),user["csrf"]):
                return JSONResponse({"detail":"Обновите страницу и повторите действие"},status_code=403)
    if request.url.path=='/api/profile/resumes':
        length=request.headers.get('content-length')
        if length and (not length.isdigit() or int(length)>6*1024*1024):
            return JSONResponse({'detail':'Слишком большой запрос загрузки'},status_code=413)
    started=time.perf_counter()
    response=await call_next(request)
    route=request.scope.get('route')
    telemetry.record(getattr(route,'path','unmatched'),request.method,response.status_code,(time.perf_counter()-started)*1000)
    response.headers["X-Content-Type-Options"]="nosniff"
    response.headers["X-Frame-Options"]="DENY"
    response.headers["Referrer-Policy"]="same-origin"
    response.headers["Content-Security-Policy"]="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"]="no-store"
    return response

@app.exception_handler(RequestValidationError)
async def invalid(request,exc):
    return JSONResponse({"detail":"; ".join(str(e["loc"][-1])+": "+e["msg"] for e in exc.errors())},status_code=422)

def rate_limit(request):
    ip=request.client.host if request.client else "local"
    timestamp=time.monotonic()
    attempts=[t for t in login_attempts.get(ip,[]) if timestamp-t<60]
    if len(attempts)>=60:
        raise HTTPException(429,"Слишком много попыток. Повторите через минуту.")
    login_attempts[ip]=attempts+[timestamp]

@app.get("/api/health")
def health():
    return {"status":"ok","demo":DEMO,"registration_enabled":registration_enabled(),"login_method":"username_or_email" if os.environ.get('FSP_EMAIL_REQUIRED','1')=='1' else 'username',"model":engine.status()}

@app.get("/api/taxonomy")
def taxonomy():
    return {"skills":[{"id":k,"label":v[0]} for k,v in SKILLS.items()],
            "roles":[{"id":k,"label":v} for k,v in ROLES.items()],"version":"it-russian-v1"}

@app.post("/api/auth/demo",include_in_schema=False)
def demo_login(body:DemoLogin,request:Request,response:Response):
    if not DEMO or os.environ.get("FSP_TEST_LOGIN")!="1":
        raise HTTPException(404,"Вход недоступен")
    rate_limit(request)
    with connection() as conn:
        user=dict(conn.execute("SELECT * FROM users WHERE id=?",(body.account,)).fetchone())
    return create_session(response,user)

@app.post("/api/auth/login")
def login(body:Login,request:Request,response:Response):
    rate_limit(request)
    with connection() as conn:
        if body.username:
            row=conn.execute("SELECT * FROM users WHERE username=?",(body.username.lower(),)).fetchone()
        elif body.email:
            row=conn.execute("SELECT * FROM users WHERE email=? AND (auth_kind='email' OR ?=1)",(body.email.lower(),os.environ.get('FSP_LEGACY_EMAIL_AUTH')=='1')).fetchone()
        else:raise HTTPException(401,"Неверный логин или пароль")
    from .auth import account_enabled
    valid_password=check_password(body.password,row["password"] if row else _dummy_password_hash)
    if not row or not account_enabled(dict(row)) or not valid_password:
        raise HTTPException(401,"Неверный логин или пароль")
    with connection() as conn:conn.execute('UPDATE users SET last_login_at=? WHERE id=?',(now(),row['id']))
    return create_session(response,dict(row))

@app.post("/api/auth/register",status_code=201)
def register(body:Registration,request:Request,response:Response):
    if not registration_enabled():
        raise HTTPException(403,'Регистрация временно закрыта')
    rate_limit(request)
    email_required=os.environ.get('FSP_EMAIL_REQUIRED','1')=='1'
    if email_required and len(body.password)<12:raise HTTPException(422,'Пароль должен содержать не менее 12 символов')
    if email_required and not body.email:raise HTTPException(422,'Укажите email для подтверждения аккаунта')
    if not body.username and (not body.email or not email_required and os.environ.get('FSP_LEGACY_EMAIL_AUTH')!='1'):
        raise HTTPException(422,'Укажите логин: 3–40 латинских букв, цифр, точек, дефисов или подчёркиваний')
    if body.username and (len(body.password)<12 or body.username.lower() in {'admin','root','system','support','moderator'}):
        raise HTTPException(422,'Выберите другой логин; пароль должен содержать не менее 12 символов')
    if not body.processing_consent:
        raise HTTPException(422,'Требуется отдельное согласие на обработку персональных данных')
    id="u_"+secrets.token_hex(8)
    candidate_id="c_"+secrets.token_hex(8) if body.role=="candidate" else None
    org_id="org_"+secrets.token_hex(8) if body.role=="employer" else None
    username=body.username.lower() if body.username else id
    email=body.email.lower() if body.email else id+"@accounts.invalid"
    recovery_code=None
    try:
        with connection() as conn:
            if candidate_id:
                c={"id":candidate_id,"name":body.name,"initials":"".join(part[0] for part in body.name.split()[:2]),
                   "title":"ИТ специалист","role":"backend","city":"","experience_years":None,"salary_min":None,
                   "currency":"RUB","work_modes":["remote"],"skills":[],"bio":"","color":"#a88bf0",
                   "looking":True,"demo":False,"contacts":{"email":email} if body.email else {},"industry":"IT"}
                conn.execute("INSERT INTO candidates(id,payload,visible) VALUES(?,?,0)",(candidate_id,dumps(c)))
            if org_id:
                conn.execute("INSERT INTO organizations VALUES(?,?)",(org_id,body.company))
            user={"id":id,"email":email,"name":body.name,"password":password_hash(body.password),
                  "role":body.role,"candidate_id":candidate_id,"org_id":org_id}
            conn.execute("INSERT INTO users(id,email,name,password,role,candidate_id,org_id) VALUES(?,?,?,?,?,?,?)",tuple(user.values()))
            if org_id:
                conn.execute("INSERT INTO organization_members VALUES(?,?,'owner',?)",(id,org_id,now()))
            if candidate_id:
                conn.execute('INSERT INTO consent_events VALUES(?,?,?,?,?,?)',('cons_'+secrets.token_hex(10),candidate_id,'processing',1,'2026-10-v1',now()))
            from .identity import issue_token
            conn.execute('UPDATE users SET username=?,auth_kind=?,created_at=? WHERE id=?',(username,'email' if body.email and email_required else 'username' if body.username else 'legacy',now(),id))
            user.update(username=username,auth_kind='email' if body.email and email_required else 'username' if body.username else 'legacy')
            if body.username and not (body.email and email_required):
                from .accounts import recovery_token
                recovery_code=recovery_token(conn,id)
            else:issue_token(conn,user,'verify_email')
            audit(conn,id,"registered",id,{"role":body.role})
            bump(conn)
            if candidate_id:update_index('candidate',candidate_id,conn)
    except db.IntegrityError:
        raise HTTPException(409,"Этот логин или email уже используется")
    result=create_session(response,user)
    if recovery_code:result["recovery_code"]=recovery_code
    return result

@app.get("/api/auth/me")
def me(user=Depends(session_user)):
    return public_user(user)

@app.post("/api/auth/logout")
def logout(request:Request,response:Response,user=Depends(session_user)):
    import hashlib
    with connection() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=?",(hashlib.sha256(request.cookies[COOKIE].encode()).hexdigest(),))
    response.delete_cookie(COOKIE,path="/")
    return {"ok":True}

@app.get("/api/system")
def system(user=Depends(session_user)):
    report_path=ROOT/"artifacts"/"evaluation.json"
    report=json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else None
    if report:
        report["stale"]=report.get("data_revision")!=db.revision()
    with connection() as conn:
        counts={
            "candidates":conn.execute("SELECT COUNT(*) FROM candidates WHERE visible=1").fetchone()[0],
            "achievements":conn.execute("SELECT COUNT(*) FROM achievements a JOIN candidates c ON c.id=a.candidate_id WHERE c.visible=1 AND a.active=1").fetchone()[0],
            "vacancies":conn.execute("SELECT COUNT(*) FROM vacancies WHERE archived=0").fetchone()[0],
            "real_vacancies":conn.execute("SELECT COUNT(*) FROM vacancies WHERE archived=0 AND json_extract(payload,'$.demo')=0").fetchone()[0],
        }
    return {"demo":DEMO,"counts":counts,"model":engine.status(),"evaluation":report,
            "fsp":{"mode":"mock","label":"Тестовый адаптер ФСП","real_api_configured":False},
            "data_revision":db.revision()}

@app.get("/api/candidates")
def candidates(vacancy_id:str|None=None,role:str|None=None,q:str=Query("",max_length=120),
               evidence_only:bool=False,saved_only:bool=False,mode:str=Query("hybrid",pattern="^(hybrid|baseline|semantic)$"),
               user=Depends(session_user)):
    require_role(user,"employer","admin")
    require_verified(user)
    from .privacy import eligible,public_profile
    with connection() as conn:
        items=[public_profile(c,user,conn) for c in list_candidates() if eligible(conn,c,user.get('org_id'))]
    if role:
        items=[c for c in items if role in [r["id"] for r in c["categories"]]]
    if q:
        terms=q.lower().split()
        items=[c for c in items if all(t in (c["name"]+" "+c["title"]+" "+c["city"]+" "+" ".join(c["skill_labels"].values())).lower() for t in terms)]
    if evidence_only:
        items=[c for c in items if c["evidence_count"]>0]
    with connection() as conn:
        saved={r[0] for r in conn.execute("SELECT entity_id FROM bookmarks WHERE user_id=? AND entity_type='candidate'",(user["id"],))}
    if saved_only:
        items=[c for c in items if c["id"] in saved]
    meta=None
    if vacancy_id:
        v=get_vacancy(vacancy_id,user)
        if v["archived"]:
            raise HTTPException(409,"Вакансия закрыта")
        items,meta=rank_candidates(v,items,mode)
    for c in items:
        c["saved"]=c["id"] in saved
    return {"items":items,"meta":meta}

@app.get("/api/candidates/{id}")
def candidate(id:str,user=Depends(session_user)):
    if user["role"]=="candidate" and user["candidate_id"]!=id:
        raise HTTPException(403,"Недостаточно прав")
    return get_candidate(id,user)

@app.get("/api/profile")
def profile(user=Depends(session_user)):
    require_role(user,"candidate")
    return get_candidate(user["candidate_id"],user)

@app.put("/api/profile")
def update_profile(body:ProfileUpdate,user=Depends(session_user)):
    require_role(user,"candidate")
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row=conn.execute("SELECT * FROM candidates WHERE id=?",(user["candidate_id"],)).fetchone()
        if row["version"]!=body.version:
            raise HTTPException(409,"Профиль изменился. Обновите страницу.")
        current=json.loads(row["payload"])
        if current.get('sandbox_org_id'):raise HTTPException(403,'Профиль ATS-демонстрации задан тестовым сценарием. Начните новый сценарий для сброса.')
        current.update(body.model_dump(exclude={"version","visible"}))
        if current.get('resume_provenance'):
            current['resume_provenance']['skills']={key:value for key,value in current['resume_provenance']['skills'].items() if key in body.skills}
        current["initials"]="".join(part[0] for part in body.name.split()[:2])
        conn.execute("UPDATE candidates SET payload=?,visible=?,version=version+1 WHERE id=?",(dumps(current),body.visible,user["candidate_id"]))
        conn.execute("UPDATE users SET name=? WHERE id=?",(body.name,user["id"]))
        bump(conn)
        audit(conn,user["id"],"profile_updated",user["candidate_id"],{"visible":body.visible})
        update_index('candidate',user['candidate_id'],conn)
    return get_candidate(user["candidate_id"],user)

@app.post("/api/profile/fsp-sync")
def sync_profile(user=Depends(session_user)):
    require_role(user,"candidate")
    if not DEMO:
        raise HTTPException(409,"Реальный API ФСП пока не настроен")
    existing=achievements_for(user["candidate_id"])
    with connection() as conn:
        audit(conn,user["id"],"fsp_mock_synced",user["candidate_id"],{"count":len(existing)})
    return {"mode":"mock","count":len(existing),"synced_at":now(),
            "message":"Обновлён тестовый адаптер. Реальные результаты ФСП не загружались."}

@app.get("/api/achievements/{id}")
def achievement(id:str,user=Depends(session_user)):
    with connection() as conn:
        row=conn.execute("SELECT * FROM achievements WHERE id=?",(id,)).fetchone()
    if not row:
        raise HTTPException(404,"Достижение не найдено")
    c=get_candidate(row["candidate_id"],user)
    if user["role"]=="candidate" and user["candidate_id"]!=c["id"]:
        raise HTTPException(403,"Недостаточно прав")
    return dict(json.loads(row["payload"]),active=bool(row["active"]),version=row["version"])

@app.get("/api/vacancies")
def vacancies(role:str|None=None,q:str=Query("",max_length=120),own_only:bool=False,matched:bool=False,
              saved_only:bool=False,user=Depends(session_user)):
    items=list_vacancies(user)
    if own_only:
        require_role(user,"employer")
        items=[v for v in items if v["org_id"]==user["org_id"]]
    if role:
        items=[v for v in items if v["role"]==role]
    if q:
        terms=q.lower().split()
        items=[v for v in items if all(t in (v["title"]+" "+v["company"]+" "+v["description"]).lower() for t in terms)]
    with connection() as conn:
        saved={r[0] for r in conn.execute("SELECT entity_id FROM bookmarks WHERE user_id=? AND entity_type='vacancy'",(user["id"],))}
    if saved_only:
        items=[v for v in items if v["id"] in saved]
    meta=None
    if matched:
        require_role(user,"candidate")
        items,meta=rank_vacancies(get_candidate(user["candidate_id"],user),items)
    for v in items:
        v["saved"]=v["id"] in saved
    return {"items":items,"meta":meta}

@app.post("/api/vacancies",status_code=201)
def create_vacancy(body:VacancyInput,user=Depends(session_user)):
    from .product import create
    return create(body,user)

@app.post("/api/vacancies/{id}/archive")
def archive(id:str,user=Depends(session_user)):
    require_role(user,"employer")
    from .auth import require_org_role
    require_org_role(user,'owner','recruiter')
    with connection() as conn:
        row=conn.execute("SELECT * FROM vacancies WHERE id=? AND org_id=?",(id,user["org_id"])).fetchone()
        if not row:
            raise HTTPException(404,"Вакансия не найдена")
        conn.execute("UPDATE vacancies SET archived=1,version=version+1 WHERE id=?",(id,))
        bump(conn)
        audit(conn,user["id"],"vacancy_archived",id)
        update_index('vacancy',id,conn)
    return {"ok":True}

@app.post("/api/extract")
def extract(body:ExtractInput,user=Depends(session_user)):
    return {"skills":extract_skills(body.text),"method":"dictionary_with_spans","requires_review":True}

@app.get("/api/match")
def match(candidate_id:str,vacancy_id:str,user=Depends(session_user)):
    if user["role"]=="candidate" and candidate_id!=user["candidate_id"]:
        raise HTTPException(403,"Недостаточно прав")
    c=get_candidate(candidate_id,user)
    v=get_vacancy(vacancy_id,user)
    if v["archived"]:
        raise HTTPException(409,"Вакансия закрыта")
    return {"candidate":c,"vacancy":v,"match":assessment(c,v)}

def offer_view(row):
    return dict(json.loads(row["payload"]),id=row["id"],org_id=row["org_id"],candidate_id=row["candidate_id"],
                vacancy_id=row["vacancy_id"],status=row["status"],version=row["version"],
                history=json.loads(row["history"]),created_at=row["created_at"])

@app.get("/api/offers")
def offers(user=Depends(session_user)):
    if user["role"]=="employer":
        condition,identity="org_id=?",user["org_id"]
    elif user["role"]=="candidate":
        condition,identity="candidate_id=?",user["candidate_id"]
    else:
        return {"items":[]}
    with connection() as conn:
        return {"items":[offer_view(r) for r in conn.execute("SELECT * FROM offers WHERE "+condition+" ORDER BY created_at DESC",(identity,))]}

@app.post("/api/offers",status_code=201)
def send_offer(body:OfferInput,user=Depends(session_user)):
    require_role(user,"employer")
    require_verified(user)
    from .privacy import eligible
    from .trust import ensure_trusted
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        ensure_trusted(conn,user)
        existing=conn.execute("SELECT * FROM offers WHERE sender_id=? AND request_key=?",(user["id"],body.idempotency_key)).fetchone()
        if existing:
            saved=json.loads(existing["payload"])
            if existing["candidate_id"]!=body.candidate_id or existing["vacancy_id"]!=body.vacancy_id or any(saved[k]!=getattr(body,k) for k in ["salary_min","salary_max","salary_type","message"]):
                raise HTTPException(409,"Ключ запроса уже использован для другого предложения")
            return offer_view(existing)
        cr=conn.execute("SELECT * FROM candidates WHERE id=? AND visible=1",(body.candidate_id,)).fetchone()
        vr=conn.execute("SELECT * FROM vacancies WHERE id=? AND org_id=? AND archived=0",(body.vacancy_id,user["org_id"])).fetchone()
        if not cr:
            raise HTTPException(404,"Кандидат недоступен")
        if not vr:
            raise HTTPException(403,"Можно отправлять предложения только по своим активным вакансиям")
        c=json.loads(cr["payload"])
        c['visible']=bool(cr['visible'])
        if not eligible(conn,c,user.get('org_id')):raise HTTPException(404,'Кандидат ещё не завершил аттестацию или недоступен')
        from .trust import risk_signals
        from .recruiting import datetime_before
        if conn.execute('SELECT 1 FROM employer_blocks WHERE candidate_id=? AND org_id=?',(c['id'],user['org_id'])).fetchone():raise HTTPException(403,'Контакт недоступен')
        if risk_signals(body.message+' '+vr['payload']):raise HTTPException(422,'Условия требуют модерации')
        daily=sum(conn.execute('SELECT COUNT(*) FROM '+table+' WHERE org_id=? AND created_at>?',(user['org_id'],datetime_before(86400))).fetchone()[0] for table in ['offers','invitations'])
        if daily>=30:raise HTTPException(429,'Достигнут дневной лимит приглашений')
        v=json.loads(vr["payload"])
        if not c.get("looking",True):
            raise HTTPException(409,"Кандидат сейчас не рассматривает предложения")
        if c.get("salary_min") is not None and body.salary_type=="net" and body.salary_max<c["salary_min"]:
            raise HTTPException(422,"Предложение ниже зарплатных ожиданий кандидата")
        passport=passports.create(c['id'],v['id'],user,conn)
        id="o_"+secrets.token_hex(8)
        timestamp=now()
        payload={"candidate_name":c["name"],"candidate_title":c["title"],"company":v["company"],
                 "vacancy_title":v["title"],"vacancy_snapshot":v,"salary_min":body.salary_min,
                 "salary_max":body.salary_max,"salary_type":body.salary_type,"currency":"RUB","period":"month",
                 "message":body.message,"work_mode":v["work_mode"],
                 "passport_id":passport["id"],"match_snapshot":passport["snapshot"]}
        history=[{"status":"sent","at":timestamp,"actor":user["role"],"message":body.message}]
        conn.execute("INSERT INTO offers(id,org_id,candidate_id,vacancy_id,sender_id,payload,status,history,created_at,request_key) VALUES(?,?,?,?,?,?,'sent',?,?,?)",
                     (id,user["org_id"],c["id"],v["id"],user["id"],dumps(payload),dumps(history),timestamp,body.idempotency_key))
        audit(conn,user["id"],"offer_sent",id,{"candidate_id":c["id"],"vacancy_id":v["id"]})
        result=offer_view(conn.execute("SELECT * FROM offers WHERE id=?",(id,)).fetchone())
    return result

@app.post("/api/offers/{id}/respond")
def respond(id:str,body:OfferResponse,user=Depends(session_user)):
    if user['role']=='employer':
        from .auth import require_org_role
        require_org_role(user,'owner','recruiter')
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row=conn.execute("SELECT * FROM offers WHERE id=?",(id,)).fetchone()
        if not row or (user["role"]=="candidate" and row["candidate_id"]!=user["candidate_id"]) or (user["role"]=="employer" and row["org_id"]!=user["org_id"]) or user["role"]=="admin":
            raise HTTPException(404,"Предложение не найдено")
        if row["version"]!=body.version:
            raise HTTPException(409,"Статус изменился. Обновите список.")
        if row["status"] not in ["sent","viewed","clarification"]:
            raise HTTPException(409,"Предложение уже завершено")
        if user["role"]=="employer" and body.action not in {"withdrawn","replied"} or user["role"]=="candidate" and body.action in {"withdrawn","replied"}:
            raise HTTPException(403,"Это действие недоступно вашей роли")
        history=json.loads(row["history"])
        from .privacy import scrub
        text=scrub(body.message) if user['role']=='candidate' and body.action!='accepted' else body.message
        history.append({"status":body.action,"at":now(),"actor":user["role"],"message":text})
        status=row["status"] if body.action=="replied" else body.action
        if body.action=='accepted':
            from .trust import company
            from .privacy import consent
            if conn.execute('SELECT 1 FROM employer_blocks WHERE candidate_id=? AND org_id=?',(row['candidate_id'],row['org_id'])).fetchone():raise HTTPException(403,'Компания заблокирована кандидатом')
            vr=conn.execute('SELECT archived FROM vacancies WHERE id=?',(row['vacancy_id'],)).fetchone()
            if not vr or vr[0] or company(conn,row['org_id'])['verification'] not in {'verified','demo_verified'}:raise HTTPException(409,'Условия больше не актуальны')
            if not consent(conn,row['candidate_id'],'processing'):raise HTTPException(403,'Согласие отозвано')
            conn.execute('INSERT INTO contact_grants VALUES(?,?,?,NULL,1,?,NULL)',('grant_'+secrets.token_hex(10),row['candidate_id'],row['org_id'],now()))
        conn.execute("UPDATE offers SET status=?,history=?,version=version+1 WHERE id=?",(status,dumps(history),id))
        audit(conn,user["id"],"offer_"+body.action,id)
        return offer_view(conn.execute("SELECT * FROM offers WHERE id=?",(id,)).fetchone())

@app.post("/api/bookmarks")
def bookmark(body:BookmarkInput,user=Depends(session_user)):
    if body.entity_type=="candidate":
        require_role(user,"employer","admin")
        get_candidate(body.entity_id,user)
    else:
        get_vacancy(body.entity_id,user)
    with connection() as conn:
        if body.saved:
            conn.execute("INSERT OR IGNORE INTO bookmarks VALUES(?,?,?)",(user["id"],body.entity_type,body.entity_id))
        else:
            conn.execute("DELETE FROM bookmarks WHERE user_id=? AND entity_type=? AND entity_id=?",(user["id"],body.entity_type,body.entity_id))
    return {"saved":body.saved}

@app.get("/api/admin/achievements")
def admin_achievements(user=Depends(session_user)):
    require_role(user,"admin")
    with connection() as conn:
        rows=conn.execute("SELECT a.*,json_extract(c.payload,'$.name') AS candidate_name FROM achievements a JOIN candidates c ON c.id=a.candidate_id").fetchall()
        return {"items":[dict(json.loads(r["payload"]),active=bool(r["active"]),version=r["version"],candidate_name=r["candidate_name"]) for r in rows]}

@app.put("/api/admin/achievements/{id}")
def toggle_achievement(id:str,body:AchievementState,user=Depends(session_user)):
    require_role(user,"admin")
    if not DEMO:
        raise HTTPException(403,"Управление mock отключено")
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row=conn.execute("SELECT * FROM achievements WHERE id=?",(id,)).fetchone()
        if not row:
            raise HTTPException(404,"Запись не найдена")
        if not json.loads(row["payload"]).get("demo"):
            raise HTTPException(403,"Можно изменять только тестовые записи")
        if row["version"]!=body.version:
            raise HTTPException(409,"Запись изменилась")
        conn.execute("UPDATE achievements SET active=?,version=version+1 WHERE id=?",(body.active,id))
        conn.execute("UPDATE candidates SET version=version+1 WHERE id=?",(row["candidate_id"],))
        bump(conn)
        audit(conn,user["id"],"mock_source_updated",id,{"active":body.active})
        update_index('candidate',row['candidate_id'],conn)
    return {"ok":True,"active":body.active}

@app.post("/api/imports",status_code=202)
def start_import(body:ImportInput,user=Depends(session_user)):
    require_role(user,"employer","admin")
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        running=conn.execute("SELECT id FROM imports WHERE actor_id=? AND status IN ('queued','running')",(user['id'],)).fetchone()
        if running:
            raise HTTPException(409,"Импорт уже выполняется")
        id="import_"+secrets.token_hex(8)
        conn.execute("INSERT INTO imports(id,actor_id,status,created_at) VALUES(?,?,'queued',?)",(id,user["id"],now()))
        enqueue('hh.import',{'query':body.query,'limit':body.limit},user['id'],request_key=id,job_id=id,conn=conn)
    return {"id":id,"status":"queued"}

@app.get("/api/imports/{id}")
def import_state(id:str,user=Depends(session_user)):
    require_role(user,"employer","admin")
    with connection() as conn:
        row=conn.execute("SELECT * FROM imports WHERE id=? AND actor_id=?",(id,user["id"])).fetchone()
        if not row:
            raise HTTPException(404,"Импорт не найден")
        return dict(row)

from .members import router as members_router
app.include_router(members_router)
from .expert_review import router as expert_router
app.include_router(expert_router)
from .openapi_contract import install as install_openapi
install_openapi(app)

dist=ROOT/"frontend"/"dist"
if (dist/"assets").exists():
    app.mount("/assets",StaticFiles(directory=dist/"assets"),name="assets")
if (dist/"api-ui").exists():
    app.mount("/api-ui",StaticFiles(directory=dist/"api-ui"),name="api-ui")

@app.get("/api/docs",include_in_schema=False)
def api_docs():
    return FileResponse(dist/"api-ui"/"index.html")

@app.get("/api/redoc",include_in_schema=False)
def old_docs():
    return RedirectResponse("/api/docs")


@app.get('/delivery/{filename}',include_in_schema=False)
def delivery_file(filename:str):
    from fastapi.responses import FileResponse
    allowed={'FSP_CAREER_GUIDE.pdf','FSP_CAREER_GUIDE.docx','FSP_CAREER_PRESENTATION.pdf','FSP_CAREER_PRESENTATION.pptx'}
    if filename not in allowed:raise HTTPException(404,'Документ не найден')
    path=Path(__file__).resolve().parents[1]/'docs/delivery/2026-10-11'/filename
    if not path.is_file():raise HTTPException(404,'Документ ещё не опубликован')
    media={'.pdf':'application/pdf','.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','.pptx':'application/vnd.openxmlformats-officedocument.presentationml.presentation'}
    return FileResponse(path,filename=filename,media_type=media[path.suffix])

@app.get("/{path:path}",include_in_schema=False)
def frontend(path:str):
    if path.startswith(("api/","delivery/")):
        raise HTTPException(404,"Метод не найден")
    index=dist/"index.html"
    if not index.exists():
        return JSONResponse({"detail":"Соберите интерфейс: npm run build в frontend"},status_code=503)
    # Revalidate the entry document on every visit; an old open bundle still needs a user reload.
    return FileResponse(index,headers={"Cache-Control":"no-store"})
