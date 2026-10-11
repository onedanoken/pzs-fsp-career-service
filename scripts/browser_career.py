"""Disposable UI acceptance; never run against real candidates or employers."""
import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = Path(os.environ.get('FSP_CAREER_BROWSER_OUT', str(ROOT/'tmp/career-browser')))
OUT.mkdir(parents=True, exist_ok=True)
checks, errors = [], []

from scripts.postgres_test_context import postgres_test_schema

with tempfile.TemporaryDirectory(prefix='fsp-career-ui-') as temporary, postgres_test_schema():
    os.environ.update( FSP_DEMO='1',FSP_TEST_LOGIN='1', FSP_ENABLE_ML='0', FSP_ALLOW_REGISTRATION='0', FSP_JOB_AUTOSTART='1')
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); sock.listen(128)
    url = 'http://127.0.0.1:'+str(sock.getsockname()[1])
    os.environ.update(FSP_ALLOWED_ORIGINS=url, FSP_PUBLIC_URL=url)
    os.environ['FSP_ASSESSMENT_PATH']='legacy'  # Frozen legacy-route regression.
    import uvicorn
    from playwright.sync_api import sync_playwright, expect
    from backend.main import app
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started: break
            time.sleep(.1)
        assert server.started
        def navigate(page, label):
            if page.viewport_size['width'] < 900:
                page.get_by_role('button', name='Открыть меню', exact=True).click()
            button=page.get_by_role('button',name=label,exact=True)
            if not button.is_visible():page.locator('.sidebar-more>summary').click()
            button.click()
            if page.viewport_size['width'] < 900:
                page.evaluate("""() => new Promise((resolve, reject) => {
                    const until=performance.now()+2000;
                    const check=()=>{if(document.querySelector('.sidebar').getBoundingClientRect().right<=1)resolve(true);
                        else if(performance.now()>until)reject(new Error('Mobile menu did not close'));
                        else requestAnimationFrame(check);};check();
                })""")
        def overflow(page):
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'), 'Horizontal overflow'
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True)
            employer = browser.new_page(viewport={'width':1440,'height':1000})
            candidate = browser.new_page(viewport={'width':320,'height':850})
            for page in [employer,candidate]: page.on('pageerror', lambda error: errors.append(str(error)))
            employer.goto(url); employer.get_by_role('button', name='СигмаТех', exact=False).click()
            expect(employer.get_by_role('heading', name='Найдите специалиста для команды')).to_be_visible()
            overflow(employer); employer.screenshot(path=str(OUT/'employer-home.png'), full_page=True)
            navigate(employer,'Поиск кандидатов'); employer.get_by_role('button',name='Создать запрос').click()
            dialog=employer.get_by_role('dialog')
            dialog.get_by_label('Название',exact=True).fill('Career browser contact')
            dialog.get_by_label('Какие задачи предстоят').fill('Разработка надёжных API и интеграций продуктовой платформы')
            dialog.get_by_label('Чем занимается команда').fill('Команда инфраструктуры и интеграций')
            dialog.get_by_label('Способ связи с работодателем').fill('hr@example.org')
            dialog.get_by_role('button',name='Сохранить запрос').click(); expect(dialog).not_to_be_visible(); employer.locator('details.hiring-activity>summary').click()
            employer.locator('details.hiring-activity').evaluate('(node)=>node.open=true');employer.get_by_label('Найм продолжается, условия и зарплата подтверждены',exact=True).check()
            employer.get_by_role('button',name='Подтвердить актуальность найма',exact=True).click()
            expect(employer.get_by_text('Работодатель подтвердил найм:',exact=False)).to_be_visible()
            employer.get_by_role('button',name='Найти кандидатов',exact=True).click()
            anna=employer.locator('article.panel').filter(has=employer.get_by_role('heading',name='Анна Смирнова'))
            expect(anna).to_be_visible(); anna.get_by_role('button',name='Пригласить',exact=True).click()
            dialog=employer.get_by_role('dialog')
            dialog.get_by_label('Следующий шаг после принятия',exact=True).fill('Встреча с командой и обсуждение задач без повторного общего теста')
            dialog.get_by_label('За сколько календарных дней вы свяжетесь с кандидатом',exact=True).fill('2')
            dialog.get_by_role('button',name='Отправить приглашение').click(); expect(dialog).not_to_be_visible()
            checks.append('Employer home, explicit hiring reconfirmation, invitation with next step and deadline')
            candidate.goto(url); candidate.get_by_text('Другие демороли',exact=True).click()
            candidate.get_by_role('button',name='Кандидат · Анна Смирнова',exact=False).click()
            expect(candidate.get_by_role('heading',name='Ваши навыки. Ваш следующий шаг.')).to_be_visible()
            expect(candidate.get_by_text('Синтетический пример · тест человеком не подтверждён',exact=True)).to_be_visible()
            overflow(candidate);candidate.screenshot(path=str(OUT/'candidate-home-320.png'),full_page=True)
            checks.append('320px candidate next steps with clearly marked synthetic certification')
            navigate(candidate,'Приглашения')
            card=candidate.locator('article.panel').filter(has_text='Career browser contact')
            expect(card).to_contain_text('Встреча с командой'); expect(card).to_contain_text('Срок начнётся после принятия')
            card.get_by_role('button',name='Принять и открыть контакты').click()
            expect(card).to_contain_text('Ожидается следующий контакт');overflow(candidate)
            navigate(employer,'Приглашения')
            employer_card=employer.locator('article.panel').filter(has_text='Career browser contact')
            employer_card.get_by_role('button',name='Связался с кандидатом',exact=True).click()
            expect(employer_card).to_contain_text('Работодатель отметил контакт')
            candidate.reload();navigate(candidate,'Приглашения')
            expect(card).to_contain_text('Работодатель отметил контакт')
            card.get_by_role('button',name='Со мной не связались',exact=True).click()
            expect(card).to_contain_text('Кандидат сообщил, что контакта не было')
            card.get_by_role('button',name='Контакт состоялся',exact=True).click()
            expect(card).to_contain_text('Кандидат подтвердил контакт');overflow(candidate)
            candidate.screenshot(path=str(OUT/'confirmed-contact-320.png'),full_page=True)
            checks.append('Mobile acceptance, employer statement, candidate dispute and separate confirmation')
            navigate(candidate,'Проверка навыков');candidate.get_by_label('Предполагаемый грейд').select_option('Junior');candidate.get_by_label('Режим').select_option('practice')
            candidate.get_by_label('Направление').select_option('frontend')
            candidate.get_by_role('button',name='Начать проверку',exact=True).click()
            expect(candidate.locator('.assessment-work .badge').filter(has_text='Можно отвечать')).to_be_visible(timeout=30000)
            with candidate.expect_response(lambda response:'/answer' in response.url and response.request.method=='PUT'):
                candidate.locator('.assessment-item').first.get_by_label('Ответ',exact=True).fill('12345')
            candidate.reload();expect(candidate.get_by_role('heading',name='Проверка навыков',exact=True)).to_be_visible()
            expect(candidate.locator('.assessment-item').first.get_by_label('Ответ',exact=True)).to_have_value('12345')
            candidate.get_by_role('button',name='Проверить и отправить ответы',exact=True).click()
            candidate.get_by_role('dialog').get_by_role('button',name='Отправить на проверку',exact=True).click()
            expect(candidate.get_by_text('Результат готов',exact=True)).to_be_visible(timeout=30000)
            expect(candidate.locator('.competency-row').first).to_be_visible();overflow(candidate)
            navigate(candidate,'Мой путь')
            expect(candidate.get_by_role('heading',name='Что показала последняя попытка')).to_be_visible()
            overflow(candidate);candidate.screenshot(path=str(OUT/'competency-feedback-320.png'),full_page=True)
            checks.append('Practice autosave/reload, observed competency feedback and next steps at 320px')
            assert not errors, errors
            browser.close()
    finally:
        server.should_exit=True;thread.join(timeout=10)

report={'checks':checks,'page_errors':errors,'data':'synthetic disposable accounts; not a human pilot'}
(OUT/'checks.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=True))
