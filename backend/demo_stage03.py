"""Explicit synthetic fixtures. Never assert that seeded people took real tests."""
import json,re,time
from .db import connection,dumps,now,DEMO
def readable_need_title(value):
    """Presentation only; frozen invitation/passport contents remain unchanged."""
    title=value.get('title','')
    if re.fullmatch(r'Browser acceptance API \d+',title) and value.get('description')=='Разработка интеграций продуктовой платформы и API' and value.get('team')=='Команда инфраструктуры и интеграций' and value.get('employer_contact')=='hr@example.org':
        return 'Backend-разработчик: интеграции и API'
    return title

def seed_domain():
    if not DEMO:return
    with connection() as conn:
        conn.execute("INSERT OR IGNORE INTO organization_members SELECT id,org_id,'owner',? FROM users WHERE role='employer' AND email LIKE '%.demo'",(now(),))
        for org in conn.execute('SELECT * FROM organizations'):
            payload={'description':'Синтетическая компания для демонстрации','industry':'IT','domain':'example.org','contact':'demo@example.org','team':'Демонстрационная продуктовая команда','demo':True,'verification_checks':{'method':'synthetic_fixture','reviewed_at':now()}}
            conn.execute("INSERT OR IGNORE INTO company_profiles(org_id,payload,verification) VALUES(?,?,'demo_verified')",(org['id'],dumps(payload)))
        for row in conn.execute('SELECT * FROM candidates').fetchall():
            candidate=json.loads(row['payload'])
            if candidate.get('sandbox_org_id'):continue
            if not candidate.get('demo'):continue
            if candidate['role'] not in {'backend','frontend','qa'}:continue
            before=dict(candidate)
            candidate.setdefault('assessment_onboarding_done',True)
            candidate.setdefault('contacts',{'email':candidate['id']+'@example.org'})
            if candidate!=before:conn.execute('UPDATE candidates SET payload=? WHERE id=?',(dumps(candidate),row['id']))
            proof={'demo':True,'calibration':'synthetic_fixture','competencies':{},'notice':'Синтетический результат; тест этим человеком не проходился'}
            conn.execute('INSERT OR IGNORE INTO grade_certifications VALUES(?,?,?,?,?,NULL,?,?,?,1)',('demo_cert_'+row['id'],row['id'],candidate['role'],'Middle',82,'demo',dumps(proof),now()))
            for kind in ['processing','publication']:
                conn.execute('INSERT OR IGNORE INTO consent_events VALUES(?,?,?,?,?,?)',('demo_consent_'+kind+'_'+row['id'],row['id'],kind,1,'demo',now()))
        # Add readable demo requests without replacing profiles, certificates or
        # user-created requests. Retain known old browser fixtures for audit.
        owner=conn.execute("SELECT org_id FROM users WHERE id='u_employer' AND role='employer' AND email LIKE '%.demo'").fetchone()
        if owner:
            for row in conn.execute('SELECT id,payload FROM employer_needs WHERE org_id=?',(owner['org_id'],)).fetchall():
                value=json.loads(row['payload'])
                if re.fullmatch(r'Browser acceptance API \d+',value.get('title','')) and value.get('description')=='Разработка интеграций продуктовой платформы и API' and value.get('team')=='Команда инфраструктуры и интеграций' and value.get('employer_contact')=='hr@example.org':
                    if not value.get('demo_test_artifact'):
                        value['demo_test_artifact']=True
                        conn.execute('UPDATE employer_needs SET payload=? WHERE id=?',(dumps(value),row['id']))
            fixtures=[
                ('demo_need_backend','Backend-разработчик в продуктовую команду','backend',['python','sql'],150000,220000,'Разрабатывать API личного кабинета, интеграции и надёжную обработку запросов.','Продуктовая команда сервиса для спортивных соревнований.'),
                ('demo_need_qa','QA-инженер для веб-сервиса','qa',['testing'],110000,160000,'Проверять пользовательские сценарии, контракты API и регрессию веб-сервиса.','Команда качества кабинета участников и работодателей.'),
            ]
            for identity,title,spec,skills,low,high,description,team in fixtures:
                value={'title':title,'description':description,'team':team,'specialization':spec,'grade':'Middle','skills':skills,'salary_min':low,'salary_max':high,'salary_type':'net','work_mode':'remote','city':'','employer_contact':'hr@example.org','valid_days':90,'demo':True,'origin':'demo_catalog_v2','risk_signals':[]}
                conn.execute("INSERT OR IGNORE INTO employer_needs(id,org_id,payload,state,expires,created_at) VALUES(?,?,?,'active',?,?)",(identity,owner['org_id'],dumps(value),time.time()+90*86400,now()))
