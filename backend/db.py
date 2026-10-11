import contextlib
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
DEMO = os.environ.get("FSP_DEMO", "0") == "1"
DATABASE_URL = os.environ.get('FSP_DATABASE_URL','')

def now():
    return datetime.now(timezone.utc).isoformat()

def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

class IntegrityError(Exception):
    """Database constraint conflict, without leaking SQL or credentials."""

@contextlib.contextmanager
def connection():
    if not DATABASE_URL:
        raise RuntimeError('PostgreSQL required: configure FSP_DATABASE_URL; no local database fallback')
    from .postgres import Connection
    conn=Connection(DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:conn.close()


def initialize():
    from .postgres import initialize as initialize_postgres
    with connection() as conn:initialize_postgres(conn,ROOT)


def audit(conn, actor, action, entity, metadata=None):
    conn.execute("INSERT INTO audit(actor_id,action,entity_id,metadata,created_at) VALUES(?,?,?,?,?)",
                 (actor,action,entity,dumps(metadata or {}),now()))

def bump(conn):
    conn.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='data_revision'")

def revision():
    with connection() as conn:
        return int(conn.execute("SELECT value FROM meta WHERE key='data_revision'").fetchone()[0])

def password_hash(password, salt=None):
    salt = salt or os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 240_000).hex()
    return salt + ":" + digest
