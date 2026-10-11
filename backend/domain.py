import json
from fastapi import HTTPException
from .db import connection,DEMO
from .taxonomy import SKILLS, categories, normalize_text

def achievements_for(candidate_id, conn=None):
    def fetch(db):
        return [dict(json.loads(r["payload"]), active=bool(r["active"]), version=r["version"])
                for r in db.execute("SELECT * FROM achievements WHERE candidate_id=? ORDER BY json_extract(payload,'$.date') DESC",(candidate_id,))]
    if conn:
        return fetch(conn)
    with connection() as db:
        return fetch(db)

def candidate_from_row(row, conn=None,material=None):
    c = json.loads(row["payload"])
    c.update({"visible":bool(row["visible"]), "version":row["version"]})
    c["achievements"] = material['achievements'].get(c['id'],[]) if material is not None else achievements_for(c["id"],conn)
    c['claimed_categories']=categories(c,c['achievements'])
    def certified(db):
        from .certification_evidence import summary
        from .certification_validity import usable_certificate
        rows=material['cert_rows'].get(c['id'],[]) if material is not None else db.execute('SELECT * FROM grade_certifications WHERE candidate_id=? AND active=1',(c['id'],))
        return [{'id':r['specialization'],'category_id':r['specialization']+':'+r['grade'],'grade':r['grade'],'score':r['score'],'status':r['status'],'certification_id':r['id'],'label':r['specialization']+' '+r['grade'],**summary(r)} for r in rows if usable_certificate(r)]
    if conn:c['categories']=certified(conn)
    else:
        with connection() as db:c['categories']=certified(db)
    demonstrated = {s for a in c["achievements"] if a["active"] and a.get("individual_evidence") for s in a.get("task_skills",[])}
    c["all_skills"] = sorted(set(c.get("skills",[])) | demonstrated)
    c["skill_labels"] = {s:SKILLS[s][0] for s in c["all_skills"] if s in SKILLS}
    c["evidence_count"] = sum(a["active"] for a in c["achievements"])
    return c

def get_candidate(candidate_id, user=None):
    if user and user['role']=='employer':
        from .auth import require_verified
        require_verified(user)
    with connection() as conn:
        row=conn.execute("SELECT * FROM candidates WHERE id=?",(candidate_id,)).fetchone()
        if not row or (not DEMO and json.loads(row["payload"]).get("demo")):
            raise HTTPException(404,"Профиль не найден")
        if not row["visible"] and (not user or user.get("candidate_id")!=candidate_id and user.get("role")!="admin"):
            raise HTTPException(404,"Профиль не найден")
        result=candidate_from_row(row,conn)
        if user and user['role']=='candidate' and user.get('candidate_id')!=candidate_id:raise HTTPException(403,'Недостаточно прав')
        from .privacy import public_profile,eligible
        if user and user['role']=='employer' and not eligible(conn,result,user.get('org_id')):raise HTTPException(404,'Профиль не найден')
        return public_profile(result,user,conn) if user else result

def list_candidates():
    with connection() as conn:
        return [candidate_from_row(r,conn) for r in conn.execute("SELECT * FROM candidates WHERE visible=1 AND NOT EXISTS (SELECT 1 FROM users WHERE users.candidate_id=candidates.id AND users.active=0)") if DEMO or not json.loads(r["payload"]).get("demo")]

def vacancy_from_row(row, user=None):
    j=json.loads(row["payload"])
    from .product import public_vacancy
    j.update({"org_id":row["org_id"],"archived":bool(row["archived"]),"version":row["version"],
              "state":j.get('state','active'),"can_offer":bool(user and user["role"]=="employer" and user["org_id"]==row["org_id"] and public_vacancy(row))})
    j["skill_labels"]={s:SKILLS[s][0] for s in j.get("required_skills",[])+j.get("optional_skills",[]) if s in SKILLS}
    return j

def get_vacancy(vacancy_id,user=None):
    with connection() as conn:
        row=conn.execute("SELECT * FROM vacancies WHERE id=?",(vacancy_id,)).fetchone()
        from .product import public_vacancy
        if not row or (not DEMO and json.loads(row["payload"]).get("demo")) or (not public_vacancy(row) and not (user and (user.get('org_id')==row['org_id'] or user['role']=='admin'))):
            raise HTTPException(404,"Вакансия не найдена")
        return vacancy_from_row(row,user)

def list_vacancies(user=None):
    with connection() as conn:
        from .product import public_vacancy
        return [vacancy_from_row(r,user) for r in conn.execute("SELECT * FROM vacancies ORDER BY json_extract(payload,'$.published_at') DESC,id DESC") if (DEMO or not json.loads(r["payload"]).get("demo")) and (public_vacancy(r) or (user and user.get('org_id') and user['org_id']==r['org_id']))]

def candidate_document(c):
    # Exclude names and contact information from matching.
    proof_text=" ".join(a["title"]+" "+a.get("role_label","") for a in c["achievements"] if a["active"])
    skills=" ".join(SKILLS.get(s,(s,[]))[0] for s in c["all_skills"])
    return c["title"]+". "+skills+". "+c.get("bio","")+". Практические задачи: "+proof_text

def vacancy_document(v):
    skills=" ".join(SKILLS.get(s,(s,[]))[0] for s in v.get("required_skills",[]))
    return v["title"]+". Обязательные навыки: "+skills+". "+v.get("description","")

def rebuild_index():
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute("DELETE FROM search_index")
        for row in conn.execute('SELECT * FROM candidates WHERE visible=1 AND NOT EXISTS (SELECT 1 FROM users WHERE users.candidate_id=candidates.id AND users.active=0)').fetchall():
            c=candidate_from_row(row,conn)
            if not c.get('looking',True):continue
            conn.execute("INSERT INTO search_index(entity_id,entity_type,text) VALUES(?,?,?)",
                         (c["id"],"candidate",normalize_text(candidate_document(c))))
        for row in conn.execute('SELECT * FROM vacancies WHERE archived=0').fetchall():
            from .product import public_vacancy
            if not public_vacancy(row):continue
            v=vacancy_from_row(row)
            conn.execute("INSERT INTO search_index(entity_id,entity_type,text) VALUES(?,?,?)",
                         (v["id"],"vacancy",normalize_text(vacancy_document(v))))
        conn.execute("INSERT OR REPLACE INTO meta VALUES('search_schema','2')")

def ensure_index():
    with connection() as conn:row=conn.execute("SELECT value FROM meta WHERE key='search_schema'").fetchone()
    if not row or row[0]!='2':rebuild_index()

def update_index(entity_type,identity,conn=None):
    """Synchronously update one row in the mutation's transaction; queue its vector."""
    if entity_type not in {'candidate','vacancy'}:raise ValueError('Unknown search entity')
    if conn is None:
        with connection() as transaction:
            transaction.execute('BEGIN IMMEDIATE');return update_index(entity_type,identity,transaction)
    table='candidates' if entity_type=='candidate' else 'vacancies'
    row=conn.execute('SELECT * FROM '+table+' WHERE id=?',(identity,)).fetchone()
    conn.execute('DELETE FROM search_index WHERE entity_id=? AND entity_type=?',(identity,entity_type))
    if not row:return
    if entity_type=='candidate':
        item=candidate_from_row(row,conn)
        if not item['visible'] or not item.get('looking',True):return
        text=candidate_document(item)
    else:
        item=vacancy_from_row(row)
        from .product import public_vacancy
        if not public_vacancy(row):return
        text=vacancy_document(item)
    conn.execute('INSERT INTO search_index(entity_id,entity_type,text) VALUES(?,?,?)',(identity,entity_type,normalize_text(text)))
    from .jobs import enqueue
    from .ml import engine
    import hashlib
    text_hash=hashlib.sha256(text.encode()).hexdigest()
    enqueue('search.warm',{'entity_type':entity_type,'entity_id':identity,'document_hash':text_hash},
            request_key=entity_type+':'+identity+':'+engine.model_key+':'+text_hash,conn=conn)

def warm_document(payload):
    from .ml import engine
    if engine.state!='ready':return {'skipped':'models_not_ready'}
    try:
        if payload['entity_type']=='candidate':
            item=get_candidate(payload['entity_id'])
            if not item.get('looking',True):return {'skipped':'not_looking'}
            text=candidate_document(item)
        else:
            item=get_vacancy(payload['entity_id'])
            if item['archived']:return {'skipped':'archived'}
            text=vacancy_document(item)
        engine.embed([text]);return {'entity_id':item['id'],'version':item['version'],'model':engine.model_key}
    except HTTPException:return {'skipped':'not_visible_or_removed'}

def queue_warmup():
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        for kind,table,condition in [('candidate','candidates','visible=1'),('vacancy','vacancies','archived=0')]:
            for row in conn.execute('SELECT id FROM '+table+' WHERE '+condition).fetchall():update_index(kind,row['id'],conn)
