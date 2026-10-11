"""One privacy policy for API, documents, matching and integrations."""
import json,re,secrets
from .db import connection, dumps, now, audit, bump
from .auth import require_verified
from fastapi import HTTPException

POLICY='2026-10-v1'
def consent(conn,candidate_id,kind):
    row=conn.execute('SELECT granted FROM consent_events WHERE candidate_id=? AND kind=? ORDER BY created_at DESC,id DESC LIMIT 1',(candidate_id,kind)).fetchone()
    return bool(row and row[0])

def contact_access(conn,candidate_id,user):
    if user and user.get('candidate_id')==candidate_id:return True
    return bool(user and user.get('org_id') and conn.execute('SELECT 1 FROM contact_grants WHERE candidate_id=? AND org_id=? AND active=1',(candidate_id,user['org_id'])).fetchone())

def scrub(text):
    text=re.sub(r'https?://\S+|www\.\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?<!\w)@[\w]{3,}', '[контакт скрыт]',str(text),flags=re.I)
    return re.sub(r'(?<!\w)\+?\d[\d\s().-]{8,}\d(?!\w)','[контакт скрыт]',text)

def redact(value):
    if isinstance(value,str):return scrub(value)
    if isinstance(value,list):return [redact(x) for x in value]
    if isinstance(value,dict):return {k:redact(v) for k,v in value.items() if k not in {'contacts','email','phone','telegram','website','extracted_text','resume_provenance'}}
    return value

def eligible(conn,candidate,org_id=None):
    from .db import DEMO
    if candidate.get('demo') and not DEMO:return False
    if candidate.get('sandbox_org_id') and candidate['sandbox_org_id']!=org_id:return False
    if org_id:
        org=conn.execute('SELECT payload FROM company_profiles WHERE org_id=?',(org_id,)).fetchone()
        if org and json.loads(org[0]).get('sandbox') and candidate.get('sandbox_org_id')!=org_id:return False
    identity=candidate['id']
    if org_id and conn.execute('SELECT 1 FROM employer_blocks WHERE candidate_id=? AND org_id=?',(identity,org_id)).fetchone():return False
    email=conn.execute('SELECT email_verified,auth_kind,active FROM users WHERE candidate_id=?',(identity,)).fetchone()
    from .certification_validity import usable_certificate
    grade=any(usable_certificate(row) for row in conn.execute('SELECT * FROM grade_certifications WHERE candidate_id=? AND active=1',(identity,)))
    return bool(candidate.get('visible') and candidate.get('looking',True) and candidate.get('assessment_onboarding_done') and email and (email[0] or email['auth_kind']=='username') and email['active'] and grade and consent(conn,identity,'processing') and consent(conn,identity,'publication'))

def public_profile(candidate,user,conn):
    if user and user.get('candidate_id')==candidate['id']:return candidate
    if candidate.get('sandbox_org_id') and (not user or user.get('org_id')!=candidate['sandbox_org_id']):raise HTTPException(404,'Профиль недоступен')
    if user and user.get('org_id'):
        if conn.execute('SELECT 1 FROM employer_blocks WHERE candidate_id=? AND org_id=?',(candidate['id'],user['org_id'])).fetchone():
            # Historic applications remain readable, without a current profile.
            # Directory/detail endpoints reject this relationship via eligible().
            return {'id':candidate['id'],'name':'Профиль недоступен','title':'','initials':'—',
                    'city':'','skills':[],'all_skills':[],'skill_labels':{},'categories':[],
                    'achievements':[],'evidence_count':0,'contacts':{},'contacts_available':False,
                    'work_modes':[],'experience_years':None,'salary_min':None,'bio':'',
                    'visible':False,'looking':False,'unavailable':True}
        org=conn.execute('SELECT payload FROM company_profiles WHERE org_id=?',(user['org_id'],)).fetchone()
        if org and json.loads(org[0]).get('sandbox') and candidate.get('sandbox_org_id')!=user['org_id']:raise HTTPException(404,'Профиль недоступен')
    # Admin moderation does not implicitly grant contact access.
    allowed=contact_access(conn,candidate['id'],user)
    result=dict(candidate) if allowed else redact(candidate)
    result['contacts_available']=allowed
    if not allowed:result['contacts']={}
    return result

def set_consent(candidate_id,kind,granted,user,conn):
    if kind not in {'processing','publication'}:raise HTTPException(422,'Неизвестный вид согласия')
    conn.execute('INSERT INTO consent_events VALUES(?,?,?,?,?,?)',('cons_'+secrets.token_hex(10),candidate_id,kind,int(granted),POLICY,now()))
    if not granted:
        from .integration_privacy import revoke_transfers
        revoke_transfers(conn,candidate_id,reason=kind+'_withdrawn')
        conn.execute('UPDATE candidates SET visible=0,version=version+1 WHERE id=?',(candidate_id,))
        conn.execute('UPDATE contact_grants SET active=0,revoked_at=? WHERE candidate_id=?',(now(),candidate_id))
    audit(conn,user['id'],'consent_changed',candidate_id,{'kind':kind,'granted':granted,'policy':POLICY})
    bump(conn)

def verified_employer(user):
    if user['role'] not in {'employer','admin'}:raise HTTPException(403,'Недостаточно прав')
    require_verified(user)
