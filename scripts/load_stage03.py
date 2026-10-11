"""Small synthetic HTTP smoke load. Reports scope and cannot prove market scale."""
import asyncio,json,os,statistics,time
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1];URL=os.environ.get('FSP_TEST_URL','http://127.0.0.1:8040')
async def main():
    async with httpx.AsyncClient(base_url=URL,trust_env=False,timeout=30) as client:
        me=(await client.post('/api/auth/demo',json={'account':'u_employer'})).json();client.headers['X-CSRF-Token']=me['csrf']
        need=(await client.post('/api/needs',json={'title':'Load smoke API','description':'Maintain reliable integrations and product APIs','team':'Infrastructure product development','specialization':'backend','grade':'Middle','skills':['python','sql'],'salary_min':120000,'salary_max':180000,'employer_contact':'hr@example.org'})).json()['id']
        semaphore=asyncio.Semaphore(40);samples=[];errors=[]
        async def request(index):
            async with semaphore:
                start=time.perf_counter()
                response=await (client.post('/api/search-sessions',json={'need_id':need}) if index%3==0 else client.get('/api/candidates/c_anna') if index%3==1 else client.get('/api/needs'))
                samples.append((time.perf_counter()-start)*1000)
                if response.status_code>=400:errors.append(response.status_code)
        started=time.perf_counter();await asyncio.gather(*(request(i) for i in range(120)));duration=time.perf_counter()-started
        values=sorted(samples)
        report={'scope':'Windows localhost SQLite; tiny synthetic catalog; no neural generation under load','concurrency':40,'requests':120,'duration_seconds':round(duration,2),'p50_ms':round(statistics.median(values),2),'p95_ms':round(values[int(.95*(len(values)-1))],2),'errors':errors,'does_not_prove':'Large data, production infrastructure, fairness or final performance budget'}
        (ROOT/'artifacts/stage03-load.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report));assert not errors
asyncio.run(main())
