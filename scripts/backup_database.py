"""SQLite consistent backup/restore; PostgreSQL uses pg_dump/pg_restore tooling."""
import argparse,hashlib,json,sqlite3,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def backup(source,target):
    source=source.resolve();target=target.resolve()
    if source==target or target.exists():raise ValueError('Use a new backup path, distinct from the source')
    target.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(target) as dst:src.backup(dst)
    with sqlite3.connect(target) as conn:
        check=conn.execute('PRAGMA integrity_check').fetchone()[0]
        if check!='ok':raise ValueError('Backup integrity check failed')
    return {'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'integrity':'ok','bytes':target.stat().st_size}
def restore(source,target):
    # Restoration always produces a new DB; swapping production is a release action.
    return backup(source,target)
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['backup','restore']);parser.add_argument('source',type=Path);parser.add_argument('target',type=Path);args=parser.parse_args()
    print(json.dumps((backup if args.action=='backup' else restore)(args.source,args.target)))
