"""Fetch a dated public snapshot or refresh an explicitly configured database."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.live_vacancies import fetch,import_items
p=argparse.ArgumentParser();p.add_argument('--query',default='Python');p.add_argument('--limit',type=int,default=100);p.add_argument('--snapshot',type=Path);p.add_argument('--apply',action='store_true');args=p.parse_args()
if not 1<=args.limit<=100:p.error('limit must be 1..100')
items,metadata=fetch(args.query,args.limit)
if args.snapshot:args.snapshot.parent.mkdir(parents=True,exist_ok=True);args.snapshot.write_text(json.dumps({'metadata':metadata,'items':items},ensure_ascii=False,indent=2),encoding='utf-8')
if args.apply:
    from backend import db
    db.initialize();metadata=import_items(items,metadata)
print(json.dumps(metadata,ensure_ascii=True))
