"""Normal email accounts, grade cases and employer journey on isolated PG.

Reference answers are supplied by a private test oracle. This is browser/API
acceptance, not independent human UX or qualification validation. No mail leaves
the fixture. No deployed accounts, data or secrets are changed.
"""
import json
import os
import re
import secrets
import socket
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.postgres_test_context import postgres_test_schema
from scripts.browser_final_release import serve

def main():
    from playwright.sync_api import sync_playwright,expect
    import httpx
    out=Path(os.environ.get('FSP_JOURNEY_BROWSER_OUT',str(ROOT/'.local/final-journey-browser')));out.mkdir(parents=True,exist_ok=True)
    checks=[];errors=[];password='Fixture-'+secrets.token_urlsafe(20)
    with postgres_test_schema():
        os.environ.update(FSP_DEMO='0',FSP_TEST_LOGIN='0',FSP_EMAIL_REQUIRED='1',FSP_ALLOW_REGISTRATION='1',
                          FSP_JOB_AUTOSTART='1',FSP_ENABLE_ML='0',FSP_ASSESSMENT_PATH='work_sample_v3_balanced',
                          FSP_SMTP_HOST='',FSP_DELIVERY_API_URL='',FSP_OLLAMA_URL='')
        sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128);url='http://127.0.0.1:'+str(sock.getsockname()[1])
        os.environ.update(FSP_PUBLIC_URL=url,FSP_ALLOWED_ORIGINS=url)
        from backend import identity,db
        identity.send_mail=lambda context,payload:{'fixture_mailbox':True}  # Never an external recipient.
        from backend.main import app
        server,thread,_=serve(app,sock)
        from scripts.create_admin import create
        create('journey.owner','Изолированный владелец',password)
        def checked(response,status=200):
            assert response.status_code==status,response.text
            return response.json()
        owner=httpx.Client(base_url=url)
        owner.headers['X-CSRF-Token']=checked(owner.post('/api/auth/login',json={'username':'journey.owner','password':password}))['csrf']
        owner.headers['X-CSRF-Token']=checked(owner.post('/api/auth/password',json={'current_password':password,'password':password+'new'}))['csrf']
        def api(page,path,body=None,method=None):
            return page.evaluate("""async arg=>{const me=await (await fetch('/api/auth/me')).json();
             const response=await fetch('/api'+arg.path,{method:arg.method||(arg.body===null?'GET':'POST'),
             headers:{'X-CSRF-Token':me.csrf,'Content-Type':'application/json'},
             body:arg.body===null?undefined:JSON.stringify(arg.body)});
             if(!response.ok)throw Error(response.status+' '+await response.text());return response.json();}""",
             {'path':path,'body':body,'method':method})
        def register(page,username,role):
            page.goto(url);page.get_by_role('button',name='Создать аккаунт',exact=True).click()
            page.get_by_label('Как вас зовут').fill('Проверочный '+('кандидат' if role=='candidate' else 'представитель'))
            page.get_by_role('dialog',name='Создать аккаунт').get_by_role('combobox').select_option(role)
            if role=='employer':page.get_by_label('Компания',exact=True).fill('Изолированная команда '+username)
            page.get_by_label('Логин',exact=True).fill(username)
            page.get_by_label('Email',exact=True).fill(username+'@example.test')
            page.get_by_label('Пароль',exact=True).last.fill(password)
            page.get_by_role('checkbox').check();page.get_by_role('button',name='Зарегистрироваться',exact=True).click()
            expect(page.get_by_role('heading',name='Подтвердите email')).to_be_visible()
            with db.connection() as conn:
                row=conn.execute("SELECT j.payload FROM jobs j JOIN users u ON u.id=j.actor_id WHERE u.username=? AND j.kind='mail.send' ORDER BY j.created_at DESC",(username,)).fetchone()
                token=json.loads(row[0])['text'].split('#verify=')[1].split('\n')[0]
            def delayed_taxonomy(route):
                response=route.fetch();time.sleep(.35);route.fulfill(response=response)
            page.route('**/api/taxonomy',delayed_taxonomy)
            page.goto(url+'/#verify='+token)
            page.wait_for_function("async()=>{const r=await fetch('/api/auth/me');return r.ok&&(await r.json()).email_verified}")
            expect(page.get_by_role('main')).to_be_visible(timeout=20000)
            page.reload();expect(page.get_by_role('main')).to_be_visible(timeout=20000)
            page.unroute('**/api/taxonomy',delayed_taxonomy)
            return api(page,'/auth/me')
        def layout(page):
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            assert page.get_by_text('Выбрать демороль',exact=True).count()==0
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch()
                variants=json.loads(os.environ.get('FSP_JOURNEY_BROWSER_VARIANTS','[[320,"backend"],[390,"qa"],[750,"devops"],[1440,"ml"],[1440,"frontend"],[1440,"security"]]'))
                for width,spec in variants:
                    cand_ctx=browser.new_context(viewport={'width':width,'height':1000})
                    emp_ctx=browser.new_context(viewport={'width':width,'height':1000})
                    cand=cand_ctx.new_page();emp=emp_ctx.new_page()
                    for page in [cand,emp]:page.on('pageerror',lambda e:errors.append(str(e)))
                    who=register(cand,'candidate.'+spec,'candidate')
                    cand.goto(url+'/candidate/assessment')
                    cand.get_by_label('Направление',exact=False).select_option(spec)
                    cand.get_by_label('Предполагаемый грейд',exact=False).select_option('Middle')
                    cand.get_by_label('Режим',exact=False).select_option('certification')
                    cand.get_by_role('button',name='Начать проверку',exact=True).click()
                    expect(cand.get_by_role('heading',name='Задание 1 из 12',exact=True)).to_be_visible(timeout=30000)
                    with db.connection() as conn:
                        row=conn.execute("SELECT id,payload FROM assessment_sessions WHERE candidate_id=? AND status='active' ORDER BY created_at DESC LIMIT 1",(who['candidate_id'],)).fetchone()
                        attempt=row['id'];data=json.loads(row['payload'])
                    assert data['assembly_plan']['version']=='balanced-core-2026-v3'
                    captured=False
                    for position,item in enumerate(data['items']):
                        if item.get('optional'):continue
                        if position:cand.get_by_role('button',name='Перейти к заданию '+str(position+1),exact=True).click()
                        if item.get('choices'):
                            expected=item['private']['expected'];wanted=expected if isinstance(expected,list) else [expected]
                            for choice in item['choices']:
                                if choice['id'] in wanted:cand.get_by_label(choice['text'],exact=True).check()
                        elif item['kind']=='work_sample':cand.get_by_label('Исправленное решение (JSON)',exact=True).fill(item['private'].get('alternative_answer',item['private']['reference_answer']))
                        elif item['kind']=='sql':
                            threshold=re.search(r'amount ≥ (\d+)',item['prompt'])[1]
                            cand.get_by_label('Ответ',exact=True).fill(f"SELECT team,SUM(amount) FROM events WHERE status='ok' AND amount>={threshold} GROUP BY team")
                        else:cand.get_by_label('Ответ',exact=True).fill(str(item['private']['expected']))
                        expect(cand.get_by_text('Ответы сохранены',exact=True)).to_be_visible()
                        layout(cand)
                        if item.get('version')=='grade-decisions-2026-v1' and not captured:
                            cand.evaluate('window.scrollTo(0,0)')
                            cand.screenshot(path=str(out/f'grade-{spec}-{width}.png'),full_page=True);captured=True
                            cand.locator('.assessment-work').screenshot(path=str(out/f'grade-panel-{spec}-{width}.png'))
                    cand.get_by_role('button',name='Завершить и проверить',exact=True).click()
                    cand.get_by_role('button',name='Отправить на проверку',exact=True).click()
                    expect(cand.get_by_role('heading',name='Автоматическая проверка пройдена',exact=True)).to_be_visible(timeout=30000)
                    state=api(cand,'/assessments/'+attempt)
                    assert state['skill_passport']['current']['category_confirmed_by_this_revision']
                    cand.get_by_text('Мои ответы',exact=True).click();expect(cand.get_by_text('Разбор:',exact=True).first).to_be_visible()
                    cand.get_by_text('Мои ответы',exact=True).click();layout(cand)
                    cand.screenshot(path=str(out/f'passport-{spec}-{width}.png'),full_page=True)
                    cand.goto(url+'/candidate/passports')
                    expect(cand.locator('.skill-passport').first).to_be_visible()
                    cand.locator('.skill-passport').first.screenshot(path=str(out/f'passport-panel-{spec}-{width}.png'))
                    api(cand,'/assessments/onboarding/complete',{})
                    profile=api(cand,'/profile')
                    from backend.assessment_catalog import SPECIALIZATIONS
                    api(cand,'/profile',{'name':'Проверочный кандидат','title':'Специалист '+SPECIALIZATIONS[spec]['label'],'role':spec,'city':'Пермь',
                        'skills':SPECIALIZATIONS[spec]['skills'],'bio':'Синтетический профиль для автоматической проверки пути',
                        'visible':True,'work_modes':['remote'],'contacts':{'email':'fixture@example.test'},'version':profile['version']},'PUT')
                    api(cand,'/consents',{'kind':'publication','granted':True})
                    representative=register(emp,'employer.'+spec,'employer')
                    emp.goto(url+'/employer/company');emp.get_by_label('Кого вы представляете').select_option('team')
                    emp.get_by_label('Название команды',exact=True).fill('Изолированная команда '+spec)
                    emp.get_by_label('Способ связи',exact=True).fill('fixture@example.test')
                    emp.get_by_label('Описание организации или команды',exact=True).fill('Синтетические сведения для автоматического сценария проверки')
                    emp.get_by_label('Над чем работает команда',exact=True).fill('Проверка интеграций и программных продуктов')
                    emp.get_by_role('button',name='Сохранить и запросить проверку',exact=True).click()
                    expect(emp.get_by_text('Сведения направлены на проверку представителя',exact=True)).to_be_visible()
                    company=api(emp,'/company');assert company['payload']['tax_id']==''
                    checked(owner.post('/api/admin/companies/'+representative['org_id']+'/review',json={'state':'verified','version':company['version'],
                        'organization_evidence':'Изолированная фикстура команды, не проверка реальной организации',
                        'authority_evidence':'Контрольный представитель команды в разрешённом сценарии'}))
                    emp.reload();expect(emp.get_by_text('Представитель команды проверен',exact=True)).to_be_visible();layout(emp)
                    emp.screenshot(path=str(out/f'company-{spec}-{width}.png'),full_page=True)
                    need=api(emp,'/needs',{'title':'Подбор '+SPECIALIZATIONS[spec]['label'],'description':'Задачи разработки и проверки инфраструктуры команды',
                        'team':'Контрольная команда продукта','specialization':spec,'grade':'Middle','skills':SPECIALIZATIONS[spec]['skills'][:2],
                        'salary_min':120000,'salary_max':180000,'employer_contact':'fixture@example.test'})
                    emp.goto(url+'/employer/candidates');emp.locator('#matching-need').select_option(need['id'])
                    emp.get_by_role('button',name='Найти кандидатов',exact=True).click()
                    expect(emp.get_by_role('button',name='Пригласить',exact=True)).to_be_visible(timeout=20000)
                    expect(emp.get_by_role('button',name='Найти кандидатов',exact=True)).to_be_enabled()
                    emp.get_by_text('Основания подбора',exact=True).click()
                    expect(emp.get_by_text('Категория основана на заданиях по направлению.',exact=False)).to_be_visible()
                    emp.get_by_text('Основания подбора',exact=True).click()
                    layout(emp);emp.screenshot(path=str(out/f'matching-{spec}-{width}.png'),full_page=True)
                    search=api(emp,'/search-sessions',{'need_id':need['id']});assert len(search['items'])==1 and search['items'][0]['candidate']['contacts']=={}
                    invitation=api(emp,'/invitations',{'need_id':need['id'],'candidate_id':who['candidate_id'],'message':'Обсудим задачи продукта и условия команды',
                        'next_step':'Встреча с командой по согласованным условиям','idempotency_key':'isolated-journey-'+spec})
                    api(cand,'/invitations/'+invitation['id']+'/respond',{'action':'accepted','version':invitation['version']})
                    assert api(emp,'/candidates/'+who['candidate_id'])['contacts']['email']=='fixture@example.test'
                    api(cand,'/contacts/revoke',{})
                    assert api(emp,'/candidates/'+who['candidate_id'])['contacts']=={}
                    checks.append({'width':width,'specialization':spec,'email_registration':True,'four_choice_grade_cases':True,'normal_company_without_tax_id':True,
                                   'automatic_category_passport':True,'matching_without_vacancy':True,'addressed_contacts_and_revocation':True,'overflow':0})
                    cand_ctx.close();emp_ctx.close()
                browser.close()
            assert not errors,errors
            report={'api':app.version,'assembly':'balanced-core-2026-v3','checks':checks,'js_errors':errors,'scope':'Browser/API acceptance on normal accounts in an isolated synthetic dataset with private reference answers. No human pilot.'}
            (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'checks':len(checks),'js_errors':len(errors)}))
        finally:owner.close();server.should_exit=True;thread.join(10)

if __name__=='__main__':main()
