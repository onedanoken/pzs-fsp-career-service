"""Explicit offline migration of an old adapter checkpoint, preserving its source."""
import argparse,hashlib,sqlite3,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.ats_state import AdapterState

def transfer(source,state_id,connection_id,target_env='FSP_ATS_STATE_DATABASE_URL'):
    source=source.resolve();before=hashlib.sha256(source.read_bytes()).hexdigest()
    target=None
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as old:
        meta=dict(old.execute('SELECT key,value FROM meta'))
        if meta.get('namespace')!=connection_id:raise ValueError('Connection ID does not match checkpoint')
        target=AdapterState({'state_id':state_id,'state_database_env':target_env})
        try:
            if target.get_meta('namespace') or target.jobs() or target.profiles():raise ValueError('Target checkpoint must be empty')
            for key in ['namespace','events_cursor']:
                if key in meta:target.set_meta(key,meta[key])
            jobs=old.execute('SELECT external_id,job_id,need_id,active FROM jobs').fetchall()
            profiles=old.execute('SELECT invitation_id,reference FROM profiles').fetchall()
            for external,job,need,active in jobs:
                target.save_job(external,job,need)
                if not active:target.close_job(external)
            for invitation,reference in profiles:target.save_profile(invitation,reference)
            if hashlib.sha256(source.read_bytes()).hexdigest()!=before:raise ValueError('Source changed during import')
            target.commit();return {'jobs':len(jobs),'profiles':len(profiles),'source_unchanged':True}
        except Exception:target.rollback();raise
        finally:target.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',required=True,type=Path)
    parser.add_argument('--state-id',required=True);parser.add_argument('--connection-id',required=True)
    args=parser.parse_args()
    result=transfer(args.source,args.state_id,args.connection_id)
    print('Imported checkpoint counts:',result['jobs'],result['profiles'],'source unchanged:',result['source_unchanged'])
