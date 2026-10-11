"""Candidate/moderator acceptance on a disposable local synthetic database."""
import json,os,socket,sys,tempfile,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
OUT=Path(os.environ.get('FSP_COMPLAINT_BROWSER_OUT',str(ROOT/'tmp/complaint-browser')));OUT.mkdir(parents=True,exist_ok=True)
errors=[];checks=[]
from scripts.postgres_test_context import postgres_test_schema

with tempfile.TemporaryDirectory(prefix='fsp-complaint-ui-') as temporary, postgres_test_schema():
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128);url='http://127.0.0.1:'+str(sock.getsockname()[1])
    os.environ.update(FSP_DEMO='1',FSP_TEST_LOGIN='1',FSP_ENABLE_ML='0',FSP_ALLOW_REGISTRATION='0',FSP_JOB_AUTOSTART='0',FSP_OLLAMA_URL='',FSP_ALLOWED_ORIGINS=url,FSP_PUBLIC_URL=url)
    import uvicorn
    from backend.main import app
    from playwright.sync_api import sync_playwright,expect
    server=uvicorn.Server(uvicorn.Config(app,log_level='error'));thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    try:
        for _ in range(100):
            if server.started:break
            time.sleep(.1)
        assert server.started
        # Create only local synthetic source objects so every contextual entry is exercised.
        import httpx
        with httpx.Client(base_url=url) as setup:
            setup.headers['X-CSRF-Token']=setup.post('/api/auth/demo',json={'account':'u_employer'}).json()['csrf']
            need=setup.post('/api/needs',json={'title':'Контекст жалобы: приглашение','description':'Разработка надёжных API и интеграций продукта','team':'Команда инфраструктуры продукта','specialization':'backend','grade':'Middle','skills':['python','sql'],'salary_min':120000,'salary_max':180000,'employer_contact':'hr@example.org'});need.raise_for_status()
            response=setup.post('/api/invitations',json={'need_id':need.json()['id'],'candidate_id':'c_anna','message':'Обсудим задачи и условия нашей команды','idempotency_key':'browser-complaint-source-001'});response.raise_for_status()
            setup.headers['X-CSRF-Token']=setup.post('/api/auth/demo',json={'account':'u_anna'}).json()['csrf']
            response=setup.post('/api/applications',json={'vacancy_id':'v_python','message':'Локальный синтетический отклик','share_contacts':True});response.raise_for_status()
        def navigate(page,route):
            if page.viewport_size['width']<900:page.get_by_role('button',name='Открыть меню',exact=True).click()
            button=page.locator('nav button[data-route="'+route+'"]')
            if not button.is_visible():page.locator('.sidebar-more>summary').click()
            button.click();expect(page.locator('.sidebar')).not_to_have_class('sidebar open')
        def overflow(page):assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),'Horizontal overflow'
        with sync_playwright() as runtime:
            browser=runtime.chromium.launch(headless=True)
            for width in (320,1440):
                candidate=browser.new_page(viewport={'width':width,'height':950});admin=browser.new_page(viewport={'width':1440,'height':950})
                for page in (candidate,admin):page.on('pageerror',lambda error:errors.append(str(error)))
                candidate.goto(url);candidate.get_by_role('button',name='Кандидат · Анна Смирнова',exact=True).click()
                navigate(candidate,'complaints');expect(candidate.get_by_role('heading',name='Мои жалобы',exact=True)).to_be_visible()
                candidate.get_by_role('button',name='Новая жалоба',exact=True).click();dialog=candidate.get_by_role('dialog')
                dialog.get_by_label('Компания',exact=True).select_option('org_sigma')
                dialog.get_by_label('Опишите ситуацию',exact=True).fill('Коротко')
                expect(dialog.get_by_role('button',name='Отправить жалобу',exact=True)).to_be_disabled()
                message='Синтетическая проверка '+str(width)+': требуют оплату перед собеседованием.'
                dialog.get_by_label('Опишите ситуацию',exact=True).fill(message)
                def fail_submission(route):
                    if route.request.method=='POST':route.fulfill(status=503,content_type='application/json',body=json.dumps({'detail':'Временный сбой. Повторите отправку.'},ensure_ascii=False))
                    else:route.continue_()
                candidate.route('**/api/complaints',fail_submission)
                dialog.get_by_role('button',name='Отправить жалобу',exact=True).click();expect(dialog.get_by_role('alert')).to_contain_text('Временный сбой')
                expect(dialog.get_by_label('Опишите ситуацию',exact=True)).to_have_value(message)
                candidate.unroute('**/api/complaints',fail_submission)
                dialog.get_by_role('button',name='Отправить жалобу',exact=True).click();expect(dialog.get_by_role('heading',name='Жалоба отправлена')).to_be_visible();overflow(candidate)
                candidate.screenshot(path=str(OUT/f'confirmation-{width}.png'),full_page=True)
                dialog.get_by_role('button',name='Закрыть',exact=True).last.click();expect(dialog).not_to_be_visible()
                card=candidate.locator('.complaint-card').filter(has_text=message);expect(card).to_contain_text('На рассмотрении');overflow(candidate)
                admin.goto(url);admin.get_by_text('Другие демороли',exact=True).click();admin.get_by_role('button',name='Модератор · Эксперт ФСП',exact=True).click()
                moderation=admin.locator('.complaint-card').filter(has_text=message)
                moderation.get_by_role('button',name='Рассмотреть жалобу',exact=True).click();review=admin.get_by_role('dialog')
                reason='Проверка '+str(width)+': по синтетическим обстоятельствам нарушение '+('не подтверждено.' if width==1440 else 'подтверждено.')
                review.get_by_label('Обоснование решения',exact=True).fill(reason)
                review.get_by_role('button',name='Отклонить жалобу' if width==1440 else 'Подтвердить нарушение',exact=True).click();expect(review).not_to_be_visible()
                candidate.get_by_role('button',name='Обновить статусы',exact=True).click();expect(card).to_contain_text(reason)
                expect(card).to_contain_text('Нарушение не подтверждено' if width==1440 else 'Нарушение подтверждено')
                candidate.reload();expect(candidate.get_by_role('heading',name='Мои жалобы',exact=True)).to_be_visible();expect(card).to_contain_text(reason);overflow(candidate)
                candidate.screenshot(path=str(OUT/f'history-{width}.png'),full_page=True)
                navigate(candidate,'vacancies');vacancy=candidate.locator('article.panel').filter(has_text='СигмаТех').first
                vacancy.get_by_role('button',name='Пожаловаться',exact=True).click();context=candidate.get_by_role('dialog')
                expect(context).to_contain_text('Компания: СигмаТех');expect(context).to_contain_text('Вакансия:')
                context.get_by_label('Опишите ситуацию',exact=True).fill('Условия вакансии отличаются от опубликованных '+str(width))
                context.get_by_role('button',name='Отправить жалобу',exact=True).click();expect(context.get_by_role('heading',name='Жалоба отправлена')).to_be_visible()
                context.get_by_role('button',name='Мои жалобы',exact=True).click();expect(candidate.get_by_role('heading',name='Мои жалобы',exact=True)).to_be_visible()
                expect(candidate.locator('.complaint-card').filter(has_text='Условия вакансии отличаются').last).to_contain_text('Вакансия:');overflow(candidate)
                for source in ('offers','applications'):
                    navigate(candidate,source)
                    source_card=candidate.locator('article.panel').filter(has_text='Контекст жалобы: приглашение').first if source=='offers' else candidate.locator('article.panel').first
                    source_card.get_by_role('button',name='Пожаловаться',exact=True).click();context=candidate.get_by_role('dialog')
                    expect(context).to_contain_text('Компания: СигмаТех')
                    source_message='Проверка источника '+source+' '+str(width)+': изменились условия найма.'
                    context.get_by_label('Опишите ситуацию',exact=True).fill(source_message)
                    context.get_by_role('button',name='Отправить жалобу',exact=True).click();expect(context.get_by_role('heading',name='Жалоба отправлена')).to_be_visible()
                    context.get_by_role('button',name='Мои жалобы',exact=True).click()
                    expect(candidate.locator('.complaint-card').filter(has_text=source_message)).to_have_count(1);overflow(candidate)
                candidate.get_by_role('button',name='Выйти',exact=True).click();candidate.get_by_text('Другие демороли',exact=True).click();candidate.get_by_role('button',name='Кандидат · Тимур Ахметов',exact=True).click()
                navigate(candidate,'complaints');expect(candidate.get_by_role('heading',name='Жалоб пока нет',exact=True)).to_be_visible()
                checks.append({'width':width,'flow':'create, failed submit preserves draft, retry, moderator decision, history/reload, contextual vacancy/invitation/application, other candidate isolation','overflow':0})
                candidate.close();admin.close()
            assert not errors,errors;browser.close()
    finally:server.should_exit=True;thread.join(timeout=10);sock.close()
report={'checks':checks,'javascript_errors':errors,'scope':'Disposable local synthetic users; no public reports or user data were created.'}
(OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False))
