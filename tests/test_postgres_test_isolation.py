import os
from backend import db
from scripts.postgres_test_context import postgres_test_schema

def test_context_isolates_an_already_imported_db_and_restores_captured_dsn():
    before=db.DATABASE_URL
    with postgres_test_schema():
        assert db.DATABASE_URL==os.environ['FSP_DATABASE_URL'] and db.DATABASE_URL!=before
        with db.connection() as conn:
            assert conn.execute('SELECT current_schema()').fetchone()[0].startswith('testbrowser_')
    assert db.DATABASE_URL==before
