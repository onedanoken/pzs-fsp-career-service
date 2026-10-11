"""Bounded document child: no user code, fixed imports only."""
import base64,json,os,sys
sys.path.insert(0,os.path.dirname(os.path.dirname(__file__)))
from backend.child_limits import restrict
restrict(384,8)
from backend.resumes import extract_text
from backend.jobs import JobError
try:
    payload=json.load(sys.stdin)
    text=extract_text(base64.b64decode(payload['content']),payload['filename'])
    print(json.dumps({'text':text},ensure_ascii=True))
except JobError as exc:
    print(json.dumps({'error_code':exc.code,'error':str(exc)},ensure_ascii=True))
