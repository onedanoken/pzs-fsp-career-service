"""PostgreSQL-only adapter checkpoints, with an isolated namespace and writer lock."""
import hashlib,os
from pathlib import Path
from . import db
from .postgres import Connection
from .ats_odoo import AdapterError

class AdapterState:
    def __init__(self,config,test_connection=False):
        identity=config.get('state_id') or config.get('state_path')
        if not identity:raise AdapterError('configuration_error')
        if config.get('state_path') and Path(config['state_path']).exists():
            # Never discard an existing offline checkpoint or silently restart it.
            raise AdapterError('configuration_error')
        url=os.environ.get(config.get('state_database_env','FSP_ATS_STATE_DATABASE_URL'))
        if not url and test_connection:url=db.DATABASE_URL
        if not url:raise AdapterError('configuration_error')
        self.identity=hashlib.sha256(str(identity).encode()).hexdigest()
        self.conn=Connection(url)
        try:
            self.conn.execute('SELECT pg_advisory_xact_lock(9042618)')
            self.conn.execute('CREATE TABLE IF NOT EXISTS ats_adapter_meta(storage_key TEXT NOT NULL,key TEXT NOT NULL,value TEXT NOT NULL,PRIMARY KEY(storage_key,key))')
            self.conn.execute('CREATE TABLE IF NOT EXISTS ats_adapter_jobs(storage_key TEXT NOT NULL,external_id TEXT NOT NULL,job_id INTEGER NOT NULL,need_id TEXT NOT NULL,active INTEGER NOT NULL,PRIMARY KEY(storage_key,external_id))')
            self.conn.execute('CREATE TABLE IF NOT EXISTS ats_adapter_profiles(storage_key TEXT NOT NULL,invitation_id TEXT NOT NULL,reference TEXT NOT NULL,PRIMARY KEY(storage_key,invitation_id))')
            self.conn.commit()
            number=int(self.identity[:15],16)
            if not self.conn.execute('SELECT pg_try_advisory_xact_lock(?)',(number,)).fetchone()[0]:raise AdapterError('sync_failed')
        except Exception:
            self.conn.rollback();self.conn.close();raise

    def get_meta(self,key):
        row=self.conn.execute('SELECT value FROM ats_adapter_meta WHERE storage_key=? AND key=?',(self.identity,key)).fetchone()
        return row[0] if row else None
    def set_meta(self,key,value):
        self.conn.execute('INSERT INTO ats_adapter_meta VALUES(?,?,?) ON CONFLICT(storage_key,key) DO UPDATE SET value=excluded.value',(self.identity,key,value))
    def need(self,external):
        row=self.conn.execute('SELECT need_id FROM ats_adapter_jobs WHERE storage_key=? AND external_id=?',(self.identity,external)).fetchone()
        return row[0] if row else None
    def close_job(self,external):
        self.conn.execute('UPDATE ats_adapter_jobs SET active=0 WHERE storage_key=? AND external_id=?',(self.identity,external))
    def save_job(self,external,job,need):
        self.conn.execute('INSERT INTO ats_adapter_jobs VALUES(?,?,?,?,1) ON CONFLICT(storage_key,external_id) DO UPDATE SET need_id=excluded.need_id,active=1',(self.identity,external,job,need))
    def jobs(self):
        return [tuple(r[k] for k in ['external_id','job_id','need_id','active']) for r in self.conn.execute('SELECT external_id,job_id,need_id,active FROM ats_adapter_jobs WHERE storage_key=?',(self.identity,))]
    def profiles(self):
        return [(r['invitation_id'],r['reference']) for r in self.conn.execute('SELECT invitation_id,reference FROM ats_adapter_profiles WHERE storage_key=?',(self.identity,))]
    def remove_profile(self,invitation):
        self.conn.execute('DELETE FROM ats_adapter_profiles WHERE storage_key=? AND invitation_id=?',(self.identity,invitation))
    def save_profile(self,invitation,reference):
        self.conn.execute('INSERT INTO ats_adapter_profiles VALUES(?,?,?) ON CONFLICT(storage_key,invitation_id) DO UPDATE SET reference=excluded.reference',(self.identity,invitation,reference))
    def commit(self):self.conn.commit()
    def rollback(self):self.conn.rollback()
    def close(self):self.conn.close()
