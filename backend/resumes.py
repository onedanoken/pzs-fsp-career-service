"""Local document parsing, source spans and explicit profile approval."""
import hashlib,io,json,re,secrets,zipfile,base64,subprocess,sys
from pathlib import Path
from xml.etree import ElementTree
from fastapi import HTTPException
from .db import connection,dumps,now,bump,audit
from .jobs import enqueue,JobError
from .taxonomy import skill_mentions,ROLE_SKILLS

MAX_BYTES=5*1024*1024
MAX_TEXT=100000

def extract_text(content,filename):
    extension=Path(filename).suffix.lower()
    try:
        if extension=='.txt':
            try:text=content.decode('utf-8-sig')
            except UnicodeDecodeError:raise JobError('invalid_encoding','TXT должен быть в UTF-8')
        elif extension=='.docx':
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if sum(item.file_size for item in archive.infolist())>20*1024*1024:raise JobError('document_too_large','Слишком большой распакованный DOCX')
                raw=archive.read('word/document.xml')
                if b'<!DOCTYPE' in raw or b'<!ENTITY' in raw:raise JobError('invalid_document','Недопустимая структура DOCX')
                xml=ElementTree.fromstring(raw);ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
                text='\n'.join(''.join(node.text or '' for node in paragraph.findall('.//w:t',ns)) for paragraph in xml.findall('.//w:p',ns))
        elif extension=='.pdf':
            from pypdf import PdfReader
            if not content.startswith(b'%PDF-'):raise JobError('invalid_document','Файл не является PDF')
            reader=PdfReader(io.BytesIO(content))
            if reader.is_encrypted:raise JobError('encrypted_pdf','Загрузите PDF без пароля')
            if len(reader.pages)>30:raise JobError('too_many_pages','Поддерживается до 30 страниц PDF')
            text='\n'.join((page.extract_text(extraction_mode='layout') or '') if '/Contents' in page else '' for page in reader.pages)
        else:raise JobError('unsupported_format','Поддерживаются PDF, DOCX и TXT')
    except JobError:raise
    except Exception:raise JobError('invalid_document','Не удалось прочитать документ; проверьте формат файла')
    text=text.replace('\x00','').strip()
    if len(text)<20:raise JobError('text_layer_missing','Недостаточно текста. Для PDF нужен текстовый слой; OCR сканов пока не включён')
    if len(text)>MAX_TEXT:raise JobError('text_too_large','В документе слишком много текста')
    return text

def parse(text):
    mentions=skill_mentions(text);skills=[];warnings=[]
    for key in dict.fromkeys(m['skill'] for m in mentions):
        group=[m for m in mentions if m['skill']==key]
        if any(m['negated'] for m in group) and any(not m['negated'] for m in group):
            warnings.append({'code':'conflicting_skill','skill':key,'message':'Есть положительные и отрицательные упоминания; уточните навык'})
        elif any(not m['negated'] for m in group):skills.append(key)
    fields={};field_sources={}
    for line in text.splitlines()[:12]:
        line=line.strip()
        name=re.fullmatch(r'(?:Имя\s*:\s*)?([А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?)',line)
        if name and not line.lower().startswith(('опыт ','образование ','ключевые ','желаемая ')):
            fields['name']=name.group(1);start=text.index(name.group(1));field_sources['name']={'start':start,'end':start+len(name.group(1)),'span':name.group(1)};break
    title=re.search(r'(?im)^(?:Должность|Роль|Позиция)\s*:\s*([^\n]{2,120})',text)
    if title:fields['title']=title.group(1).strip();field_sources['title']={'start':title.start(1),'end':title.end(1),'span':title.group(1)}
    city=re.search(r'(?im)^Город\s*:\s*([^\n]{2,80})',text)
    if city:fields['city']=city.group(1).strip();field_sources['city']={'start':city.start(1),'end':city.end(1),'span':city.group(1)}
    experience=re.search(r'(?im)(?:Опыт\s*(?:работы)?\s*:?\s*)(\d+(?:[.,]\d+)?)\s*(?:лет|года?|год)',text)
    if experience and float(experience.group(1).replace(',','.'))<=50:
        fields['experience_years']=float(experience.group(1).replace(',','.'));field_sources['experience_years']={'start':experience.start(),'end':experience.end(),'span':experience.group()}
    fields['skills']=skills
    scores={role:len(set(skills)&group) for role,group in ROLE_SKILLS.items()}
    best=max(scores.values(),default=0)
    roles=[role for role,score in scores.items() if score==best and score>0]
    if len(roles)==1:fields['role']=roles[0]
    contacts={}
    email=re.search(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}',text)
    phone=re.search(r'(?<!\w)\+?\d[\d\s().-]{8,}\d(?!\w)',text)
    if email:contacts['email']=email.group()
    if phone:contacts['phone']=phone.group().strip()
    if contacts:fields['contacts']=contacts
    grade=re.search(r'(?im)(?:Грейд|Уровень)\s*:\s*(Intern|Junior|Middle|Senior)',text)
    if grade:fields['declared_grade']=grade.group(1).title()
    soft=re.search(r'(?im)^(?:Софт[- ]скиллы|Soft skills)\s*:\s*([^\n]{2,600})',text)
    if soft:fields['soft_skills']=[v.strip()[:120] for v in soft.group(1).split(',') if v.strip()][:20]
    return {'parser_version':'local-spans-v2','method':'local_document_parser_and_dictionary','requires_review':True,
            'fields':fields,'field_sources':field_sources,'skill_mentions':mentions,'warnings':warnings,
            'verification':'claimed','notice':'Извлечение из резюме не подтверждает достижение ФСП или профессиональный грейд.'}

def upload(content,filename,user):
    with connection() as conn:
        row=conn.execute('SELECT payload FROM candidates WHERE id=?',(user['candidate_id'],)).fetchone()
        if row and json.loads(row[0]).get('sandbox_org_id'):raise HTTPException(403,'В ATS-демонстрации используются только встроенные тестовые данные')
    if not content or len(content)>MAX_BYTES:raise HTTPException(413,'Размер файла должен быть от 1 байта до 5 МБ')
    if Path(filename).suffix.lower() not in {'.pdf','.docx','.txt'}:raise HTTPException(415,'Поддерживаются PDF, DOCX и TXT')
    checksum=hashlib.sha256(content).hexdigest()
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        profile=conn.execute('SELECT version FROM candidates WHERE id=?',(user['candidate_id'],)).fetchone()
        existing=conn.execute('SELECT id FROM resume_documents WHERE actor_id=? AND sha256=? AND profile_version=?',(user['id'],checksum,profile['version'])).fetchone()
        identity=existing['id'] if existing else 'resume_'+secrets.token_hex(10)
        if not existing:conn.execute('INSERT INTO resume_documents(id,actor_id,candidate_id,filename,sha256,content,profile_version,created_at) VALUES(?,?,?,?,?,?,?,?)',
                                      (identity,user['id'],user['candidate_id'],Path(filename.replace('\\','/')).name[:160],checksum,content,profile['version'],now()))
        job=enqueue('resume.parse',{'document_id':identity},user['id'],identity,conn=conn)
    return {'id':identity,'job_id':job,'status':'queued','reused':bool(existing)}

def document(identity,user):
    with connection() as conn:
        row=conn.execute('SELECT * FROM resume_documents WHERE id=? AND actor_id=?',(identity,user['id'])).fetchone()
    if not row:raise HTTPException(404,'Документ не найден')
    return {'id':row['id'],'filename':row['filename'],'sha256':row['sha256'],'state':row['state'],
            'draft':json.loads(row['draft']) if row['draft'] else None,'text':row['extracted_text'],
            'profile_version':row['profile_version'],'version':row['version'],'created_at':row['created_at']}

def process_document(context,payload):
    with connection() as conn:row=conn.execute('SELECT * FROM resume_documents WHERE id=?',(payload['document_id'],)).fetchone()
    if not row:raise JobError('document_missing','Документ удалён')
    if row['state'] in {'ready','approved'}:return {'document_id':row['id'],'state':row['state']}
    context.progress(0,1)
    try:
        result=subprocess.run([sys.executable,'-I',str(Path(__file__).with_name('document_runner.py'))],input=json.dumps({'content':base64.b64encode(row['content']).decode(),'filename':row['filename']}),capture_output=True,text=True,timeout=10)
        data=json.loads(result.stdout)
        if data.get('error_code'):raise JobError(data['error_code'],data['error'])
        text=data['text'];draft=parse(text)
    except (subprocess.TimeoutExpired,ValueError,KeyError):raise JobError('document_limits','Документ не удалось обработать в пределах лимитов; попробуйте текстовый PDF или TXT')
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE');context.check(conn)
        conn.execute("UPDATE resume_documents SET extracted_text=?,draft=?,state='ready' WHERE id=? AND state='queued'",(text,dumps(draft),row['id']))
        context.progress(1,1,conn=conn)
    return {'document_id':row['id'],'state':'ready'}

def approve(identity,document_version,profile,user):
    from .domain import update_index,candidate_from_row
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT * FROM resume_documents WHERE id=? AND actor_id=?',(identity,user['id'])).fetchone()
        if not row:raise HTTPException(404,'Документ не найден')
        if row['state']!='ready' or row['version']!=document_version:raise HTTPException(409,'Черновик недоступен или уже подтверждён')
        candidate=conn.execute('SELECT * FROM candidates WHERE id=?',(user['candidate_id'],)).fetchone()
        if candidate['version']!=profile.version or profile.version!=row['profile_version']:raise HTTPException(409,'Профиль изменился после загрузки документа; загрузите актуальное резюме заново')
        payload=json.loads(candidate['payload']);payload.update(profile.model_dump(exclude={'version','visible'}))
        payload['initials']=''.join(part[0] for part in profile.name.split()[:2])
        draft=json.loads(row['draft']);positive=set(draft['fields']['skills'])
        payload['resume_provenance']={'document_id':identity,'sha256':row['sha256'],'parser_version':draft['parser_version'],
            'skills':{skill:{'origin':'resume' if skill in positive else 'manual','verification':'claimed',
                      'spans':[m for m in draft['skill_mentions'] if m['skill']==skill and not m['negated']] if skill in positive else []} for skill in profile.skills}}
        conn.execute('UPDATE candidates SET payload=?,visible=?,version=version+1 WHERE id=?',(dumps(payload),profile.visible,user['candidate_id']))
        conn.execute('UPDATE users SET name=? WHERE id=?',(profile.name,user['id']))
        conn.execute("UPDATE resume_documents SET state='approved',version=version+1,approved_at=? WHERE id=?",(now(),identity))
        bump(conn);update_index('candidate',user['candidate_id'],conn)
        audit(conn,user['id'],'resume_approved',identity,{'candidate_id':user['candidate_id']})
        return candidate_from_row(conn.execute('SELECT * FROM candidates WHERE id=?',(user['candidate_id'],)).fetchone(),conn)
