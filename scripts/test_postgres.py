"""PostgreSQL-only tests; supply FSP_TEST_POSTGRES_DSN securely."""
import os,subprocess,sys
from pathlib import Path
if not os.environ.get('FSP_TEST_POSTGRES_DSN'):
    raise SystemExit('Set FSP_TEST_POSTGRES_DSN to a disposable PostgreSQL UTF-8 database')
raise SystemExit(subprocess.run([sys.executable,'-X','utf8','-m','pytest','tests','-q','--tb=short',*sys.argv[1:]],cwd=Path(__file__).resolve().parents[1]).returncode)
