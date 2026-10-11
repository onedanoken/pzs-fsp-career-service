"""Versioned match snapshots. The hash is integrity, not FSP certification."""
import hashlib,json,secrets
from fastapi import HTTPException
from .db import connection,dumps,now
from .domain import get_candidate,get_vacancy,candidate_from_row,vacancy_from_row
from .matching import assessment,MATCH_VERSION

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def fingerprint(candidate,vacancy):
    return digest({'candidate_id':candidate['id'],'profile_version':candidate['version'],
                   'vacancy_id':vacancy['id'],'vacancy_version':vacancy['version'],'algorithm':MATCH_VERSION,
                   'sources':sorted((a['id'],a['version'],a['active'],digest(a)) for a in candidate['achievements'])})

def build(candidate,vacancy):
    match=assessment(candidate,vacancy)
    for row in match['matrix']:
        for proof in row['evidence']:
            source=next(a for a in candidate['achievements'] if a['id']==proof['id'])
            proof.update({'version':source['version'],'source_id':source.get('source_id'),
                          'source':source.get('source'),'source_sha256':digest(source)})
    questions=[{'kind':'required_skill','skill':row['skill'],'question':'Уточните опыт работы с '+row['label']} for row in match['matrix'] if row['required'] and row['status']=='unknown']
    questions += [{'kind':'work_condition','question':question} for question in match['constraints']['unknown']]
    return {'schema_version':'match-passport-v1','algorithm_version':MATCH_VERSION,
            'candidate':{'id':candidate['id'],'name':candidate['name'],'title':candidate['title'],'version':candidate['version']},
            'vacancy':{'id':vacancy['id'],'title':vacancy['title'],'company':vacancy.get('company'),'version':vacancy['version']},
            'profile_snapshot':{key:candidate.get(key) for key in ['id','version','title','role','skills','city','salary_min','currency','experience_years','work_modes','relocate','looking']},
            'vacancy_snapshot':{key:vacancy.get(key) for key in ['id','version','title','role','required_skills','optional_skills','salary_min','salary_max','salary_type','currency','experience_min','work_mode','city']},
            'match':match,'questions':questions,'sources':candidate['achievements'],
            'demo':bool(candidate.get('demo') or vacancy.get('demo') or any(a.get('demo') for a in candidate['achievements'])),
            'notice':'Совпадение требований и происхождение данных. Не вероятность найма, не аттестация грейда.',
            'data_fingerprint':fingerprint(candidate,vacancy)}

def create(candidate_id,vacancy_id,user,conn=None):
    if conn is None:
        with connection() as transaction:
            transaction.execute('BEGIN IMMEDIATE')
            return create(candidate_id,vacancy_id,user,transaction)
    if user['role']=='candidate' and candidate_id!=user['candidate_id']:raise HTTPException(403,'Недостаточно прав')
    cr=conn.execute('SELECT * FROM candidates WHERE id=?',(candidate_id,)).fetchone()
    vr=conn.execute('SELECT * FROM vacancies WHERE id=?',(vacancy_id,)).fetchone()
    if not cr or (not cr['visible'] and user.get('candidate_id')!=candidate_id and user['role']!='admin'):raise HTTPException(404,'Профиль не найден')
    if not vr or vr['archived']:raise HTTPException(409,'Вакансия недоступна')
    from .privacy import public_profile,eligible
    candidate=public_profile(candidate_from_row(cr,conn),user,conn)
    if user['role']=='employer' and not eligible(conn,candidate,user.get('org_id')):raise HTTPException(404,'Профиль недоступен')
    snapshot=build(candidate,vacancy_from_row(vr,user))
    identity='passport_'+secrets.token_hex(10);created=now();checksum=digest(snapshot)
    conn.execute('INSERT INTO passports VALUES(?,?,?,?,?,?,?,?,?)',(identity,user['id'],user.get('org_id'),candidate_id,vacancy_id,dumps(snapshot),snapshot['data_fingerprint'],checksum,created))
    return {'id':identity,'created_at':created,'integrity_sha256':checksum,'snapshot':snapshot,'stale':False}

def read(identity,user):
    with connection() as conn:
        row=conn.execute('SELECT * FROM passports WHERE id=?',(identity,)).fetchone()
    if not row or not (row['actor_id']==user['id'] or user['role']=='admin' or
            user['role']=='candidate' and row['candidate_id']==user.get('candidate_id') or
            user['role']=='employer' and row['org_id']==user.get('org_id')):
        raise HTTPException(404,'Паспорт не найден')
    candidate=get_candidate(row['candidate_id'],user);vacancy=get_vacancy(row['vacancy_id'],user)
    snapshot=json.loads(row['snapshot'])
    return {'id':identity,'created_at':row['created_at'],'snapshot':snapshot,
            'integrity_sha256':row['integrity_sha256'],'integrity_valid':digest(snapshot)==row['integrity_sha256'],
            'stale':row['fingerprint']!=fingerprint(candidate,vacancy) or vacancy['archived'],
            'current_versions':{'profile':candidate['version'],'vacancy':vacancy['version']}}
