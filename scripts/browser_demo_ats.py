"""Full UI-only mock ATS journey on an isolated server; no human pilot claim."""
import json,os,socket,sys,tempfile,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
OUT=Path(os.environ.get('FSP_MOCK_BROWSER_OUT',str(ROOT/'tmp/mock-ats-browser')));OUT.mkdir(parents=True,exist_ok=True)
checks=[];errors=[]
from scripts.postgres_test_context import postgres_test_schema

with tempfile.TemporaryDirectory(prefix='fsp-demo-ats-ui-') as temporary, postgres_test_schema():
    os.environ.update(FSP_DEMO='1',FSP_TEST_LOGIN='1',FSP_ENABLE_ML='0',FSP_ALLOW_REGISTRATION='0',FSP_JOB_AUTOSTART='0',FSP_OLLAMA_URL='')
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128);url='http://127.0.0.1:'+str(sock.getsockname()[1]);os.environ.update(FSP_ALLOWED_ORIGINS=url,FSP_PUBLIC_URL=url)
    import uvicorn
    from backend.main import app
    from playwright.sync_api import sync_playwright,expect
    server=uvicorn.Server(uvicorn.Config(app,log_level='error'));thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    try:
        for _ in range(100):
            if server.started:break
            time.sleep(.1)
        assert server.started
        def overflow(page):assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),'Horizontal overflow'
        def next_button(page,name):return page.locator('.mock-next').get_by_role('button',name=name,exact=True)
        def switch(page,role):
            next_button(page,'Продолжить как '+role).click()
            expect(page.get_by_role('heading',name='Демонстрация ATS',exact=True)).to_be_visible()
        with sync_playwright() as runtime:
            browser=runtime.chromium.launch(headless=True)
            page=browser.new_page(viewport={'width':320,'height':900});page.on('pageerror',lambda error:errors.append(str(error)));page.goto(url)
            page.get_by_role('button',name='Работодатель · СигмаТех',exact=True).click()
            page.get_by_role('button',name='Открыть меню',exact=True).click();page.locator('.sidebar-more>summary').click();page.get_by_role('button',name='Демонстрация ATS',exact=True).click()
            page.get_by_role('button',name='Начать демонстрацию',exact=True).click()
            expect(page.get_by_text('Симуляция · только тестовые данные',exact=True)).to_be_visible()
            next_button(page,'Импортировать позицию').click();next_button(page,'Пригласить кандидата').click()
            expect(page.locator('.mock-next')).to_contain_text('Принять приглашение')
            page.reload();expect(page.get_by_role('heading',name='Демонстрация ATS',exact=True)).to_be_visible();overflow(page)
            page.locator('.mock-actions>summary').click();page.locator('.mock-actions').get_by_role('button',name='Проверить передачу',exact=True).click();expect(page.get_by_text('Копии кандидата нет',exact=True)).to_be_visible();page.locator('.mock-actions>summary').click()
            switch(page,'кандидат');next_button(page,'Принять приглашение').click()
            # The candidate has accepted but ATS transfer still needs a separate action.
            expect(page.get_by_text('Копии кандидата нет',exact=True)).to_be_visible();next_button(page,'Разрешить передачу в ATS').click()
            switch(page,'работодатель');next_button(page,'Проверить передачу').click()
            expect(page.locator('.mock-record')).to_contain_text('candidate-ats@example.invalid')
            next_button(page,'Изменить этап').click();expect(page.locator('.mock-record')).to_contain_text('Собеседование');overflow(page)
            page.screenshot(path=str(OUT/'copied-stage-320.png'),full_page=True)
            next_button(page,'Показать временный сбой').click();expect(page.locator('.mock-next')).to_contain_text('503')
            next_button(page,'Повторить обмен').click();expect(page.locator('.mock-record')).to_have_count(1);expect(page.locator('.mock-next').get_by_role('alert')).to_have_count(0)
            switch(page,'кандидат');next_button(page,'Отозвать передачу').click()
            switch(page,'работодатель');next_button(page,'Обработать отзыв').click();expect(page.get_by_text('Копии кандидата нет',exact=True)).to_be_visible()
            next_button(page,'Закрыть позицию').click();expect(page.get_by_role('heading',name='Сценарий завершён',exact=True)).to_be_visible();overflow(page)
            page.screenshot(path=str(OUT/'complete-320.png'),full_page=True)
            page.get_by_role('button',name='Вернуться в исходный кабинет',exact=True).click();expect(page.get_by_role('heading',name='Найдите специалиста для команды')).to_be_visible()
            checks.append('320px UI-only full cycle, persistence on reload, role switches, real consent gates, controlled 503/retry, removal/ack, closure and return to original account')
            other=browser.new_page(viewport={'width':1440,'height':1000});other.on('pageerror',lambda error:errors.append(str(error)));other.goto(url);other.get_by_role('button',name='Работодатель · СигмаТех',exact=True).click();other.locator('.sidebar-more>summary').click();other.get_by_role('button',name='Демонстрация ATS',exact=True).click();other.get_by_role('button',name='Начать демонстрацию').click()
            expect(other.get_by_text('Копии кандидата нет',exact=True)).to_be_visible();expect(other.locator('.mock-record')).to_have_count(0);overflow(other);other.screenshot(path=str(OUT/'new-isolated-1440.png'),full_page=True)
            checks.append('Independent browser gets its own isolated sandbox')
            assert not errors,errors;browser.close()
    finally:server.should_exit=True;thread.join(timeout=10);sock.close()
report={'checks':checks,'javascript_errors':errors,'scope':'Synthetic browser automation; not a real user pilot or commercial ATS connection.'}
(OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=True))
