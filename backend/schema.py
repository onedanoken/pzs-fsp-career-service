"""Native PostgreSQL base schema; additive domain migrations follow."""
BASE_SCHEMAS = [
"""
CREATE TABLE IF NOT EXISTS organizations(id TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS candidates(id TEXT PRIMARY KEY, payload TEXT NOT NULL,
            visible INTEGER NOT NULL DEFAULT 1, version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL, password TEXT NOT NULL, role TEXT NOT NULL,
            candidate_id TEXT REFERENCES candidates(id), org_id TEXT REFERENCES organizations(id));
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
            csrf TEXT NOT NULL, expires DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS achievements(id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES candidates(id),
            payload TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS vacancies(id TEXT PRIMARY KEY, org_id TEXT REFERENCES organizations(id),
            payload TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS offers(id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES organizations(id),
            candidate_id TEXT NOT NULL REFERENCES candidates(id), vacancy_id TEXT NOT NULL REFERENCES vacancies(id),
            sender_id TEXT NOT NULL REFERENCES users(id), payload TEXT NOT NULL, status TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1, history TEXT NOT NULL, created_at TEXT NOT NULL,
            request_key TEXT NOT NULL, UNIQUE(sender_id,request_key));
CREATE TABLE IF NOT EXISTS bookmarks(user_id TEXT NOT NULL REFERENCES users(id),
            entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, PRIMARY KEY(user_id,entity_type,entity_id));
CREATE TABLE IF NOT EXISTS audit(id BIGSERIAL PRIMARY KEY, actor_id TEXT,
            action TEXT NOT NULL, entity_id TEXT, metadata TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS embeddings(cache_key TEXT PRIMARY KEY, vector BYTEA NOT NULL, dimension INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS reranks(cache_key TEXT PRIMARY KEY, score DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS imports(id TEXT PRIMARY KEY,actor_id TEXT NOT NULL,status TEXT NOT NULL,
            imported INTEGER NOT NULL DEFAULT 0,error TEXT,created_at TEXT NOT NULL,completed_at TEXT);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
INSERT OR IGNORE INTO meta VALUES('data_revision','1');
""",
"""
CREATE TABLE IF NOT EXISTS jobs(
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, actor_id TEXT,
                request_key TEXT, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
                result TEXT, checkpoint TEXT NOT NULL DEFAULT '{}',
                attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3,
                available_at DOUBLE PRECISION NOT NULL, lease_until DOUBLE PRECISION, worker_id TEXT,
                progress INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0,
                error_code TEXT, error TEXT, created_at TEXT NOT NULL, completed_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS jobs_dedupe ON jobs(COALESCE(actor_id,''),kind,request_key);
CREATE INDEX IF NOT EXISTS jobs_claim ON jobs(status,available_at,lease_until);
CREATE TABLE IF NOT EXISTS resume_documents(
                id TEXT PRIMARY KEY, actor_id TEXT NOT NULL REFERENCES users(id),
                candidate_id TEXT NOT NULL REFERENCES candidates(id), filename TEXT NOT NULL,
                sha256 TEXT NOT NULL, content BYTEA NOT NULL, extracted_text TEXT,
                draft TEXT, state TEXT NOT NULL DEFAULT 'queued', profile_version INTEGER NOT NULL,
                version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, approved_at TEXT,
                UNIQUE(actor_id,sha256,profile_version));
CREATE TABLE IF NOT EXISTS passports(
                id TEXT PRIMARY KEY, actor_id TEXT NOT NULL REFERENCES users(id), org_id TEXT,
                candidate_id TEXT NOT NULL REFERENCES candidates(id), vacancy_id TEXT NOT NULL REFERENCES vacancies(id),
                snapshot TEXT NOT NULL, fingerprint TEXT NOT NULL, integrity_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS passports_owner ON passports(actor_id,org_id);
"""
]
