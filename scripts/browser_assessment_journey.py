"""Full candidate/reviewer/employer path on an isolated synthetic database."""
import json,os,socket,sys,tempfile,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));OUT=Path(os.environ.get('FSP_JOURNEY_BROWSER_OUT',str(ROOT/'tmp/journey-browser')));OUT.mkdir(parents=True,exist_ok=True)
checks=[];errors=[]
from scripts.postgres_test_context import postgres_test_schema

with tempfile.TemporaryDirectory(prefix='fsp-journey-ui-') as temporary, postgres_test_schema():
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128);url='http://127.0.0.1:'+str(sock.getsockname()[1])
    os.environ.update(FSP_DEMO='1',FSP_TEST_LOGIN='1',FSP_ENABLE_ML='0',FSP_ALLOW_REGISTRATION='0',FSP_JOB_AUTOSTART='1',FSP_OLLAMA_URL='',FSP_SMTP_HOST='',FSP_ALLOWED_ORIGINS=url,FSP_PUBLIC_URL=url,FSP_ASSESSMENT_PATH='work_sample_v1')
    import uvicorn
    from backend.main import app
    from backend import db
    from playwright.sync_api import sync_playwright,expect
    server=uvicorn.Server(uvicorn.Config(app,log_level='error'));thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    try:
        for _ in range(100):
            if server.started:break
            time.sleep(.1)
        assert server.started
        def navigate(page,route):
            if page.viewport_size['width']<900:page.get_by_role('button',name='Открыть меню',exact=True).click()
            button=page.locator('nav button[data-route="'+route+'"]')
            if not button.is_visible():page.locator('.sidebar-more>summary').click()
            button.click();expect(page.locator('.sidebar')).not_to_have_class('sidebar open')
        def overflow(page):assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        with sync_playwright() as runtime:
            browser=runtime.chromium.launch(headless=True)
            for width,mode in [(320,'practice'),(1440,'certification')]:
                candidate=browser.new_page(viewport={'width':width,'height':950});expert=browser.new_page(viewport={'width':1440,'height':950})
                for page in (candidate,expert):page.on('pageerror',lambda error:errors.append(str(error)))
                candidate.goto(url);candidate.get_by_role('button',name='Кандидат · Анна Смирнова',exact=True).click();navigate(candidate,'assessment')
                expect(candidate.locator('.assessment-steps')).to_contain_text('Рабочий кейс')
                candidate.get_by_label('Направление',exact=False).select_option('devops');candidate.get_by_label('Предполагаемый грейд',exact=False).select_option('Junior');candidate.get_by_label('Режим',exact=False).select_option(mode)
                candidate.get_by_role('button',name='Начать проверку',exact=True).click();expect(candidate.get_by_role('heading',name='Задание 1 из 5',exact=True)).to_be_visible(timeout=20000)
                with db.connection() as conn:
                    row=conn.execute("SELECT id,payload FROM assessment_sessions WHERE specialization='devops' AND mode=? ORDER BY created_at DESC LIMIT 1",(mode,)).fetchone();identity=row['id'];payload=json.loads(row['payload'])
                for index,item in enumerate(payload['items']):
                    if index:candidate.get_by_role('button',name='Перейти к заданию '+str(index+1),exact=True).click()
                    expect(candidate.get_by_role('heading',name=f'Задание {index+1} из 5',exact=True)).to_be_visible()
                    if item['kind']=='work_sample':
                        candidate.get_by_role('button',name='Начать с исходного варианта',exact=True).click()
                        candidate.get_by_label('Исправленное решение (JSON)',exact=True).fill(item['private']['reference_answer'])
                        expect(candidate.get_by_text('Ответы сохранены',exact=True)).to_be_visible(timeout=10000)
                        candidate.reload();expect(candidate.get_by_role('heading',name='Задание 4 из 5',exact=True)).to_be_visible()
                        expect(candidate.get_by_label('Исправленное решение (JSON)',exact=True)).to_have_value(item['private']['reference_answer'])
                        candidate.screenshot(path=str(OUT/f'work-{width}.png'),full_page=True)
                    elif item['kind']=='explanation':candidate.get_by_label('Ответ',exact=True).fill('Синтетический разбор: build предшествует независимым проверкам; release требует обе. Проверяем отказ и отрицательный сценарий. При одном исполнителе время и расписание меняются; это нужно измерить.')
                    elif item.get('choices'):
                        key=item['private']['expected'];ids=key if isinstance(key,list) else [key]
                        for option in item['choices']:
                            if option['id'] in ids:candidate.get_by_label(option['text'],exact=True).check()
                    else:candidate.get_by_label('Ответ',exact=True).fill(str(item['private']['expected']))
                    expect(candidate.get_by_text('Ответы сохранены',exact=True)).to_be_visible(timeout=10000);overflow(candidate)
                candidate.get_by_role('button',name='Проверить и отправить ответы',exact=True).click();candidate.get_by_role('dialog').get_by_role('button',name='Отправить на проверку',exact=True).click()
                expect(candidate.locator('.skill-passport')).to_be_visible(timeout=20000);expect(candidate.get_by_role('heading',name='Ожидается разбор',exact=True)).to_be_visible()
                with db.connection() as conn:assert not conn.execute('SELECT 1 FROM grade_certifications WHERE session_id=?',(identity,)).fetchone()
                candidate.screenshot(path=str(OUT/f'awaiting-{width}.png'),full_page=True)
                candidate.locator('.skill-passport').get_by_role('checkbox').check();expect(candidate.locator('.skill-passport').get_by_role('checkbox')).to_be_checked()
                expert.goto(url);expert.get_by_text('Другие демороли',exact=True).click();expert.get_by_role('button',name='Модератор · Эксперт ФСП',exact=True).click()
                card=expert.locator('.structured-review[data-session="'+identity+'"]');expect(card).to_be_visible()
                for criterion in payload['assessment_path']['rubric']:
                    card.get_by_label(criterion['label']+' — уровень',exact=True).select_option('3')
                    card.get_by_label(criterion['label']+' — основание',exact=True).fill('Синтетическое наблюдение для проверки формы. Не независимый экспертный пилот.')
                card.get_by_label('Итоговое обоснование',exact=True).fill('Структурированный разбор тестового артефакта; данные синтетические и не являются независимой калибровкой.')
                expert.screenshot(path=str(OUT/f'review-{width}.png'),full_page=True)
                card.get_by_role('button',name='Сохранить разбор по рубрике',exact=True).click();expect(card.get_by_text('Разбор сохранён',exact=True)).to_be_visible()
                candidate.get_by_role('button',name='Обновить разбор',exact=True).click();expect(candidate.get_by_role('heading',name='Разбор завершён',exact=True)).to_be_visible()
                expect(candidate.locator('.skill-passport')).to_contain_text('v2');expect(candidate.locator('.skill-passport')).to_contain_text('Демонстрационный результат');overflow(candidate)
                with db.connection() as conn:
                    passport=conn.execute('SELECT * FROM assessment_passports WHERE session_id=? ORDER BY revision DESC LIMIT 1',(identity,)).fetchone()
                    cert=conn.execute('SELECT 1 FROM grade_certifications WHERE session_id=? AND active=1',(identity,)).fetchone();assert bool(cert)==(mode=='certification')
                response=candidate.request.get(url+'/api/skill-passports/'+passport['id']+'/download');assert response.ok
                exported=response.json();assert exported['revision']==2 and exported['snapshot']['environment']=='demonstration' and 'reference_answer' not in response.text()
                candidate.screenshot(path=str(OUT/f'result-{width}.png'),full_page=True)
                employer=browser.new_page(viewport={'width':1440,'height':950});employer.goto(url);employer.get_by_role('button',name='Работодатель · СигмаТех',exact=True).click()
                navigate(employer,'candidates');employer.get_by_role('button',name='Создать запрос',exact=True).click();form=employer.get_by_role('dialog')
                form.get_by_label('Название',exact=True).fill('Паспорт навыков UI '+str(width));form.get_by_label('Какие задачи предстоят',exact=True).fill('Разработка надёжных API и интеграций платформы');form.get_by_label('Чем занимается команда',exact=True).fill('Команда платформы и интеграций');form.get_by_label('Способ связи с работодателем',exact=True).fill('hr@example.org')
                form.get_by_role('button',name='Сохранить запрос',exact=True).click();expect(form).not_to_be_visible();employer.locator('details.hiring-activity>summary').click();employer.locator('details.hiring-activity').evaluate('(node)=>node.open=true');employer.get_by_label('Найм продолжается, условия и зарплата подтверждены',exact=True).check();employer.get_by_role('button',name='Подтвердить актуальность найма',exact=True).click();employer.get_by_role('button',name='Найти кандидатов',exact=True).click()
                profile=employer.locator('article.panel').filter(has=employer.get_by_role('heading',name='Анна Смирнова',exact=True));profile.get_by_role('button',name='Паспорта навыков',exact=True).click()
                modal=employer.get_by_role('dialog');expect(modal).to_contain_text('Демонстрационный результат');expect(modal).to_contain_text('Разбор завершён');overflow(employer)
                employer.screenshot(path=str(OUT/f'employer-{width}.png'),full_page=True)
                candidate.locator('.skill-passport').get_by_role('checkbox').uncheck();expect(candidate.locator('.skill-passport').get_by_role('checkbox')).not_to_be_checked()
                assert employer.request.get(url+'/api/skill-passports/'+passport['id']).status==404
                navigate(candidate,'passports');expect(candidate.get_by_role('heading',name='Паспорта навыков',exact=True)).to_be_visible()
                checks.append({'width':width,'mode':mode,'stages':3,'items':5,'passport_revision':2,'category_only_after_review':True,'owner_publication_and_revocation':True,'employer_ui':True,'overflow':0})
                candidate.close();expert.close();employer.close()
            assert not errors,errors;browser.close()
    finally:server.should_exit=True;thread.join(timeout=10);sock.close()
report={'checks':checks,'javascript_errors':errors,'scope':'Scripted synthetic answers and reviewer actions on a disposable local database; not a real human pilot or independent calibration.'}
(OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False))
