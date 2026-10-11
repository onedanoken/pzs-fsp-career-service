"""Small DB-API compatibility boundary for the existing modular service.

Only application-owned SQL is translated. User data always stays in bound
parameters. PostgreSQL is the only application store.
"""
import re
from .db import IntegrityError
import psycopg
from psycopg.rows import dict_row
from psycopg.pq import TransactionStatus

class Row(dict):
    def __getitem__(self,key):return list(self.values())[key] if isinstance(key,int) else super().__getitem__(key)
class Cursor:
    def __init__(self,cursor):self.cursor=cursor
    @property
    def rowcount(self):return self.cursor.rowcount
    def fetchone(self):
        row=self.cursor.fetchone();return Row(row) if row is not None else None
    def fetchall(self):return [Row(row) for row in self.cursor.fetchall()]
    def __iter__(self):return (Row(row) for row in self.cursor)

def translate(sql):
    sql=sql.strip().rstrip(';')
    sql=re.sub(r'json_extract\((\w+(?:\.\w+)?),\s*\'\$\.([\w]+)\'\)',lambda m:'('+m[1]+"::jsonb->>'"+m[2]+"')",sql)
    sql=sql.replace("(payload::jsonb->>'demo')=0","(payload::jsonb->>'demo')='false'")
    if sql.startswith('INSERT OR IGNORE '):sql=sql.replace('INSERT OR IGNORE ','INSERT ',1)+' ON CONFLICT DO NOTHING'
    if sql.startswith('INSERT OR REPLACE '):
        table=re.search(r'INTO (\w+)',sql)[1]
        columns={'meta':('key',['value']),'embeddings':('cache_key',['vector','dimension']),'reranks':('cache_key',['score'])}
        key,values=columns[table]
        sql=sql.replace('INSERT OR REPLACE ','INSERT ',1)+' ON CONFLICT('+key+') DO UPDATE SET '+','.join(v+'=excluded.'+v for v in values)
    # Replace placeholders outside quoted SQL strings/identifiers.
    sql=sql.replace('%','%%')
    return re.sub(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|\?",lambda m:'%s' if m[0]=='?' else m[0],sql)

class Connection:
    def __init__(self,url):self.raw=psycopg.connect(url,row_factory=dict_row,connect_timeout=10)
    @property
    def in_transaction(self):return self.raw.info.transaction_status!=TransactionStatus.IDLE
    def execute(self,sql,parameters=()):
        if sql.strip()=='BEGIN IMMEDIATE':
            if not self.in_transaction:self.raw.execute('BEGIN')
            return Cursor(self.raw.execute('SELECT pg_advisory_xact_lock(9042603)'))
        try:return Cursor(self.raw.execute(translate(sql),tuple(int(v) if isinstance(v,bool) else v for v in parameters)))
        except psycopg.IntegrityError as exc:raise IntegrityError('Database integrity constraint') from exc
    def commit(self):self.raw.commit()
    def rollback(self):self.raw.rollback()
    def close(self):self.raw.close()

def initialize(conn,root):
    from .migrations import SCHEMA
    from .schema import BASE_SCHEMAS
    scripts=BASE_SCHEMAS
    for script in scripts+[SCHEMA]:
        for statement in script.split(';'):
            statement=statement.strip()
            if not statement or statement.startswith(('PRAGMA','CREATE VIRTUAL')):continue
            statement=re.sub(r'INTEGER PRIMARY KEY AUTOINCREMENT','BIGSERIAL PRIMARY KEY',statement)
            statement=re.sub(r'\bBLOB\b','BYTEA',statement)
            statement=re.sub(r'\bREAL\b','DOUBLE PRECISION',statement)
            conn.execute(statement)
        if script==scripts[0]:
            conn.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified INTEGER NOT NULL DEFAULT 0')
            conn.execute('CREATE TABLE IF NOT EXISTS search_index(entity_id TEXT NOT NULL,entity_type TEXT NOT NULL,text TEXT NOT NULL,PRIMARY KEY(entity_id,entity_type))')
            conn.execute("CREATE INDEX IF NOT EXISTS search_index_text ON search_index USING GIN(to_tsvector('simple',text))")
    from .accounts_schema import migrate_accounts
    migrate_accounts(conn,postgres=True)
    conn.execute("INSERT INTO meta VALUES('schema_version','7') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    conn.execute('INSERT INTO schema_migrations VALUES(5,?) ON CONFLICT(version) DO NOTHING',(__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),))
    conn.execute('INSERT INTO schema_migrations VALUES(6,?) ON CONFLICT(version) DO NOTHING',(__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),))
