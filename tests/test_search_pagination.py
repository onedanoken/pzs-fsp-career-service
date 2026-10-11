import copy,json
from backend import db

def test_pages_remain_authorized_and_recheck_hidden_profiles(clients):
    employer,candidate,_,_=clients
    with db.connection() as conn:
        template=json.loads(conn.execute("SELECT payload FROM candidates WHERE id='c_anna'").fetchone()[0]);password=conn.execute("SELECT password FROM users WHERE id='u_anna'").fetchone()[0]
        for i in range(3):
            identity='c_page_'+str(i);uid='u_page_'+str(i);profile=copy.deepcopy(template);profile.update(id=identity,name='Reference page '+str(i),salary_min=50000,contacts={'email':uid+'@example.org'})
            conn.execute('INSERT INTO candidates(id,payload,visible) VALUES(?,?,1)',(identity,db.dumps(profile)))
            conn.execute('INSERT INTO users(id,role,name,email,password,candidate_id,email_verified) VALUES(?,?,?,?,?,?,1)',(uid,'candidate',profile['name'],uid+'@example.org',password,identity))
            for kind in ['processing','publication']:conn.execute('INSERT INTO consent_events VALUES(?,?,?,?,?,?)',('page_'+kind+str(i),identity,kind,1,'reference',db.now()))
            conn.execute('INSERT INTO grade_certifications VALUES(?,?,?,?,?,NULL,?,?,?,1)',('page_cert'+str(i),identity,'backend','Middle',100,'provisional',db.dumps({'calibration':'synthetic_fixture','demo':True,'tested_skills':['python','sql']}),db.now()))
    need=employer.post('/api/needs',json={'title':'Pagination reference','description':'Maintain database and API contracts with reproducible tests','team':'Controlled team reference pool','specialization':'backend','grade':'Middle','skills':['python','sql'],'salary_min':50000,'salary_max':200000,'employer_contact':'reference@example.org'}).json()['id']
    first=employer.post('/api/search-sessions',json={'need_id':need,'limit':1}).json();second=employer.post('/api/search-sessions',json={'need_id':need,'limit':1,'offset':1}).json()
    assert len(first['items'])==len(second['items'])==1 and first['page']['has_more']
    assert first['items'][0]['candidate']['id']!=second['items'][0]['candidate']['id']
    assert not first['items'][0]['candidate']['contacts_available']
    assert candidate.get('/api/search-sessions/'+first['id']).status_code==403
    hidden=first['items'][0]['candidate']['id']
    with db.connection() as conn:conn.execute('UPDATE candidates SET visible=0 WHERE id=?',(hidden,))
    replay=employer.get('/api/search-sessions/'+first['id']).json();assert all(x['candidate']['id']!=hidden for x in replay['items'])
    assert replay['page']['total_at_snapshot']==first['page']['total_at_snapshot']-1
    assert employer.post('/api/search-sessions',json={'need_id':need,'limit':101}).status_code==422
    assert employer.post('/api/search-sessions',json={'need_id':need,'offset':-1}).status_code==422
