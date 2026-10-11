"""Synthetic contract checks; real Odoo validation is a separate integration run."""
import json
import secrets
import time
import httpx
import pytest
from backend import db
from backend.ats_bridge import synchronize,checked_url
from backend.ats_odoo import AdapterError,OdooRecruitment
from test_stage03 import create_need,invite,NEED

def key(employer,scopes=None):
    response=employer.post('/api/ats-connections',json={'name':'ATS operations test','scopes':scopes or ['needs.write','profiles.read','stages.write','events.read']})
    assert response.status_code==201,response.text
    data=response.json();return data,{'Authorization':'Bearer '+data['token']}

def test_diagnostics_are_owned_and_never_echo_profiles_or_credentials(clients):
    employer,candidate,other,sign=clients;client,headers=key(employer,['events.read'])
    denied=other.get('/api/ats/v1/profiles',headers=headers);assert denied.status_code==403
    bad=other.post('/api/ats/v1/sync-report',json={'run_id':secrets.token_hex(12),'provider':'odoo19','status':'failed','error_code':'invalid-raw-marker'},headers=headers)
    assert bad.status_code==422
    report=employer.get('/api/ats-connections/'+client['id']+'/diagnostics');assert report.status_code==200
    text=report.text
    assert client['token'] not in text and 'invalid-raw-marker' not in text and 'example.org' not in text
    assert [item['status_code'] for item in report.json()['activity']][:2]==[422,403]
    assert candidate.get('/api/ats-connections/'+client['id']+'/diagnostics').status_code==403
    sign(other,'u_employer2')
    assert other.get('/api/ats-connections/'+client['id']+'/diagnostics').status_code==404

def test_run_reporting_is_idempotent_fenced_and_recovers_an_interrupted_lease(clients):
    employer,candidate,other,sign=clients;client,headers=key(employer)
    first={'run_id':secrets.token_hex(12),'provider':'odoo19','status':'running'}
    assert other.post('/api/ats/v1/sync-report',json=first,headers=headers).status_code==200
    second={**first,'run_id':secrets.token_hex(12)}
    assert other.post('/api/ats/v1/sync-report',json=second,headers=headers).status_code==409
    with db.connection() as conn:
        row=conn.execute('SELECT payload FROM ats_connections WHERE id=?',(client['id'],)).fetchone();settings=json.loads(row[0]);settings['sync']['lease_until']=time.time()-1;conn.execute('UPDATE ats_connections SET payload=? WHERE id=?',(db.dumps(settings),client['id']))
    assert employer.get('/api/ats-connections/'+client['id']+'/diagnostics').json()['sync']['error_code']=='worker_interrupted'
    assert other.post('/api/ats/v1/sync-report',json=second,headers=headers).status_code==200
    stale={**first,'status':'completed','counts':{'needs':1}}
    assert other.post('/api/ats/v1/sync-report',json=stale,headers=headers).status_code==409
    complete={**second,'status':'completed','counts':{'needs':2}}
    result=other.post('/api/ats/v1/sync-report',json=complete,headers=headers);assert result.status_code==200
    assert other.post('/api/ats/v1/sync-report',json=complete,headers=headers).json()==result.json()
    assert other.post('/api/ats/v1/sync-report',json={**complete,'counts':{'needs':3}},headers=headers).status_code==409
    report=employer.get('/api/ats-connections/'+client['id']+'/diagnostics').json()
    assert report['sync']['status']=='completed' and report['sync']['last_success_at']

def test_single_profile_and_visible_hiring_stage_follow_ownership_and_consent(clients):
    employer,candidate,other,sign=clients;client,headers=key(employer)
    item=invite(employer,create_need(employer));path='/api/ats/v1/profiles/'+item['id']
    assert other.get(path,headers=headers).status_code==404
    assert candidate.post('/api/invitations/'+item['id']+'/respond',json={'action':'accepted','version':item['version']}).status_code==200
    assert other.get(path,headers=headers).status_code==404
    assert candidate.post('/api/invitations/'+item['id']+'/ats-consent',json={'granted':True,'fields':['name']}).status_code==200
    assert list(other.get(path,headers=headers).json()['profile'])==['name']
    stage={'invitation_id':item['id'],'external_candidate_id':'odoo-17','stage':'Interview'}
    assert other.post('/api/ats/v1/stages',json=stage,headers={**headers,'Idempotency-Key':secrets.token_hex(12)}).status_code==200
    for person in [employer,candidate]:
        invitation=next(row for row in person.get('/api/invitations').json()['items'] if row['id']==item['id'])
        assert invitation['recruitment_stage']['stage']=='Interview'
    assert candidate.post('/api/invitations/'+item['id']+'/ats-consent',json={'granted':False}).status_code==200
    assert other.get(path,headers=headers).status_code==404
    report=employer.get('/api/ats-connections/'+client['id']+'/diagnostics').json()
    assert report['awaiting_ack']==1

def test_key_rotation_preserves_connection_and_invalidates_old_key(clients):
    employer,candidate,other,sign=clients;client,old=key(employer)
    response=employer.post('/api/ats-connections/'+client['id']+'/rotate',json={})
    assert response.status_code==200 and response.json()['id']==client['id']
    assert other.get('/api/ats/v1/events',headers=old).status_code==401
    fresh={'Authorization':'Bearer '+response.json()['token']}
    assert other.get('/api/ats/v1/events',headers=fresh).status_code==200
    sign(other,'u_employer2')
    assert other.post('/api/ats-connections/'+client['id']+'/rotate',json={}).status_code==404

class Platform:
    def __init__(self):self.frames=[];self.profiles_data=[];self.consent=True;self.closed=[];self.exported_stages=[]
    def request(self,method,path,body):self.frames.append(body);return {'connection_id':'ats_operations_test','status':body['status']}
    def events(self,cursor):return {'items':[],'has_more':False,'next_cursor':cursor}
    def import_need(self,external,need,key):return {'id':'need_fixture'}
    def close_need(self,external,key):self.closed.append(external)
    def profiles(self):return self.profiles_data if self.consent else []
    def profile(self,identity):return self.profiles_data[0] if self.consent else None
    def stage(self,*args):self.exported_stages.append(args)

class Vendor:
    def __init__(self):self.records={};self.active=True;self.fail=False;self.after_export=None
    def positions(self):
        if self.fail:raise AdapterError('network_error')
        return [{'id':1,'name':'ATS fixture position','description':'Maintain reliable product APIs and data processing','active':self.active,'no_of_recruitment':1}]
    def upsert(self,reference,candidate,job,profile):
        self.records[reference]={'id':17,'stage':'Interview','stage_revision':1}
        if self.after_export:self.after_export()
        return self.records[reference]
    def status(self,reference):return self.records.get(reference)
    def forget(self,reference):self.records.pop(reference,None);return {'deleted':True}

def configuration(tmp_path):return {'provider':'odoo19','state_id':str(tmp_path/'isolated-bridge'),'database':'fixture','company_id':1,'job_rules':{'1':{key:value for key,value in NEED.items() if key not in {'title','description'}}}}

def test_bridge_restart_does_not_duplicate_and_failed_inventory_does_not_close_jobs(tmp_path,clients):
    platform=Platform();vendor=Vendor();cfg=configuration(tmp_path)
    platform.profiles_data=[{'invitation_id':'invite_fixture','candidate_id':'candidate_fixture','need_id':'need_fixture','profile':{'name':'Synthetic person'}}]
    assert synchronize(cfg,platform,vendor)['status']=='completed'
    assert synchronize(cfg,platform,vendor)['status']=='completed' and len(vendor.records)==1
    vendor.fail=True
    with pytest.raises(AdapterError,match='network_error'):synchronize(cfg,platform,vendor)
    assert platform.closed==[] and platform.frames[-1]['status']=='failed'
    vendor.fail=False;vendor.active=False;synchronize(cfg,platform,vendor)
    assert platform.closed and len(vendor.records)==1 # Existing candidate stage may continue.

def test_consent_revoked_while_exporting_removes_copy_and_never_updates_stage(tmp_path,clients):
    platform=Platform();vendor=Vendor();cfg=configuration(tmp_path)
    platform.profiles_data=[{'invitation_id':'invite_fixture','candidate_id':'candidate_fixture','need_id':'need_fixture','profile':{'name':'Synthetic person'}}]
    vendor.after_export=lambda:setattr(platform,'consent',False)
    synchronize(cfg,platform,vendor)
    assert not vendor.records and not platform.exported_stages

def test_odoo_transport_does_not_follow_redirects_or_retry_access_denial(monkeypatch):
    client=OdooRecruitment('https://ats.example','synthetic-transport-fixture','fixture',1);client.http.close();requests=[]
    def handler(request):requests.append(request);return httpx.Response(403,json={'unsafe':'credential-echo-marker'})
    client.http=httpx.Client(base_url='https://ats.example',transport=httpx.MockTransport(handler),follow_redirects=False)
    with pytest.raises(AdapterError,match='access_denied') as raised:client.positions()
    assert len(requests)==1 and 'credential-echo-marker' not in str(raised.value)
    client.close()

@pytest.mark.parametrize('url',['http://example.org','https://name:password@example.org','https://192.144.15.182','https://111.88.146.220','https://vehicle-reid-lct.duckdns.org'])
def test_operator_url_rules_reject_insecure_credentials_and_protected_hosts(url):
    with pytest.raises(AdapterError):checked_url(url)
