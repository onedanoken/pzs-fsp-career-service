"""Disposable PostgreSQL schema for local browser acceptance, never app fallback."""
import os,secrets,sys
from contextlib import contextmanager
import psycopg
from psycopg.conninfo import make_conninfo

@contextmanager
def postgres_test_schema():
    url=os.environ.get('FSP_TEST_POSTGRES_DSN')
    if not url:raise RuntimeError('Set FSP_TEST_POSTGRES_DSN to an isolated PostgreSQL UTF-8 test database')
    schema='testbrowser_'+secrets.token_hex(10)
    with psycopg.connect(url,autocommit=True) as conn:
        if conn.execute('SHOW server_encoding').fetchone()[0]!='UTF8':raise RuntimeError('Test database must use UTF8')
        conn.execute('CREATE SCHEMA '+schema)
    old=os.environ.get('FSP_DATABASE_URL')
    isolated=make_conninfo(url,options='-c search_path='+schema)
    os.environ['FSP_DATABASE_URL']=isolated
    # app/db may already be imported by an API runner. Changing only the
    # environment then silently uses its cached parent DSN instead of this schema.
    loaded=sys.modules.get('backend.db')
    cached=loaded.DATABASE_URL if loaded is not None else None
    if loaded is not None:loaded.DATABASE_URL=isolated
    try:
        with psycopg.connect(isolated) as conn:
            if conn.execute('SELECT current_schema()').fetchone()[0]!=schema:
                raise RuntimeError('Disposable schema isolation failed')
        yield
    finally:
        if loaded is not None:loaded.DATABASE_URL=cached
        if old is None:os.environ.pop('FSP_DATABASE_URL',None)
        else:os.environ['FSP_DATABASE_URL']=old
        with psycopg.connect(url,autocommit=True) as conn:conn.execute('DROP SCHEMA '+schema+' CASCADE')
