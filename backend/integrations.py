import html
import json
import logging
import re
import httpx
from .db import connection,dumps,now,bump,audit
from .taxonomy import extract_skills,ROLE_SKILLS

log=logging.getLogger(__name__)

def clean_html(value):
    return html.unescape(re.sub(r"<[^>]*>"," ",value or ""))

def normalize_vacancy(item):
    description=clean_html(item.get('description',''))
    extracted=extract_skills(description+' '+' '.join(k['name'] for k in item.get('key_skills',[])))
    skills=[x['skill'] for x in extracted if not x['negated']]
    optional=[x['skill'] for x in extracted if not x['required'] and not x['negated']]
    salary=item.get('salary') or {};currency=salary.get('currency')
    formats={x.get('id') for x in item.get('work_format',[])}
    mode='remote' if (item.get('schedule') or {}).get('id')=='remote' or 'REMOTE' in formats else 'hybrid' if 'HYBRID' in formats else 'office'
    return {'id':'hh_'+item['id'],'title':item['name'],'company':(item.get('employer') or {}).get('name',''),
            'role':max(ROLE_SKILLS,key=lambda r:len(set(skills)&ROLE_SKILLS[r])) if skills else 'backend',
            'required_skills':list(dict.fromkeys(s for s in skills if s not in optional)),'optional_skills':list(dict.fromkeys(optional)),
            'salary_min':salary.get('from'),'salary_max':salary.get('to'),'currency':'RUB' if currency=='RUR' else currency,
            'salary_type':'gross' if salary.get('gross') is True else 'net' if salary.get('gross') is False else 'unknown',
            'experience_min':{'noExperience':0,'between1And3':1,'between3And6':3,'moreThan6':6}.get((item.get('experience') or {}).get('id')),
            'work_mode':mode,'city':(item.get('area') or {}).get('name'),'description':description,
            'source':'HeadHunter','source_url':item.get('alternate_url'),'published_at':item.get('published_at'),
            'demo':False,'extraction_reviewed':False,'archived':bool(item.get('archived'))}

def process_headhunter(context,payload):
    from .jobs import JobError
    from .domain import update_index
    checkpoint=json.loads(context.job['checkpoint']);cursor=checkpoint.get('cursor',0);ids=checkpoint.get('ids')
    def request(client,url,params=None):
        try:
            response=client.get(url,params=params)
            if response.status_code==403:raise JobError('source_access_denied','HeadHunter отклонил доступ (HTTP 403)')
            if response.status_code==429 or response.status_code>=500:raise JobError('source_temporary_failure','Источник временно недоступен (HTTP '+str(response.status_code)+')',True)
            response.raise_for_status();return response.json()
        except (httpx.TimeoutException,httpx.NetworkError):raise JobError('source_network_error','Сетевая ошибка источника; будет повторная попытка',True)
        except httpx.HTTPStatusError:raise JobError('source_http_error','Источник вернул ошибку HTTP')
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');context.check(conn)
        conn.execute("UPDATE imports SET status='running' WHERE id=?",(context.id,))
    with httpx.Client(timeout=25,headers={'User-Agent':'FSPCareerMVP/0.2 (local prototype)'}) as client:
        if ids is None:
            ids=[item['id'] for item in request(client,'https://api.hh.ru/vacancies',{'text':payload['query'],'per_page':payload['limit']})['items']]
            context.progress(cursor,len(ids),{'ids':ids,'cursor':cursor})
        for index in range(cursor,len(ids)):
            raw=request(client,'https://api.hh.ru/vacancies/'+ids[index]);item=normalize_vacancy(raw)
            with connection() as conn:
                conn.execute('BEGIN IMMEDIATE');context.check(conn)
                row=conn.execute('SELECT * FROM vacancies WHERE id=?',(item['id'],)).fetchone()
                old=json.loads(row['payload']) if row else None
                comparable={k:v for k,v in (old or {}).items() if k not in {'imported_at','last_seen_at'}}
                if not row or comparable!=item:
                    item['imported_at']=now();item['last_seen_at']=now()
                    conn.execute("INSERT INTO vacancies(id,org_id,payload,archived) VALUES(?,NULL,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,archived=excluded.archived,version=vacancies.version+1",(item['id'],dumps(item),item['archived']))
                    bump(conn);update_index('vacancy',item['id'],conn)
                else:
                    old['last_seen_at']=now();conn.execute('UPDATE vacancies SET payload=? WHERE id=?',(dumps(old),item['id']))
                context.progress(index+1,len(ids),{'ids':ids,'cursor':index+1},conn)
                conn.execute('UPDATE imports SET imported=? WHERE id=?',(index+1,context.id))
    with connection() as conn:
        context.check(conn);audit(conn,context.job['actor_id'],'vacancies_imported',context.id,{'source':'hh','count':len(ids)})
    return {'source':'hh','imported':len(ids)}
