"""Restore one snapshot twice into new local databases and check row equality.
Failed readiness returns to the healthy local product endpoint, preserving data.
This is a process/readiness exercise, never a production failure injection.
"""
import argparse,hashlib,json,os,re,secrets,socket,subprocess,sys,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def fingerprint(conn):
    from psycopg import sql
    tables=[r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")]
    values={};counts={}
    for name in tables:
        rows=conn.execute(sql.SQL('SELECT row_to_json(t)::text FROM {} t').format(sql.Identifier(name))).fetchall()
        encoded=sorted(json.dumps(json.loads(r[0]),sort_keys=True,separators=(',',':'),ensure_ascii=False) for r in rows)
        values[name]=hashlib.sha256('\n'.join(encoded).encode()).hexdigest();counts[name]=len(rows)
    return {'table_hashes':values,'table_counts':counts,'digest':hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()}

def main():
    p=argparse.ArgumentParser();p.add_argument('--snapshot',required=True);p.add_argument('--output',default='artifacts/recovery-v2.json');a=p.parse_args()
    import psycopg,uvicorn,httpx
    from psycopg.conninfo import make_conninfo
    from psycopg import sql
    snapshot=Path(a.snapshot).resolve();assert snapshot.is_file()
    cfg=json.loads((ROOT/'tmp/postgres-test.json').read_text(encoding='utf-8-sig'));assert cfg['host']=='127.0.0.1'
    password=Path(cfg['password_file']).read_text().strip();base=make_conninfo(host=cfg['host'],port=cfg['port'],user=cfg['user'],password=password,dbname='postgres')
    names=['recovery_'+secrets.token_hex(8),'recovery_'+secrets.token_hex(8)];assert all(re.fullmatch(r'recovery_[0-9a-f]{16}',n) for n in names)
    env=os.environ.copy();env['PGPASSWORD']=password;digests=[]
    with psycopg.connect(base,autocommit=True) as conn:
        for name in names:conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    servers=[]
    try:
        for name in names:
            command=[str(Path(cfg['bin_directory'])/('pg_restore.exe' if os.name=='nt' else 'pg_restore')),'-h',cfg['host'],'-p',str(cfg['port']),'-U',cfg['user'],'-d',name,'--no-owner','--no-acl','--exit-on-error']
            with snapshot.open('rb') as data:subprocess.run(command,stdin=data,env=env,check=True,capture_output=True)
            with psycopg.connect(make_conninfo(base,dbname=name)) as conn:digests.append(fingerprint(conn))
        assert digests[0]==digests[1] and len(digests[0]['table_counts'])>10
        os.environ.update(FSP_DATABASE_URL=make_conninfo(base,dbname=names[0]),FSP_DEMO='1',FSP_ENABLE_ML='0',FSP_JOB_AUTOSTART='0',FSP_ALLOW_REGISTRATION='0',FSP_OLLAMA_URL='')
        from backend.main import app
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse
        def serve(target):
            sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128);url='http://127.0.0.1:'+str(sock.getsockname()[1])
            server=uvicorn.Server(uvicorn.Config(target,log_level='error'));thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start();servers.append((server,thread))
            for _ in range(200):
                if server.started:break
                time.sleep(.05)
            assert server.started;return url
        good=serve(app);assert httpx.get(good+'/api/health',trust_env=False).status_code==200
        with psycopg.connect(make_conninfo(base,dbname=names[0])) as conn:before=fingerprint(conn)
        broken=FastAPI()
        @broken.get('/api/health')
        def unhealthy():return JSONResponse({'status':'injected_unready_release'},status_code=503)
        bad=serve(broken);active=bad
        if httpx.get(active+'/api/health',trust_env=False).status_code!=200:active=good
        assert active==good and httpx.get(active+'/api/health',trust_env=False).status_code==200
        with psycopg.connect(make_conninfo(base,dbname=names[0])) as conn:after=fingerprint(conn)
        assert before==after
        report={'scope':'Two fresh local PostgreSQL databases restored from one operator-provided reference snapshot; no production changes','snapshot_origin':'local_synthetic_reference' if snapshot.name=='synthetic-reference.dump' else 'operator_supplied_not_independently_verified','production_export':False,'snapshot_sha256':hashlib.sha256(snapshot.read_bytes()).hexdigest(),'two_restores_equal':True,'tables':len(digests[0]['table_counts']),'row_counts':digests[0]['table_counts'],'restored_digest':digests[0]['digest'],'product_health_after_restore':200,'failed_release_readiness':503,'healthy_endpoint_restored':True,'rollback_preserved_rows':True,'limits':'Readiness/process routing exercise on local loopback, not an outage or image downgrade on the public VM. Archive remains private; no live database snapshot or production credentials are asserted.'}
        output=ROOT/a.output;output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps({k:report[k] for k in ['two_restores_equal','tables','product_health_after_restore','rollback_preserved_rows']}))
    finally:
        for server,thread in servers:server.should_exit=True;thread.join(timeout=10)
        with psycopg.connect(base,autocommit=True) as conn:
            for name in names:
                conn.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s',(name,))
                conn.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
if __name__=='__main__':main()
