"""Sustained HTTP workload on a named disposable local PostgreSQL schema.

Never targets the public service. Creates controlled fake profiles and captures
actual timings, errors and application-process resources without guessed SLA.
"""
import argparse,concurrent.futures,copy,json,os,secrets,socket,statistics,sys,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def rss_bytes():
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD)]+[(name,ctypes.c_size_t) for name in ['peak','working','paged_peak','paged','nonpaged_peak','nonpaged','pagefile','pagefile_peak']]
        value=Counters();value.cb=ctypes.sizeof(value)
        function=ctypes.windll.psapi.GetProcessMemoryInfo;function.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
        if not function(wintypes.HANDLE(-1),ctypes.byref(value),value.cb):raise OSError('Cannot measure own process RSS')
        return value.working
    import resource
    value=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform=='darwin' else value*1024

def main():
    p=argparse.ArgumentParser();p.add_argument('--profiles',type=int,default=1200);p.add_argument('--seconds',type=int,default=60);p.add_argument('--concurrency',type=int,default=4);p.add_argument('--output',default='artifacts/postgres-load-v2.json');a=p.parse_args()
    assert 100<=a.profiles<=20000 and 10<=a.seconds<=600 and 1<=a.concurrency<=16
    import psycopg,uvicorn,httpx
    from psycopg.conninfo import make_conninfo
    from psycopg import sql
    cfg=json.loads((ROOT/'tmp/postgres-test.json').read_text(encoding='utf-8-sig'));assert cfg['host']=='127.0.0.1'
    password=Path(cfg['password_file']).read_text().strip();base=make_conninfo(host=cfg['host'],port=cfg['port'],user=cfg['user'],password=password,dbname='postgres');schema='load_'+secrets.token_hex(8)
    with psycopg.connect(base,autocommit=True) as conn:conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128);url='http://127.0.0.1:'+str(sock.getsockname()[1])
    os.environ.update(FSP_DATABASE_URL=make_conninfo(base,options='-c search_path='+schema),FSP_DEMO='1',FSP_ENABLE_ML='0',FSP_ALLOW_REGISTRATION='0',FSP_JOB_AUTOSTART='0',FSP_OLLAMA_URL='',FSP_ALLOWED_ORIGINS=url,FSP_PUBLIC_URL=url,FSP_SMTP_HOST='',FSP_DELIVERY_API_URL='')
    from backend import db
    from backend.main import app
    server=uvicorn.Server(uvicorn.Config(app,log_level='error'));thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    try:
        for _ in range(200):
            if server.started:break
            time.sleep(.1)
        assert server.started
        with db.connection() as conn:
            template=json.loads(conn.execute("SELECT payload FROM candidates WHERE id='c_anna'").fetchone()[0]);user=dict(conn.execute("SELECT * FROM users WHERE id='u_anna'").fetchone())
            for i in range(a.profiles):
                identity='load_candidate_'+str(i);uid='load_user_'+str(i);profile=copy.deepcopy(template);profile.update(id=identity,name='Synthetic profile '+str(i),role='backend',all_skills=['python','sql'],salary_min=80000+(i%5)*10000,demo=True,contacts={'email':uid+'@example.org'},initials='LP')
                conn.execute('INSERT INTO candidates(id,payload,visible) VALUES(?,?,1)',(identity,db.dumps(profile)))
                conn.execute('INSERT INTO users(id,role,name,email,password,candidate_id,email_verified) VALUES(?,?,?,?,?,?,1)',(uid,'candidate',profile['name'],uid+'@example.org',user['password'],identity))
                for kind in ['processing','publication']:conn.execute('INSERT INTO consent_events VALUES(?,?,?,?,?,?)',('load_consent_'+kind+str(i),identity,kind,1,'reference',db.now()))
                conn.execute('INSERT INTO grade_certifications VALUES(?,?,?,?,?,NULL,?,?,?,1)',('load_cert_'+str(i),identity,'backend','Middle',70+i%31,'provisional',db.dumps({'calibration':'synthetic_fixture','demo':True,'tested_skills':['python','sql']}),db.now()))
        setup=httpx.Client(base_url=url,trust_env=False,timeout=60);login=setup.post('/api/auth/demo',json={'account':'u_employer'});login.raise_for_status();csrf=login.json()['csrf']
        need=setup.post('/api/needs',headers={'X-CSRF-Token':csrf},json={'title':'Isolated load reference','description':'Maintain reliable product APIs and database contracts','team':'Local controlled performance experiment','specialization':'backend','grade':'Middle','skills':['python','sql'],'salary_min':80000,'salary_max':180000,'employer_contact':'fake-hr@example.org'});need.raise_for_status();need_id=need.json()['id'];cookie=dict(setup.cookies)
        cpu_before=os.times();peak=rss_bytes();latencies=[];errors=[];lock=threading.Lock();started=time.perf_counter();deadline=started+a.seconds
        def workload(index):
            with httpx.Client(base_url=url,trust_env=False,timeout=60,cookies=cookie,headers={'X-CSRF-Token':csrf}) as client:
                count=0
                while time.perf_counter()<deadline:
                    begin=time.perf_counter()
                    try:
                        r=client.post('/api/search-sessions',json={'need_id':need_id}) if (count+index)%10==0 else client.get('/api/needs') if count%2 else client.get('/api/candidates/c_anna')
                        with lock:
                            latencies.append((time.perf_counter()-begin)*1000)
                            if r.status_code>=400:errors.append({'status':r.status_code,'route':r.request.url.path})
                    except httpx.HTTPError as error:
                        with lock:errors.append({'exception':type(error).__name__})
                    count+=1
        with concurrent.futures.ThreadPoolExecutor(max_workers=a.concurrency) as pool:
            futures=[pool.submit(workload,i) for i in range(a.concurrency)]
            while any(not f.done() for f in futures):peak=max(peak,rss_bytes());time.sleep(.25)
            for f in futures:f.result()
        duration=time.perf_counter()-started;cpu_after=os.times();ordered=sorted(latencies)
        report={'scope':'Isolated Windows application process + local PostgreSQL16, controlled synthetic profiles; not public production workload','profiles':a.profiles,'concurrency':a.concurrency,'requested_seconds':a.seconds,'actual_seconds':round(duration,2),'requests':len(latencies),'requests_per_second':round(len(latencies)/duration,2),'p50_ms':round(statistics.median(ordered),2),'p95_ms':round(ordered[int(.95*(len(ordered)-1))],2),'max_ms':round(max(ordered),2),'application_peak_rss_mib':round(peak/1024**2,2),'application_cpu_seconds':round(cpu_after.user+cpu_after.system-cpu_before.user-cpu_before.system,2),'errors':errors,'ml':'disabled; no neural latency claims','limits':'Bounded result pages with batched ranking inputs; timings describe this machine/catalogue/mix, not all hardware or a market-scale SLA.'}
        path=ROOT/a.output;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report));assert not errors
    finally:
        server.should_exit=True;thread.join(timeout=10)
        with psycopg.connect(base,autocommit=True) as conn:conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
if __name__=='__main__':main()
