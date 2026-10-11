"""Public Work Russia API. No employer contact/person fields are retained."""
import hashlib,json,re,time
from datetime import datetime,timezone
from urllib.parse import urlparse
import httpx
from fastapi import APIRouter,Depends
from pydantic import Field
from .auth import session_user,require_role
from .schemas import StrictModel
from .db import connection,dumps,now,audit,bump
from .integrations import clean_html
from .taxonomy import extract_skills,ROLE_SKILLS,normalize_text
from .domain import update_index
from .jobs import enqueue,JobError

API='https://opendata.trudvsem.ru/api/v1/vacancies'
router=APIRouter(prefix='/api',tags=['Public vacancy sources'])

def normalize(raw,observed_at=None):
    stamp=observed_at or now();seen=datetime.fromisoformat(stamp).timestamp()
    company=raw.get('company') or {};description=clean_html((raw.get('duty') or '')+'\n'+(raw.get('requirements') or ''))
    title=str(raw.get('job-name') or '')[:140];identity=str(raw.get('id') or '')
    if not re.fullmatch(r'[\w-]{1,100}',identity) or not title:raise ValueError('Invalid source identity')
    url=raw.get('vac_url','');parsed=urlparse(url)
    if parsed.scheme!='https' or parsed.hostname!='trudvsem.ru' or parsed.username or parsed.password:raise ValueError('Invalid source link')
    published=raw.get('creation-date');modified=raw.get('date_modify') or published
    try:
        date_text=re.sub(r'([+-]\d{2})(\d{2})$',r'\1:\2',modified.replace('Z','+00:00'))
        updated=datetime.fromisoformat(date_text)
        if updated.tzinfo is None:updated=updated.replace(tzinfo=timezone.utc)
        if updated.timestamp()>seen+86400:raise ValueError('Future source date')
    except (AttributeError,TypeError):raise ValueError('Missing source date')
    extracted=extract_skills(title+' '+description);skills=list(dict.fromkeys(x['skill'] for x in extracted if not x['negated']))
    city=((raw.get('addresses') or {}).get('address') or [{}])[0].get('location') or (raw.get('region') or {}).get('name','')
    role=max(ROLE_SKILLS,key=lambda r:len(set(skills)&ROLE_SKILLS[r])) if skills else 'backend'
    content={'title':title,'company':company.get('name','Не указано'),'description':description[:8000],'city':city,'salary_min':raw.get('salary_min'),'salary_max':raw.get('salary_max')}
    fingerprint=hashlib.sha256(normalize_text(dumps(content)).encode()).hexdigest()
    return dict(content,id='trud_'+identity,role=role,required_skills=skills,optional_skills=[],currency='RUB',salary_type='unknown',work_mode='remote' if 'дистанц' in str(raw.get('schedule','')).lower() else 'office',experience_min=(raw.get('requirement') or {}).get('experience'),source='Работа России',source_url=url,published_at=published,source_modified_at=modified,observed_at=stamp,last_seen_at=stamp,expires=seen+30*86400,content_fingerprint=fingerprint,demo=False,extraction_reviewed=False,state='active',employer_verification='external_source_only')

def fetch(query,limit=100):
    try:
        with httpx.Client(timeout=30,follow_redirects=False,headers={'User-Agent':'FSPCareer/0.4 public-opendata'},trust_env=False) as client:
            response=client.get(API,params={'text':query,'limit':limit,'offset':0})
        if response.status_code==403:raise JobError('source_access_denied','Источник отклонил доступ (403)')
        if response.status_code==429 or response.status_code>=500:raise JobError('source_temporary_failure','Источник временно недоступен',True)
        response.raise_for_status();envelope=response.json()
        raw=envelope['results']['vacancies'];stamp=now();items=[];rejected=0
        for entry in raw:
            try:items.append(normalize(entry['vacancy'],stamp))
            except (ValueError,KeyError,TypeError,IndexError):rejected+=1
        return items,{'source':'Работа России','api':API,'query':query,'observed_at':stamp,'http_status':response.status_code,'received':len(raw),'normalized':len(items),'rejected':rejected,'total':envelope.get('meta',{}).get('total'),'retained':'vacancies without contact/person fields'}
    except (httpx.NetworkError,httpx.TimeoutException):raise JobError('source_network_error','Ошибка сети источника',True)
    except (httpx.HTTPError,ValueError,KeyError,TypeError):raise JobError('source_invalid_response','Источник вернул некорректный ответ')

def import_items(items,metadata,actor=None,context=None):
    counts={'inserted':0,'updated':0,'unchanged':0,'duplicates':0,'expired':0}
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if context:context.check(conn)
        for item in items:
            row=conn.execute('SELECT * FROM vacancies WHERE id=?',(item['id'],)).fetchone()
            # Keep provenance for an exact content duplicate, no approximate merge.
            duplicates=conn.execute("SELECT id FROM vacancies WHERE id<>? AND json_extract(payload,'$.content_fingerprint')=? AND archived=0",(item['id'],item['content_fingerprint'])).fetchall()
            if duplicates and not row:counts['duplicates']+=1;continue
            old=json.loads(row['payload']) if row else None
            transient={'observed_at','last_seen_at','expires'}
            changed=not old or bool(row['archived']) or {k:v for k,v in old.items() if k not in transient}!={k:v for k,v in item.items() if k not in transient}
            if not row:counts['inserted']+=1
            elif changed:counts['updated']+=1
            else:counts['unchanged']+=1
            conn.execute('INSERT INTO vacancies(id,org_id,payload,archived) VALUES(?,NULL,?,0) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,archived=0,version=vacancies.version+?',(item['id'],dumps(item),int(changed)))
            if changed:bump(conn);update_index('vacancy',item['id'],conn)
        for row in conn.execute("SELECT * FROM vacancies WHERE archived=0 AND json_extract(payload,'$.source')='Работа России'").fetchall():
            if json.loads(row['payload']).get('expires',0)<time.time():
                conn.execute('UPDATE vacancies SET archived=1,version=version+1 WHERE id=?',(row['id'],));update_index('vacancy',row['id'],conn);bump(conn);counts['expired']+=1
        result={**metadata,**counts};conn.execute("INSERT OR REPLACE INTO meta VALUES('live_vacancies_latest',?)",(dumps(result),));audit(conn,actor,'public_vacancies_imported',None,result)
    return result

def process(context,payload):
    items,metadata=fetch(payload['query'],payload['limit']);return import_items(items,metadata,context.job['actor_id'],context)

class Import(StrictModel):query:str=Field(default='Python',min_length=2,max_length=80);limit:int=Field(default=100,ge=1,le=100)
@router.post('/admin/vacancy-imports',status_code=202)
def start(body:Import,user=Depends(session_user)):
    require_role(user,'admin');identity=enqueue('trud.import',body.model_dump(),user['id'])
    return {'job_id':identity}

@router.get('/admin/vacancy-imports')
def state(user=Depends(session_user)):
    require_role(user,'admin')
    with connection() as conn:
        row=conn.execute("SELECT value FROM meta WHERE key='live_vacancies_latest'").fetchone()
        return {'latest':json.loads(row[0]) if row else None,'jobs':[dict(r,result=json.loads(r['result']) if r['result'] else None) for r in conn.execute("SELECT id,status,result,error,created_at FROM jobs WHERE kind='trud.import' ORDER BY created_at DESC LIMIT 20")]}
