from pathlib import Path
import json,time
from fastapi import APIRouter,Depends,File,UploadFile,HTTPException
from fastapi.responses import JSONResponse
from pydantic import Field
from .auth import session_user,require_role
from .schemas import StrictModel,ProfileUpdate
from .db import connection,ROOT,audit
from . import passports,resumes,jobs,telemetry

router=APIRouter(prefix='/api',tags=['Service improvements'])

class PassportRequest(StrictModel):
    candidate_id:str=Field(min_length=1,max_length=100)
    vacancy_id:str=Field(min_length=1,max_length=100)

class ResumeApproval(StrictModel):
    version:int=Field(ge=1)
    profile:ProfileUpdate

@router.post('/passports',status_code=201)
def create_passport(body:PassportRequest,user=Depends(session_user)):
    return passports.create(body.candidate_id,body.vacancy_id,user)

@router.get('/passports/{identity}')
def read_passport(identity:str,user=Depends(session_user)):
    return passports.read(identity,user)

@router.get('/passports/{identity}/export')
def export_passport(identity:str,user=Depends(session_user)):
    return JSONResponse(passports.read(identity,user),headers={'Content-Disposition':'attachment; filename="match-passport.json"'})

@router.get('/offers/{identity}/passport')
def offer_passport(identity:str,user=Depends(session_user)):
    with connection() as conn:row=conn.execute('SELECT * FROM offers WHERE id=?',(identity,)).fetchone()
    if not row or not (user['role']=='candidate' and row['candidate_id']==user.get('candidate_id') or user['role']=='employer' and row['org_id']==user.get('org_id')):
        raise HTTPException(404,'Предложение не найдено')
    payload=json.loads(row['payload'])
    if not payload.get('passport_id'):raise HTTPException(404,'Предложение создано до добавления паспортов')
    return passports.read(payload['passport_id'],user)

@router.post('/profile/resumes',status_code=202)
async def upload_resume(file:UploadFile=File(...),user=Depends(session_user)):
    require_role(user,'candidate')
    try:content=await file.read(resumes.MAX_BYTES+1)
    finally:await file.close()
    return resumes.upload(content,file.filename or '',user)

@router.get('/profile/resumes/{identity}')
def read_resume(identity:str,user=Depends(session_user)):
    require_role(user,'candidate')
    return resumes.document(identity,user)

@router.post('/profile/resumes/{identity}/approve')
def approve_resume(identity:str,body:ResumeApproval,user=Depends(session_user)):
    require_role(user,'candidate')
    return resumes.approve(identity,body.version,body.profile,user)

def owned_job(identity,user,conn):
    row=conn.execute('SELECT * FROM jobs WHERE id=?',(identity,)).fetchone()
    if not row or row['actor_id']!=user['id'] and user['role']!='admin':raise HTTPException(404,'Задание не найдено')
    return row

@router.get('/jobs/{identity}')
def read_job(identity:str,user=Depends(session_user)):
    with connection() as conn:return jobs.public_job(owned_job(identity,user,conn))

@router.post('/jobs/{identity}/retry')
def retry_job(identity:str,user=Depends(session_user)):
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');row=owned_job(identity,user,conn)
        if row['status']!='failed':raise HTTPException(409,'Повтор доступен только для завершившегося с ошибкой задания')
        conn.execute("UPDATE jobs SET status='queued',attempts=0,available_at=?,error=NULL,error_code=NULL,completed_at=NULL WHERE id=?",(time.time(),identity))
        if row['kind']=='hh.import':conn.execute("UPDATE imports SET status='queued',error=NULL,completed_at=NULL WHERE id=?",(identity,))
        if row['kind']=='resume.parse':conn.execute("UPDATE resume_documents SET state='queued' WHERE id=?",(json.loads(row['payload'])['document_id'],))
        audit(conn,user['id'],'job_retried',identity)
    return {'id':identity,'status':'queued'}

@router.get('/operations')
def operations(user=Depends(session_user)):
    require_role(user,'admin')
    with connection() as conn:
        schema=conn.execute("SELECT CAST(value AS INTEGER) FROM meta WHERE key='schema_version'").fetchone()[0]
        counts={table:conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ['passports','resume_documents']}
    return {'schema_version':schema,'counts':counts,'jobs':jobs.statistics(),'api':telemetry.report(),
            'worker_mode':'embedded' if __import__('os').environ.get('FSP_JOB_AUTOSTART','1')=='1' else 'external_or_manual'}

@router.get('/quality/challenge')
def challenge_report(user=Depends(session_user)):
    require_role(user,'admin')
    path=ROOT/'artifacts'/'challenge-evaluation.json'
    if not path.exists():raise HTTPException(404,'Запустите scripts/evaluate_challenge.py')
    return json.loads(path.read_text(encoding='utf-8'))
