"""Run a vendor adapter from private configuration; credentials are env names."""
import argparse
import json
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.ats_bridge import synchronize
from backend.ats_odoo import AdapterError

parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True);parser.add_argument('--interval',type=int,default=0)
args=parser.parse_args()
if args.interval and not 60<=args.interval<=3600:parser.error('interval must be 0 or 60-3600 seconds')
while True:
    status=0
    try:
        config=json.loads(args.config.read_text(encoding='utf-8'))
        config['state_path']=str((args.config.parent/config['state_path']).resolve())
        result=synchronize(config);print(json.dumps(result),flush=True)
    except (AdapterError,ValueError,KeyError,OSError) as error:
        status=1;print(json.dumps({'status':'failed','error_code':getattr(error,'code','configuration_error')}),flush=True)
    if not args.interval:raise SystemExit(status)
    try:time.sleep(args.interval)
    except KeyboardInterrupt:raise SystemExit(0)
