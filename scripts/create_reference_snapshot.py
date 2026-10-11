"""Create/dump only a fresh local synthetic product database, never production."""
import argparse,hashlib,json,os,re,secrets,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
import psycopg
from psycopg.conninfo import make_conninfo
from psycopg import sql
cfg=json.loads((root/'tmp/postgres-test.json').read_text(encoding='utf-8-sig'));assert cfg['host']=='127.0.0.1'
password=Path(cfg['password_file']).read_text().strip();base=make_conninfo(host=cfg['host'],port=cfg['port'],user=cfg['user'],password=password,dbname='postgres')
parser=argparse.ArgumentParser();parser.add_argument('--output',default='tmp/recovery/synthetic-reference.dump');args=parser.parse_args()
target=Path(args.output).resolve();target.parent.mkdir(parents=True,exist_ok=True);folder=target.parent
if target.exists():raise ValueError('Choose a new output path; existing snapshots are not overwritten')
name='fixture_'+secrets.token_hex(8);assert re.fullmatch(r'fixture_[0-9a-f]{16}',name)
with psycopg.connect(base,autocommit=True) as conn:conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
try:
    os.environ.update(FSP_DATABASE_URL=make_conninfo(base,dbname=name),FSP_DEMO='1',FSP_ENABLE_ML='0',FSP_JOB_AUTOSTART='0',FSP_ALLOW_REGISTRATION='0',FSP_SMTP_HOST='',FSP_DELIVERY_API_URL='',FSP_OLLAMA_URL='',FSP_ASSESSMENT_PATH='work_sample_v2_auto')
    from fastapi.testclient import TestClient
    from backend.main import app
    from backend import db
    from backend.jobs import Worker
    import re
    with TestClient(app) as client:
        signed=client.post('/api/auth/demo',json={'account':'u_anna'}).json();client.headers['X-CSRF-Token']=signed['csrf']
        identity=client.post('/api/assessments',json={'specialization':'devops','grade':'Junior','mode':'certification'}).json()['id']
        worker=Worker()
        for _ in range(20):
            if not worker.run_once():break
        current=client.get('/api/assessments/'+identity).json()
        with db.connection() as conn:data=json.loads(conn.execute('SELECT payload FROM assessment_sessions WHERE id=?',(identity,)).fetchone()[0])
        for item in data['items']:
            if item['kind']=='explanation':continue
            answer=item['private']['reference_answer'] if item['kind']=='work_sample' else json.dumps(item['private']['expected']) if item['kind']=='multi' else str(item['private']['expected'])
            current=client.put('/api/assessments/'+identity+'/answer',json={'version':current['version'],'item_id':item['id'],'answer':answer}).json()
        assert client.post('/api/assessments/'+identity+'/finish',json={'version':current['version']}).status_code==202
        for _ in range(20):
            if not worker.run_once():break
        assert client.get('/api/assessments/'+identity).json()['result']['passed']
    env=os.environ.copy();env['PGPASSWORD']=password
    command=[str(Path(cfg['bin_directory'])/('pg_dump.exe' if os.name=='nt' else 'pg_dump')),'-h',cfg['host'],'-p',str(cfg['port']),'-U',cfg['user'],'-d',name,'-Fc','--no-owner','--no-acl']
    with target.open('wb') as data:subprocess.run(command,stdout=data,env=env,check=True)
    report={'origin':'fresh_local_synthetic_product_database','production_export':False,'bytes':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'schema':6,'contains_completed_auto_assessment_and_passport':True}
    (folder/'synthetic-backup.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
finally:
    with psycopg.connect(base,autocommit=True) as conn:conn.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
