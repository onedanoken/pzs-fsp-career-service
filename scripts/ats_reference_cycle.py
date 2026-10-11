"""Real HTTP round trip with a vendor-neutral reference ATS, synthetic profiles."""
import json,os,secrets,sys,time
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.ats_client import UniversalATSClient
URL=os.environ.get('FSP_TEST_URL','http://127.0.0.1:8040')
report={'transport':'real localhost HTTP','external_system':'reference ATS client, not a proprietary vendor','data':'synthetic demo fixtures','steps':[]}
def step(name):report['steps'].append({'name':name,'passed':True})
with httpx.Client(base_url=URL,trust_env=False) as employer,httpx.Client(base_url=URL,trust_env=False) as candidate:
    for client,account in [(employer,'u_employer'),(candidate,'u_anna')]:
        response=client.post('/api/auth/demo',json={'account':account});response.raise_for_status();client.headers['X-CSRF-Token']=response.json()['csrf']
    key=employer.post('/api/ats-connections',json={'name':'Reference adapter '+secrets.token_hex(4),'scopes':['needs.write','profiles.read','stages.write','events.read']});key.raise_for_status()
    ats=UniversalATSClient(URL,key.json()['token'])
    try:
        need={'title':'ATS live reference API','description':'Maintain reliable product APIs and data processing','team':'Product infrastructure and integrations','specialization':'backend','grade':'Middle','skills':['python','sql'],'salary_min':120000,'salary_max':180000,'employer_contact':'hr@example.org'}
        external='REFERENCE-'+secrets.token_hex(6);request_key=secrets.token_hex(16)
        local=ats.import_need(external,need,request_key);again=ats.import_need(external,need,request_key);assert local['id']==again['id'];step('Import need and repeat delivery without duplicate')
        invitation=employer.post('/api/invitations',json={'need_id':local['id'],'candidate_id':'c_anna','message':'Discuss our infrastructure objectives','idempotency_key':secrets.token_hex(16)});invitation.raise_for_status();item=invitation.json()
        assert not any(p['invitation_id']==item['id'] for p in ats.profiles());step('Candidate absent before acceptance')
        candidate.post('/api/invitations/'+item['id']+'/respond',json={'action':'accepted','version':item['version']}).raise_for_status()
        assert not any(p['invitation_id']==item['id'] for p in ats.profiles());step('Candidate absent before separate ATS consent')
        candidate.post('/api/invitations/'+item['id']+'/ats-consent',json={'granted':True,'fields':['name','contacts','categories']}).raise_for_status()
        transferred=next(p for p in ats.profiles() if p['invitation_id']==item['id']);assert transferred['profile']['contacts'] and 'answers' not in transferred['profile'];step('Transfer selected fields to external client')
        external_cache={item['id']:transferred['profile']}
        status_key=secrets.token_hex(16);status=ats.stage(item['id'],'REFERENCE-C17','Interview',status_key);assert ats.stage(item['id'],'REFERENCE-C17','Interview',status_key)==status
        assert candidate.get('/api/invitations/'+item['id']+'/recruitment-stage').json()['stage']=='Interview';step('Return recruitment stage, repeated delivery idempotent')
        ats.close_need(external);step('Close outdated external need')
        candidate.post('/api/invitations/'+item['id']+'/ats-consent',json={'granted':False}).raise_for_status()
        assert not any(p['invitation_id']==item['id'] for p in ats.profiles());step('Stop future transfer after consent withdrawal')
        withdrawals=[e for e in ats.events()['items'] if e.get('withdrawal',{}).get('invitation_id')==item['id']]
        assert len(withdrawals)==1 and withdrawals[0]['status']=='awaiting_ack'
        del external_cache[item['id']]
        acknowledgement=ats.acknowledge_deletion(withdrawals[0]['id'],'Reference client removed its local copy')
        assert acknowledgement==ats.acknowledge_deletion(withdrawals[0]['id'],'Reference client removed its local copy') and item['id'] not in external_cache
        step('Read durable withdrawal, delete reference copy and acknowledge idempotently')
        employer.post('/api/ats-connections/'+key.json()['id']+'/revoke',json={}).raise_for_status()
        try:ats.profiles();raise AssertionError('revoked key accepted')
        except httpx.HTTPStatusError as error:assert error.response.status_code==401
        step('Reject revoked key')
    finally:ats.close()
report['checked_at']=time.strftime('%Y-%m-%d %H:%M:%S')
(ROOT/'artifacts/stage03-ats-http.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print('Reference ATS round trip passed:',len(report['steps']),'steps')
