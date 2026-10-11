"""All-role automatic journey and reserve video on disposable demo data."""
import json,os,re,socket,sys,tempfile,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));OUT=Path(os.environ.get('FSP_CLOSEOUT_BROWSER_OUT',str(ROOT/'tmp/closeout-browser')));OUT.mkdir(parents=True,exist_ok=True)
checks=[];errors=[]
from scripts.postgres_test_context import postgres_test_schema

with tempfile.TemporaryDirectory(prefix='fsp-final-ui-') as temporary, postgres_test_schema():
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128);url='http://127.0.0.1:'+str(sock.getsockname()[1])
    os.environ.update(FSP_DEMO='1',FSP_TEST_LOGIN='1',FSP_ENABLE_ML='0',FSP_ALLOW_REGISTRATION='0',FSP_JOB_AUTOSTART='1',FSP_OLLAMA_URL='',FSP_SMTP_HOST='',FSP_DELIVERY_API_URL='',FSP_PUBLIC_URL=url,FSP_ALLOWED_ORIGINS=url,FSP_ASSESSMENT_PATH='work_sample_v2_auto')
    import uvicorn
    from backend.main import app
    from backend import db
    from playwright.sync_api import sync_playwright,expect
    server=uvicorn.Server(uvicorn.Config(app,log_level='error'));thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    try:
        for _ in range(100):
            if server.started:break
            time.sleep(.1)
        def navigate(page,route):
            if page.viewport_size['width']<=900:page.get_by_role('button',name='Открыть меню',exact=True).click()
            button=page.locator('nav button[data-route="'+route+'"]')
            if not button.is_visible():page.locator('.sidebar-more>summary').click()
            button.click();expect(page.locator('main h1')).to_be_visible()
        def overflow(page):assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+1')
        with sync_playwright() as runtime:
            browser=runtime.chromium.launch(headless=True)
            for width,spec,mode in [(320,'backend','practice'),(390,'frontend','practice'),(750,'qa','practice'),(1440,'devops','certification'),(1440,'ml','practice'),(1440,'security','practice')]:
                record=width==1440 and spec=='devops'
                context=browser.new_context(viewport={'width':width,'height':950},record_video_dir=str(OUT/'video') if record else None,record_video_size={'width':1440,'height':950} if record else None)
                page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.goto(url)
                page.get_by_role('button',name='Кандидат · Анна Смирнова',exact=True).click();navigate(page,'assessment')
                page.get_by_label('Направление',exact=False).select_option(spec);page.get_by_label('Предполагаемый грейд',exact=False).select_option('Junior');page.get_by_label('Режим',exact=False).select_option(mode)
                page.get_by_role('button',name='Начать проверку',exact=True).click();expect(page.get_by_role('heading',name='Задание 1 из 5',exact=True)).to_be_visible(timeout=20000)
                with db.connection() as conn:
                    row=conn.execute('SELECT id,payload FROM assessment_sessions WHERE specialization=? ORDER BY created_at DESC LIMIT 1',(spec,)).fetchone();identity=row['id'];data=json.loads(row['payload'])
                for index,item in enumerate(data['items']):
                    if index:page.get_by_role('button',name='Перейти к заданию '+str(index+1),exact=True).click()
                    if item['kind']=='explanation':continue
                    answer=item['private']['reference_answer'] if item['kind']=='work_sample' else json.dumps(item['private']['expected']) if item['kind']=='multi' else str(item['private']['expected'])
                    if item['kind']=='sql':
                        threshold=re.search(r'amount ≥ (\d+)',item['prompt'])[1];answer=f"SELECT team,SUM(amount) FROM events WHERE status='ok' AND amount>={threshold} GROUP BY team"
                    if item.get('choices'):
                        expected=item['private']['expected'];expected=expected if isinstance(expected,list) else [expected]
                        for choice in item['choices']:
                            if choice['id'] in expected:page.get_by_label(choice['text'],exact=True).check()
                    elif item['kind']=='work_sample':
                        page.get_by_label('Исправленное решение (JSON)',exact=True).fill(answer)
                        expect(page.get_by_text('Ответы сохранены',exact=True)).to_be_visible();page.screenshot(path=str(OUT/('case-'+spec+'-'+str(width)+'.png')),full_page=True)
                    else:page.get_by_label('Ответ',exact=True).fill(answer)
                    expect(page.get_by_text('Ответы сохранены',exact=True)).to_be_visible()
                page.get_by_role('button',name='Завершить и проверить',exact=True).click();page.get_by_role('button',name='Отправить на проверку',exact=True).click()
                expect(page.get_by_role('heading',name='Автоматическая проверка пройдена',exact=True)).to_be_visible(timeout=20000)
                overflow(page);page.screenshot(path=str(OUT/('result-'+spec+'-'+str(width)+'.png')),full_page=True)
                if record:page.screenshot(path=str(OUT/'result-devops-viewport.png'))
                state=page.request.get(url+'/api/assessments/'+identity).json();assert state['result']['passed'] and state['result']['assessment_path']['review_required'] is False
                assert state['skill_passport']['snapshot']['category_issued']==(mode=='certification')
                page.reload();expect(page.get_by_role('heading',name='Автоматическая проверка пройдена',exact=True)).to_be_visible();overflow(page)
                if record:
                    page.get_by_role('button',name='Сохранить уровень и перейти к профилю',exact=True).click();expect(page.locator('main h1')).to_contain_text('профиль')
                    page.locator('button.user-button').click();page.locator('.switcher button').filter(has_text='СигмаТех').click();expect(page.locator('button.user-button')).to_contain_text('СигмаТех');navigate(page,'candidates')
                    page.screenshot(path=str(OUT/'employer-matching-1440.png'),full_page=True)
                    navigate(page,'ats-demo');expect(page.locator('main h1')).to_contain_text('ATS');page.screenshot(path=str(OUT/'ats-demo-1440.png'),full_page=True)
                    page.get_by_role('button',name='Начать демонстрацию',exact=True).click()
                    def next_action(name):
                        page.locator('.mock-next').get_by_role('button',name=name,exact=True).click()
                    for name in ['Импортировать позицию','Пригласить кандидата','Продолжить как кандидат','Принять приглашение','Разрешить передачу в ATS','Продолжить как работодатель','Проверить передачу','Изменить этап']:
                        next_action(name)
                    expect(page.locator('.mock-record')).to_contain_text('Собеседование');page.screenshot(path=str(OUT/'ats-stage-1440.png'),full_page=True);page.screenshot(path=str(OUT/'ats-stage-viewport.png'))
                    for name in ['Показать временный сбой','Повторить обмен','Продолжить как кандидат','Отозвать передачу','Продолжить как работодатель','Обработать отзыв','Закрыть позицию']:
                        next_action(name)
                    expect(page.get_by_role('heading',name='Сценарий завершён',exact=True)).to_be_visible()
                    page.get_by_role('button',name='Вернуться в исходный кабинет',exact=True).click()
                    navigate(page,'home');page.locator('button.user-button').click();page.locator('.switcher button').filter(has_text='Эксперт ФСП').click();expect(page.locator('button.user-button')).to_contain_text('Эксперт ФСП');navigate(page,'integrations')
                    expect(page.get_by_role('heading',name='Состояние подключения')).to_be_visible();page.screenshot(path=str(OUT/'integration-hub-1440.png'),full_page=True);page.screenshot(path=str(OUT/'integration-hub-viewport.png'))
                    video=page.video;context.close();video.save_as(str(OUT/'fsp-career-reserve-demo.webm'))
                else:context.close()
                checks.append({'width':width,'specialization':spec,'mode':mode,'automatic_category':mode=='certification','optional_explanation_skipped':True,'passport':True,'reload':True,'overflow':0});print(json.dumps(checks[-1]),flush=True)
            browser.close()
        assert not errors,errors
        (OUT/'report.json').write_text(json.dumps({'checks':checks,'javascript_errors':errors,'origin':'controlled_synthetic_browser_journeys','human_first_use_study':False,'video':'fsp-career-reserve-demo.webm'},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    finally:server.should_exit=True;thread.join(timeout=10)
