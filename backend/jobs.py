"""Durable at-least-once jobs with leases, fencing and resumable handlers."""
import json,os,secrets,threading,time,logging
from .db import connection,dumps,now

log=logging.getLogger(__name__)

class JobError(Exception):
    def __init__(self,code,message,retryable=False):
        super().__init__(message);self.code=code;self.retryable=retryable

def enqueue(kind,payload,actor_id=None,request_key=None,max_attempts=3,conn=None,job_id=None):
    if conn is None:
        with connection() as transaction:
            return enqueue(kind,payload,actor_id,request_key,max_attempts,transaction,job_id)
    if request_key:
        existing=conn.execute("SELECT * FROM jobs WHERE COALESCE(actor_id,'')=? AND kind=? AND request_key=?",(actor_id or '',kind,request_key)).fetchone()
        if existing:
            if json.loads(existing['payload'])!=payload:
                raise JobError('idempotency_conflict','Ключ запроса использован с другими данными')
            return existing['id']
    identity=job_id or 'job_'+secrets.token_hex(10)
    conn.execute("INSERT INTO jobs(id,kind,actor_id,request_key,payload,max_attempts,available_at,created_at) VALUES(?,?,?,?,?,?,?,?)",
                 (identity,kind,actor_id,request_key,dumps(payload),max_attempts,time.time(),now()))
    return identity

def public_job(row):
    result=dict(row)
    for key in ['payload','worker_id','lease_until','checkpoint','request_key']:
        result.pop(key,None)
    result['result']=json.loads(row['result']) if row['result'] else None
    return result


def recover_assessment(conn,job):
    """A terminal worker error must preserve answers and never assign a grade."""
    if job['kind'] not in {'assessment.generate','assessment.grade'}:return
    identity=json.loads(job['payload'])['session_id']
    row=conn.execute('SELECT * FROM assessment_sessions WHERE id=?',(identity,)).fetchone()
    expected='generating' if job['kind']=='assessment.generate' else 'grading'
    if not row or row['status']!=expected:return
    payload=json.loads(row['payload'])
    payload['technical_error']='Обработка прервана. Ответы сохранены; технический сбой не меняет грейд. Повторите проверку или начните новую сессию.'
    state='cancelled' if expected=='generating' else 'active'
    conn.execute('UPDATE assessment_sessions SET status=?,payload=?,version=version+1,expires=? WHERE id=?',(state,dumps(payload),time.time()+2700,identity))

def claim(worker_id,lease_seconds=30):
    timestamp=time.time()
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        # A crash on the final attempt must become a visible terminal failure.
        interrupted=conn.execute("SELECT * FROM jobs WHERE status='running' AND lease_until<? AND attempts>=max_attempts AND kind IN ('assessment.generate','assessment.grade')",(timestamp,)).fetchall()
        conn.execute("UPDATE jobs SET status='failed',error_code='worker_interrupted',error='Обработка прервана; лимит попыток исчерпан',completed_at=? WHERE status='running' AND lease_until<? AND attempts>=max_attempts",(now(),timestamp))
        for job in interrupted:recover_assessment(conn,job)
        conn.execute("UPDATE imports SET status='failed',error='Обработка прервана',completed_at=? WHERE id IN (SELECT id FROM jobs WHERE kind='hh.import' AND status='failed') AND status IN ('queued','running')",(now(),))
        conn.execute("UPDATE resume_documents SET state='failed' WHERE state='queued' AND id IN (SELECT json_extract(payload,'$.document_id') FROM jobs WHERE kind='resume.parse' AND status='failed')")
        row=conn.execute("SELECT * FROM jobs WHERE attempts<max_attempts AND ((status IN ('queued','retry') AND available_at<=?) OR (status='running' AND lease_until<?)) ORDER BY CASE kind WHEN 'resume.parse' THEN 0 WHEN 'hh.import' THEN 1 ELSE 2 END,available_at,created_at LIMIT 1",(timestamp,timestamp)).fetchone()
        if not row:return None
        conn.execute("UPDATE jobs SET status='running',worker_id=?,lease_until=?,attempts=attempts+1 WHERE id=?",(worker_id,timestamp+lease_seconds,row['id']))
        return dict(conn.execute('SELECT * FROM jobs WHERE id=?',(row['id'],)).fetchone())

class Context:
    def __init__(self,job):self.job=job;self.id=job['id'];self.worker_id=job['worker_id']
    def check(self,conn):
        row=conn.execute('SELECT status,worker_id,lease_until FROM jobs WHERE id=?',(self.id,)).fetchone()
        if not row or row['status']!='running' or row['worker_id']!=self.worker_id or row['lease_until']<time.time():
            raise JobError('lease_lost','Задание передано другому обработчику')
    def progress(self,completed,total,checkpoint=None,conn=None):
        if conn is None:
            with connection() as transaction:return self.progress(completed,total,checkpoint,transaction)
        conn.execute('BEGIN IMMEDIATE') if not conn.in_transaction else None
        self.check(conn)
        conn.execute('UPDATE jobs SET progress=?,total=?,checkpoint=? WHERE id=?',(completed,total,dumps(checkpoint or {}),self.id))

def dispatch(context):
    payload=json.loads(context.job['payload'])
    if context.job['kind']=='mail.send':
        from .identity import send_mail
        return send_mail(context,payload)
    if context.job['kind'] in {'assessment.generate','assessment.grade'}:
        from .assessments import process_generation,process_grading
        return (process_generation if context.job['kind']=='assessment.generate' else process_grading)(context,payload)
    if context.job['kind']=='resume.parse':
        from .resumes import process_document
        return process_document(context,payload)
    if context.job['kind']=='hh.import':
        from .integrations import process_headhunter
        return process_headhunter(context,payload)
    if context.job['kind']=='trud.import':
        from .live_vacancies import process
        return process(context,payload)
    if context.job['kind']=='search.warm':
        from .domain import warm_document
        return warm_document(payload)
    raise JobError('unknown_job','Неизвестный вид задания')

def execute(job,handler=dispatch,lease_seconds=30):
    context=Context(job);heartbeat_stop=threading.Event()
    def heartbeat():
        while not heartbeat_stop.wait(max(.1,lease_seconds/3)):
            try:
                with connection() as conn:
                    conn.execute("UPDATE jobs SET lease_until=? WHERE id=? AND worker_id=? AND status='running'",(time.time()+lease_seconds,context.id,context.worker_id))
            except Exception:log.exception('Job heartbeat failed')
    keeper=threading.Thread(target=heartbeat,daemon=True);keeper.start()
    try:
        result=handler(context)
        with connection() as conn:
            conn.execute('BEGIN IMMEDIATE');context.check(conn)
            conn.execute("UPDATE jobs SET status='completed',result=?,error=NULL,error_code=NULL,completed_at=?,lease_until=NULL,worker_id=NULL WHERE id=?",(dumps(result),now(),context.id))
            if job['kind']=='hh.import':conn.execute("UPDATE imports SET status='completed',completed_at=?,error=NULL WHERE id=?",(now(),context.id))
    except Exception as exc:
        code=exc.code if isinstance(exc,JobError) else 'processing_error'
        retryable=isinstance(exc,JobError) and exc.retryable
        retry=retryable and job['attempts']<job['max_attempts']
        message=str(exc)[:250] if isinstance(exc,JobError) else 'Не удалось обработать задание'
        if not isinstance(exc,JobError):log.exception('Job %s failed',context.id)
        with connection() as conn:
            changed=conn.execute("UPDATE jobs SET status=?,available_at=?,error_code=?,error=?,completed_at=?,lease_until=NULL,worker_id=NULL WHERE id=? AND worker_id=? AND status='running'",
                 ('retry' if retry else 'failed',time.time()+min(30,2**job['attempts']),code,message,None if retry else now(),context.id,context.worker_id)).rowcount
            if changed and job['kind']=='hh.import':
                conn.execute("UPDATE imports SET status=?,error=?,completed_at=? WHERE id=?",('queued' if retry else 'failed',message,None if retry else now(),context.id))
            if changed and not retry and job['kind']=='resume.parse':
                conn.execute("UPDATE resume_documents SET state='failed' WHERE id=? AND state='queued'",(json.loads(job['payload'])['document_id'],))
            if changed and not retry:recover_assessment(conn,job)
    finally:heartbeat_stop.set();keeper.join(timeout=2)

class Worker:
    def __init__(self):self.stop_event=threading.Event();self.thread=None;self.identity='worker_'+secrets.token_hex(8)
    def run_once(self):
        job=claim(self.identity)
        if not job:return False
        execute(job);return True
    def run(self):
        while not self.stop_event.is_set():
            try:
                if not self.run_once():self.stop_event.wait(.3)
            except Exception:log.exception('Worker cycle failed');self.stop_event.wait(1)
    def start(self):
        self.thread=threading.Thread(target=self.run,name='fsp-worker',daemon=True);self.thread.start()
    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=3)

def statistics():
    with connection() as conn:
        return {row['status']:row['count'] for row in conn.execute('SELECT status,COUNT(*) AS count FROM jobs GROUP BY status')}

if __name__=='__main__':
    from .db import initialize
    from .ml import engine
    from .domain import queue_warmup
    initialize();engine.prepare()
    if engine.state=='ready':queue_warmup()
    worker=Worker()
    try:worker.run()
    except KeyboardInterrupt:worker.stop()
