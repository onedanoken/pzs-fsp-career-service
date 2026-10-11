import json
from backend import db
from backend.demo_stage03 import readable_need_title
from test_stage03 import invite

PROBE={'title':'Browser acceptance API 1791199194','description':'Разработка интеграций продуктовой платформы и API','team':'Команда инфраструктуры и интеграций','specialization':'backend','grade':'Middle','skills':['python'],'salary_min':120000,'salary_max':180000,'employer_contact':'hr@example.org'}

def test_only_known_fixture_gets_a_readable_title():
 assert readable_need_title(PROBE)=='Backend-разработчик: интеграции и API'
 assert readable_need_title(dict(PROBE,description='Рабочий запрос собственной команды'))==PROBE['title']
 assert readable_need_title(dict(PROBE,title='Backend-разработчик'))=='Backend-разработчик'

def test_historical_invitation_title_is_readable_without_changing_snapshot_or_status(clients):
 employer,candidate,_,_=clients
 need=employer.post('/api/needs',json=PROBE).json()['id'];item=invite(employer,need)
 accepted=candidate.post('/api/invitations/'+item['id']+'/respond',json={'action':'accepted','version':item['version']});assert accepted.status_code==200
 item=accepted.json()
 with db.connection() as conn:
  before=dict(conn.execute('SELECT payload,history,status FROM invitations WHERE id=?',(item['id'],)).fetchone())
  passport=dict(conn.execute('SELECT snapshot,integrity_sha256 FROM need_passports WHERE id=?',(json.loads(before['payload'])['passport_id'],)).fetchone())
 response=candidate.get('/api/invitations').json()['items'];visible=next(x for x in response if x['id']==item['id'])
 assert visible['need_title']=='Backend-разработчик: интеграции и API'
 assert visible['payload']['need']['title']==PROBE['title']
 assert visible['status']=='accepted'
 with db.connection() as conn:
  assert before==dict(conn.execute('SELECT payload,history,status FROM invitations WHERE id=?',(item['id'],)).fetchone())
  assert passport==dict(conn.execute('SELECT snapshot,integrity_sha256 FROM need_passports WHERE id=?',(json.loads(before['payload'])['passport_id'],)).fetchone())
