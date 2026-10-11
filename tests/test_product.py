import json
from backend import db
from backend.domain import get_candidate,get_vacancy
from backend.matching import assessment,rank_candidates,rank_vacancies,constraints
from backend.taxonomy import extract_skills,categories
from backend.ml import engine

def profile_payload(candidate):
    c=candidate.get("/api/profile").json()
    return {k:c.get(k) for k in ["name","title","role","city","experience_years","salary_min","skills","bio","visible","looking","work_modes","version"]}|{"relocate":c.get("relocate",False)}

def offer_payload(**changes):
    return {"candidate_id":"c_anna","vacancy_id":"v_python","salary_min":120000,"salary_max":180000,
            "salary_type":"net","message":"Приглашаем обсудить задачи и условия.","idempotency_key":"request-product-001"}|changes

def test_auth_and_csrf(clients):
    employer,candidate,expert,sign=clients
    assert employer.get("/api/system").status_code==200
    assert candidate.get("/api/candidates").status_code==403
    assert employer.get("/api/admin/achievements").status_code==403
    assert employer.post("/api/bookmarks",json={"entity_type":"candidate","entity_id":"c_anna","saved":True},headers={"X-CSRF-Token":""}).status_code==403
    assert employer.post("/api/auth/demo",json={"account":"u_admin"},headers={"Origin":"https://evil.example"}).status_code==403
    assert employer.post("/api/auth/login",json={"email":"hr@sigma.demo","password":"wrong"}).status_code==401
    result=employer.post("/api/auth/login",json={"email":"hr@sigma.demo","password":"DemoPass2026!"})
    assert result.status_code==200
    assert "password" not in result.json()

def test_hidden_profile_has_no_search_direct_match_or_source_access(clients):
    employer,candidate,expert,sign=clients
    body=profile_payload(candidate)|{"visible":False}
    assert candidate.put("/api/profile",json=body).status_code==200
    assert candidate.get("/api/profile").status_code==200
    assert all(c["id"]!="c_anna" for c in employer.get("/api/candidates").json()["items"])
    assert employer.get("/api/candidates/c_anna").status_code==404
    assert employer.get("/api/match?candidate_id=c_anna&vacancy_id=v_python").status_code==404
    assert employer.get("/api/achievements/a_anna_api").status_code==404
    assert employer.post("/api/bookmarks",json={"entity_type":"candidate","entity_id":"c_anna","saved":True}).status_code==404
    assert employer.post("/api/offers",json=offer_payload()).status_code==404
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM search_index WHERE entity_id='c_anna'").fetchone()[0]==0

def test_offer_org_isolation_idempotency_response_and_snapshot(clients):
    employer,candidate,expert,sign=clients
    body=offer_payload()
    created=employer.post("/api/offers",json=body)
    assert created.status_code==201,created.text
    offer=created.json()
    assert employer.post("/api/offers",json=body).json()["id"]==offer["id"]
    assert employer.post("/api/offers",json=body|{"message":"Другая версия сообщения"}).status_code==409
    assert len(candidate.get("/api/offers").json()["items"])==1
    sign(expert,"u_employer2")
    assert expert.get("/api/offers").json()["items"]==[]
    assert expert.post("/api/offers",json=offer_payload(idempotency_key="other-company-key")).status_code==403
    assert expert.post("/api/vacancies/v_python/archive",json={}).status_code==404
    assert expert.post("/api/offers/"+offer["id"]+"/respond",json={"action":"withdrawn","version":1}).status_code==404
    assert candidate.get("/api/offers").json()["items"][0]["vacancy_snapshot"]["title"]==get_vacancy("v_python")["title"]
    assert employer.post("/api/offers/"+offer["id"]+"/respond",json={"action":"accepted","version":1}).status_code==403
    response=candidate.post("/api/offers/"+offer["id"]+"/respond",json={"action":"accepted","message":"Готова обсудить начало работы","version":1})
    assert response.status_code==200,response.text
    assert response.json()["status"]=="accepted"
    assert len(response.json()["history"])==2
    assert candidate.post("/api/offers/"+offer["id"]+"/respond",json={"action":"declined","version":1}).status_code==409
    assert candidate.post("/api/offers/"+offer["id"]+"/respond",json={"action":"declined","version":2}).status_code==409
    assert employer.post("/api/vacancies/v_python/archive",json={}).status_code==200
    assert employer.post("/api/offers",json=offer_payload(idempotency_key="archived-vacancy-key")).status_code==403

def test_offer_clarification_and_withdrawal(clients):
    employer,candidate,expert,sign=clients
    assert employer.post("/api/offers",json=offer_payload(salary_max=90000,salary_min=60000)).status_code==422
    o=employer.post("/api/offers",json=offer_payload()).json()
    assert candidate.post("/api/offers/"+o["id"]+"/respond",json={"action":"withdrawn","version":1}).status_code==403
    r=candidate.post("/api/offers/"+o["id"]+"/respond",json={"action":"clarification","message":"Какой график?","version":1})
    assert r.status_code==200
    reply=employer.post("/api/offers/"+o["id"]+"/respond",json={"action":"replied","message":"Гибкий график, начало с 9 до 11.","version":2})
    assert reply.status_code==200 and reply.json()["status"]=="clarification"
    assert reply.json()["history"][-1]["status"]=="replied"
    assert candidate.post("/api/offers/"+o["id"]+"/respond",json={"action":"replied","message":"test","version":3}).status_code==403
    assert employer.post("/api/offers/"+o["id"]+"/respond",json={"action":"withdrawn","version":3}).json()["status"]=="withdrawn"

def test_source_revocation_removes_proof_but_keeps_self_claim(clients):
    employer,candidate,expert,sign=clients
    before=employer.get("/api/match?candidate_id=c_anna&vacancy_id=v_python").json()
    assert before["match"]["matrix"][0]["status"]=="demo_source"
    assert before["match"]["evidence_coverage"]>0
    revoke=expert.put("/api/admin/achievements/a_anna_api",json={"active":False,"version":1})
    assert revoke.status_code==200
    after=employer.get("/api/match?candidate_id=c_anna&vacancy_id=v_python").json()
    assert after["match"]["matrix"][0]["status"]=="claimed"
    assert after["match"]["evidence_coverage"]==0
    backend_category=next(r for r in after["candidate"]["categories"] if r["id"]=="backend")
    assert backend_category['status']=='demo' and backend_category['certification_id']
    assert any(r['id']=='backend' for r in after['candidate']['claimed_categories'])
    assert after["candidate"]["version"]>before["candidate"]["version"]
    assert expert.put("/api/admin/achievements/a_anna_api",json={"active":True,"version":1}).status_code==409
    assert expert.put("/api/admin/achievements/a_anna_api",json={"active":True,"version":2}).status_code==200

def test_team_result_and_sports_rank_do_not_prove_skills(clients):
    c=get_candidate("c_claims")
    c["achievements"]=[{"id":"fiction","active":True,"demo":True,"role":None,"individual_evidence":False,
                       "sports_rank":"Мастер спорта","task_skills":["python"],"result":"1 место"}]
    m=assessment(c,get_vacancy("v_python"))
    assert all(r["status"]=="claimed" for r in m["matrix"])
    assert all(r["basis"]=="claimed" for r in categories(c,c["achievements"]))
    beginner=get_candidate("c_nikita")
    assert next(r for r in beginner["categories"] if r["id"]=="backend")['status']=='demo'

def test_registration_empty_profile_and_optimistic_lock(clients):
    employer,candidate,expert,sign=clients
    r=candidate.post("/api/auth/register",json={"name":"Новый участник","email":"new@example.test","password":"LongPassword!","role":"candidate","processing_consent":True})
    assert r.status_code==201,r.text
    candidate.headers["X-CSRF-Token"]=r.json()["csrf"]
    assert candidate.get("/api/profile").json()["achievements"]==[]
    assert candidate.post("/api/profile/fsp-sync",json={}).json()["count"]==0
    body=profile_payload(candidate)|{"skills":["python"],"salary_min":80000}
    assert candidate.put("/api/profile",json=body).status_code==200
    assert candidate.put("/api/profile",json=body).status_code==409
    assert candidate.post("/api/auth/register",json={"name":"Ещё участник","email":"new@example.test","password":"LongPassword!","role":"candidate","processing_consent":True}).status_code==409

def test_candidate_cannot_read_other_profile_or_source(clients):
    employer,candidate,expert,sign=clients
    assert candidate.get("/api/candidates/c_timur").status_code==403
    assert candidate.get("/api/achievements/a_timur").status_code==403
    assert candidate.get("/api/match?candidate_id=c_timur&vacancy_id=v_front").status_code==403
    assert candidate.put("/api/admin/achievements/a_anna_api",json={"active":False,"version":1}).status_code==403

def test_salary_city_unknowns_and_neural_failure_fallback(clients,monkeypatch):
    c=get_candidate("c_anna")
    v=get_vacancy("v_python")
    assert not constraints(c,v|{"salary_max":90000})["eligible"]
    assert constraints(c,v|{"salary_max":90000,"salary_type":"gross"})["eligible"]
    assert constraints(c,v|{"salary_max":None})["unknown"]
    assert not constraints(c,v|{"work_mode":"hybrid","city":"Москва"})["eligible"]
    assert constraints(c|{"relocate":True},v|{"work_mode":"hybrid","city":"Москва"})["eligible"]
    monkeypatch.setattr(engine,"state","ready")
    def fail(*args,**kwargs):raise RuntimeError("test inference failure")
    monkeypatch.setattr(engine,"embed",fail)
    _,meta=rank_candidates(v,[c])
    assert meta["mode"]=="baseline"
    _,meta=rank_vacancies(c,[v])
    assert meta["mode"]=="baseline"

def test_vacancy_creation_preserves_review_and_validation(clients):
    employer,candidate,expert,sign=clients
    body={"title":"Новый Python API","description":"Разработка API на Python и FastAPI с PostgreSQL.","role":"backend",
          "required_skills":["python","fastapi"],"optional_skills":["git"],"salary_min":120000,"salary_max":160000}
    assert employer.post("/api/vacancies",json=body|{"optional_skills":["python"]}).status_code==422
    r=employer.post("/api/vacancies",json=body)
    assert r.status_code==201,r.text
    assert r.json()["org_id"]=="org_sigma" and r.json()["extraction_reviewed"]
    assert employer.get("/api/candidates?vacancy_id="+r.json()["id"]).status_code==200

def test_bookmarks_are_private_and_persisted(clients):
    employer,candidate,expert,sign=clients
    assert employer.post("/api/bookmarks",json={"entity_type":"candidate","entity_id":"c_anna","saved":True}).status_code==200
    assert employer.get("/api/candidates?saved_only=true").json()["items"][0]["id"]=="c_anna"
    sign(expert,"u_employer2")
    assert expert.get("/api/candidates?saved_only=true").json()["items"]==[]

def test_skill_extraction_handles_boundaries_negation_and_optional():
    extracted={x["skill"]:x for x in extract_skills("Нужны Java и Spring. JavaScript не требуется. Docker будет плюсом. C# и .NET обязательны.")}
    assert extracted["java"]["required"] and not extracted["java"]["negated"]
    assert extracted["javascript"]["negated"]
    assert not extracted["docker"]["required"]
    assert extracted["csharp"]["required"] and extracted["dotnet"]["required"]
    assert "java" not in {x["skill"] for x in extract_skills("JavaScript React TypeScript")}
    assert extract_skills("Не требуется Python")[0]["negated"]
