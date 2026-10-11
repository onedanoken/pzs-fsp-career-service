import json,random
from urllib.parse import urlparse,parse_qs
import httpx
from backend import db
from backend.neural_provider import neural_variant
from backend.delivery_gateway import signature
from tests.test_universal_delivery import provider

def test_oidc_real_http_pkce_link_registry_and_revocation(clients,monkeypatch):
    employer,candidate,admin,_=clients
    with provider(monkeypatch) as (app,key,url):
        app.state.redirect_uri='http://testserver/api/fsp/oidc/callback'
        monkeypatch.setenv('FSP_PUBLIC_URL','http://testserver');monkeypatch.setenv('FSP_OIDC_ISSUER',url);monkeypatch.setenv('FSP_OIDC_CLIENT_ID','fsp-reference');monkeypatch.setenv('FSP_OIDC_DEMO_ISSUER','1');monkeypatch.setenv('FSP_OIDC_PARTICIPANT_CLAIM','fsp_id')
        start=candidate.get('/api/fsp/oidc/start');assert start.status_code==200,start.text
        response=httpx.get(start.json()['url'],follow_redirects=False,trust_env=False);assert response.status_code==307
        callback=response.headers['location'];linked=candidate.get(callback,follow_redirects=False);assert linked.status_code==303,linked.text
        assert candidate.get(callback,follow_redirects=False).status_code==400
        identity=candidate.get('/api/fsp/identities').json()['items'][0];assert identity['status']=='demo'
        achievement={'external_id':'reference-achievement-1','identity_id':identity['id'],'title':'Reference individual result','contest':'Local conformance fixture','date':'2026-10-07','result':'Synthetic protocol evidence','individual_evidence':True,'task_skills':['python'],'source_url':url+'/registry/reference'}
        assert employer.post('/api/admin/fsp/registry/achievements',json=achievement).status_code==403
        imported=admin.post('/api/admin/fsp/registry/achievements',json=achievement);assert imported.status_code==201 and imported.json()['demo']
        monkeypatch.setenv('FSP_REGISTRY_API_KEY',key);monkeypatch.setenv('FSP_REGISTRY_ISSUER',url)
        entry={k:v for k,v in achievement.items() if k!='identity_id'};entry['external_id']='signed-registry-2'
        event={'event_id':'registry-event-001','issuer':url,'participant_id':'FSP-REFERENCE-001','achievement':entry}
        import time
        raw=json.dumps(event,separators=(',',':')).encode();stamp=str(int(time.time()));headers={'Content-Type':'application/json','X-FSP-Timestamp':stamp,'X-FSP-Signature':signature(key,stamp,raw)}
        signed=candidate.post('/api/integration-events/fsp-achievements',content=raw,headers=headers);assert signed.status_code==200,signed.text
        assert signed.json()['demo'] and not signed.json()['duplicate']
        assert candidate.post('/api/integration-events/fsp-achievements',content=raw,headers=headers).json()['duplicate']
        assert candidate.post('/api/integration-events/fsp-achievements',content=raw,headers={'X-FSP-Timestamp':stamp,'X-FSP-Signature':'invalid'}).status_code==401
        assert candidate.post('/api/fsp/identities/'+identity['id']+'/revoke',json={}).status_code==200
        assert admin.post('/api/admin/fsp/registry/achievements',json=achievement).status_code==409
        assert candidate.post('/api/integration-events/fsp-achievements',content=raw,headers=headers).status_code==409
        with db.connection() as conn:assert conn.execute('SELECT active FROM achievements WHERE id=?',(imported.json()['id'],)).fetchone()[0]==0

def test_generator_universal_contract_is_explicitly_a_mock(clients,monkeypatch):
    with provider(monkeypatch) as (app,key,url):
        monkeypatch.setenv('FSP_OLLAMA_URL',url);monkeypatch.setenv('FSP_GENERATOR_PROTOCOL','fsp-json-v1');monkeypatch.setenv('FSP_GENERATOR_TOKEN',key)
        variant,metadata=neural_variant('backend','Junior',random.Random(8))
        assert variant is not None and metadata['generator']=='reference_protocol_simulator'
        assert metadata['neural_items']==0 and 'not_real' in metadata['generator_scope']
