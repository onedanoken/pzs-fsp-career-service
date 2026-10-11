import json,random
import pytest
from backend.qa_screening import generate,scenario,GROUPS
from backend.work_samples import add_journey,AUTO_PATH_VERSION
from backend.jobs import Worker
from backend import db

@pytest.mark.parametrize('grade',['Intern','Junior','Middle','Senior'])
def test_qa_screening_is_role_specific_and_varied(grade):
 families=set()
 for seed in range(60):
  result=generate(grade,random.Random(seed),seed)
  path=add_journey(result,'qa',grade,AUTO_PATH_VERSION)
  screens=[x for x in path['items'] if x.get('stage')=='screening']
  assert len(screens)==3 and len(path['items'])==5
  assert len({x['competency'] for x in screens})==3
  assert all(x['kind'] in {'choice','multi'} for x in screens)
  assert all(x['family'].startswith('qa:') for x in screens)
  assert not any('SELECT ' in x['prompt'] or 'Напишите функцию' in x['prompt'] for x in screens)
  families.update(x['family'].split(':')[1] for x in screens)
 assert families=={f for v in GROUPS.values() for f in v}

def test_boundary_answers_follow_the_visible_contract():
 from backend.qa_screening import generate
 seen=0
 for seed in range(100):
  result=generate('Middle',random.Random(seed),seed)
  for item in result['items']:
   if item['family']!='qa:boundaries':continue
   c=item['private']['condition']
   selected={o['text'] for o in item['choices'] if o['id'] in item['private']['expected']}
   assert selected=={f"{c['low']-1} и {c['low']}",f"{c['high']} и {c['high']+1}",'Пустой ввод'}
   assert len(selected)<len(item['choices']);seen+=1
 assert seen>10

def test_qa_v2_public_screening_and_historic_path_isolation(clients,monkeypatch):
 _,candidate,_,_=clients
 monkeypatch.setenv('FSP_ASSESSMENT_PATH','work_sample_v1')
 old=candidate.post('/api/assessments',json={'specialization':'qa','grade':'Junior','mode':'practice'}).json()['id']
 Worker().run_once();previous=candidate.get('/api/assessments/'+old).json()
 assert any(i['kind']=='sql' for i in previous['items'])
 assert candidate.post('/api/assessments/'+old+'/cancel',json={}).status_code==200
 monkeypatch.setenv('FSP_ASSESSMENT_PATH','work_sample_v2_auto')
 new=candidate.post('/api/assessments',json={'specialization':'qa','grade':'Junior','mode':'practice'}).json()['id']
 Worker().run_once();current=candidate.get('/api/assessments/'+new).json()
 assert all(i['kind'] in {'choice','multi'} for i in current['items'][:3])
 assert all('private' not in i for i in current['items'])
 assert candidate.get('/api/assessments/'+old).json()['items']==previous['items']

def test_demo_seed_preserves_user_fields_and_marks_only_known_probes(clients):
 employer,_,_,_=clients
 from backend.demo_stage03 import seed_domain
 body={'title':'Browser acceptance API 12345','description':'Разработка интеграций продуктовой платформы и API','team':'Команда инфраструктуры и интеграций','specialization':'backend','grade':'Middle','skills':['python'],'salary_min':120000,'salary_max':180000,'employer_contact':'hr@example.org'}
 known=employer.post('/api/needs',json=body).json()['id']
 body['title']='Browser acceptance API 54321';body['description']='Запрос, созданный пользователем для собственной команды.'
 own=employer.post('/api/needs',json=body).json()['id']
 with db.connection() as conn:
  row=conn.execute("SELECT payload FROM candidates WHERE id='c_anna'").fetchone();value=json.loads(row[0]);value['assessment_onboarding_done']=False;value['name']='Собственное тестовое имя';conn.execute("UPDATE candidates SET payload=? WHERE id='c_anna'",(db.dumps(value),))
 seed_domain();seed_domain()
 items={x['id']:x for x in employer.get('/api/needs').json()['items']}
 assert known in items and items[known]['payload']['demo_test_artifact']
 assert not items[own]['payload'].get('demo_test_artifact')
 assert sum(x['payload'].get('origin')=='demo_catalog_v2' for x in items.values())==2
 with db.connection() as conn:after=json.loads(conn.execute("SELECT payload FROM candidates WHERE id='c_anna'").fetchone()[0])
 assert after['name']=='Собственное тестовое имя' and after['assessment_onboarding_done'] is False
