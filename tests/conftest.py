import os
os.environ["FSP_ENABLE_ML"]="0"
os.environ['FSP_JOB_AUTOSTART']='0'
os.environ['FSP_DEMO']='1'
os.environ['FSP_TEST_LOGIN']='1'
os.environ['FSP_LEGACY_EMAIL_AUTH']='1'
# Keep frozen username-only accounts covered; email acceptance tests enable the new policy.
os.environ['FSP_EMAIL_REQUIRED']='0'
# Preserve legacy-route regression coverage; dedicated journey tests override this flag.
os.environ['FSP_ASSESSMENT_PATH']='legacy'
import pytest
from fastapi.testclient import TestClient
from backend import db
from backend.main import app
from backend.ml import engine

@pytest.fixture(autouse=True)
def isolated_rate_window():
    from backend.main import login_attempts
    login_attempts.clear()
    yield
    login_attempts.clear()

@pytest.fixture
def clients(tmp_path,monkeypatch):
    url=os.environ.get('FSP_TEST_POSTGRES_DSN')
    schema=None
    if url:
        import psycopg,secrets
        from psycopg.conninfo import make_conninfo
        schema='test_'+secrets.token_hex(10)
        with psycopg.connect(url,autocommit=True) as conn:
            if conn.execute('SHOW server_encoding').fetchone()[0]!='UTF8':raise RuntimeError('Test PostgreSQL database must use UTF8')
            conn.execute('CREATE SCHEMA '+schema)
        monkeypatch.setattr(db,'DATABASE_URL',make_conninfo(url,options='-c search_path='+schema))
    else:raise RuntimeError('Tests require isolated PostgreSQL: set FSP_TEST_POSTGRES_DSN')
    engine.state="disabled"
    with TestClient(app) as employer, TestClient(app) as candidate, TestClient(app) as expert:
        def sign(client, account):
            response=client.post("/api/auth/demo",json={"account":account})
            assert response.status_code==200,response.text
            client.headers["X-CSRF-Token"]=response.json()["csrf"]
            return response.json()
        sign(employer,"u_employer")
        sign(candidate,"u_anna")
        sign(expert,"u_admin")
        yield employer,candidate,expert,sign
    if schema:
        with psycopg.connect(url,autocommit=True) as conn:conn.execute('DROP SCHEMA '+schema+' CASCADE')
