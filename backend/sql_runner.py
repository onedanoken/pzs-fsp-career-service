"""One disposable SQL process. No extensions, files, writes or network."""
import json,sqlite3,sys,os
sys.path.insert(0,os.path.dirname(__file__))
from child_limits import restrict
restrict(128,2)
payload=json.load(sys.stdin)
db=sqlite3.connect(':memory:')
db.execute('CREATE TABLE events(team TEXT,amount INTEGER,status TEXT)')
db.executemany('INSERT INTO events VALUES(?,?,?)',payload['rows'])
allowed={sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ,sqlite3.SQLITE_FUNCTION,sqlite3.SQLITE_RECURSIVE}
functions={'sum','count','max','min','avg','coalesce','ifnull','nullif','lower','upper','length','abs','round','typeof'}
def authorize(action,arg1,arg2,*rest):
    if action==sqlite3.SQLITE_FUNCTION and (arg1 or arg2 or '').lower() not in functions:return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY
db.set_authorizer(authorize)
steps=[0]
def progress():
    steps[0]+=1
    return int(steps[0]>2000)
db.set_progress_handler(progress,1000)
try:
    rows=db.execute(payload['query']).fetchmany(101)
    if len(rows)>100:raise ValueError('result limit')
    print(json.dumps({'ok':True,'rows':rows}))
except Exception:
    print(json.dumps({'ok':False,'rows':[]}))
