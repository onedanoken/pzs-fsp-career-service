"""Additive account-control schema; old attempts and consent snapshots are untouched."""
from .db import now

def migrate_accounts(conn, postgres=True):
    first=not conn.execute('SELECT 1 FROM schema_migrations WHERE version=7').fetchone()
    columns={'username':'TEXT','active':'INTEGER NOT NULL DEFAULT 1','admin_access':"TEXT NOT NULL DEFAULT 'none'",'account_version':'INTEGER NOT NULL DEFAULT 1','auth_kind':"TEXT NOT NULL DEFAULT 'legacy'",'created_at':'TEXT','last_login_at':'TEXT','password_change_required':'INTEGER NOT NULL DEFAULT 0','is_synthetic':'INTEGER NOT NULL DEFAULT 0'}
    existing=set() if postgres else {r[1] for r in conn.execute('PRAGMA table_info(users)')}
    for name,definition in columns.items():
        if postgres or name not in existing:
            conn.execute('ALTER TABLE users ADD COLUMN '+('IF NOT EXISTS ' if postgres else '')+name+' '+definition)
    conn.execute('UPDATE users SET username=id WHERE username IS NULL')
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS users_username ON users(username)')
    if first:conn.execute("UPDATE users SET admin_access='operator' WHERE role='admin' AND admin_access='none'")
    conn.execute("UPDATE users SET is_synthetic=1 WHERE email LIKE '%.demo' OR id IN ('u_anna','u_timur','u_admin','u_employer','u_employer2')")
    conn.execute('CREATE TABLE IF NOT EXISTS account_recovery(hash TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),expires '+('DOUBLE PRECISION' if postgres else 'REAL')+' NOT NULL,created_at TEXT NOT NULL,consumed_at TEXT)')
    conn.execute('CREATE INDEX IF NOT EXISTS account_recovery_owner ON account_recovery(user_id)')
    conn.execute('INSERT OR IGNORE INTO schema_migrations VALUES(7,?)',(now(),))
