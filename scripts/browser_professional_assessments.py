"""UI acceptance of owned cases on disposable synthetic data, not a human pilot."""
import json,os,socket,sys,tempfile,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
OUT=Path(os.environ.get('FSP_PROFESSIONAL_BROWSER_OUT',str(ROOT/'tmp/professional-browser')));OUT.mkdir(parents=True,exist_ok=True)
checks=[];errors=[]
from scripts.postgres_test_context import postgres_test_schema

with tempfile.TemporaryDirectory(prefix='fsp-professional-ui-') as temporary, postgres_test_schema():
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128);url='http://127.0.0.1:'+str(sock.getsockname()[1])
    os.environ.update(FSP_DEMO='1',FSP_TEST_LOGIN='1',FSP_ENABLE_ML='0',FSP_ALLOW_REGISTRATION='0',FSP_JOB_AUTOSTART='1',FSP_OLLAMA_URL='',FSP_ALLOWED_ORIGINS=url,FSP_PUBLIC_URL=url)
    import uvicorn
    os.environ['FSP_ASSESSMENT_PATH']='legacy'  # Frozen legacy-route regression.
    from backend.main import app
    from backend import db
    from playwright.sync_api import sync_playwright,expect
    server=uvicorn.Server(uvicorn.Config(app,log_level='error'));thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    try:
        for _ in range(100):
            if server.started:break
            time.sleep(.1)
        assert server.started
        with sync_playwright() as runtime:
            browser=runtime.chromium.launch(headless=True)
            for spec,width in [('devops',320),('ml',390),('security',1440)]:
                context=browser.new_context(viewport={'width':width,'height':950});page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(url);page.get_by_role('button',name='Кандидат · Анна Смирнова',exact=True).click()
                if width<900:page.get_by_role('button',name='Открыть меню',exact=True).click()
                page.locator('nav button[data-route="assessment"]').click();expect(page.locator('.assessment-setup')).to_be_visible()
                page.get_by_label('Направление',exact=False).select_option(spec)
                page.get_by_label('Предполагаемый грейд',exact=False).select_option('Senior')
                page.get_by_label('Режим',exact=False).select_option('practice')
                page.get_by_role('button',name='Начать проверку',exact=True).click()
                expect(page.get_by_role('heading',name='Задание 1 из 6',exact=True)).to_be_visible(timeout=20000)
                with db.connection() as conn:
                    row=conn.execute("SELECT id,payload FROM assessment_sessions WHERE specialization=? AND mode='practice' ORDER BY created_at DESC LIMIT 1",(spec,)).fetchone();identity=row['id'];data=json.loads(row['payload'])
                    before=[tuple(r.values()) for r in conn.execute("SELECT * FROM grade_certifications WHERE candidate_id='c_anna' ORDER BY id")] if db.DATABASE_URL else [tuple(r) for r in conn.execute("SELECT * FROM grade_certifications WHERE candidate_id='c_anna' ORDER BY id")]
                assert data['blueprint_version']=='competencies-2026-10-v5'
                for index,item in enumerate(data['items']):
                    if index:page.get_by_role('button',name='Перейти к заданию '+str(index+1),exact=True).click()
                    expect(page.get_by_role('heading',name=f'Задание {index+1} из 6',exact=True)).to_be_visible()
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                    key=item['private'].get('expected')
                    if item['kind'] in ['choice','multi']:
                        ids=key if isinstance(key,list) else [key]
                        # Deliberately wrong for the single-choice observation to exercise guidance.
                        if item['kind']=='choice':ids=[next(x['id'] for x in item['choices'] if x['id'] not in ids)]
                        for option in item['choices']:
                            if option['id'] in ids:page.get_by_label(option['text'],exact=True).check()
                    elif item['kind']=='explanation':page.get_by_label('Ответ',exact=True).fill('Синтетический ответ для проверки интерфейса: инвариант, ограничение, сценарий отказа и измеримая проверка. Независимый эксперт его не оценивал.')
                    else:page.get_by_label('Ответ',exact=True).fill(str(key))
                    expect(page.get_by_text('Ответы сохранены',exact=True)).to_be_visible(timeout=10000)
                    if index==1:
                        page.screenshot(path=str(OUT/f'{spec}-question-{width}.png'),full_page=True)
                        page.reload();expect(page.get_by_role('heading',name='Задание 2 из 6',exact=True)).to_be_visible()
                        expect(page.get_by_label('Ответ',exact=True)).to_have_value(str(key))
                page.get_by_role('button',name='Проверить и отправить ответы',exact=True).click()
                page.get_by_role('dialog').get_by_role('button',name='Отправить на проверку',exact=True).click()
                expect(page.locator('.competency-list')).to_be_visible(timeout=20000)
                expect(page.get_by_text('Ожидает эксперта',exact=True)).to_be_visible()
                with db.connection() as conn:
                    actual=json.loads(conn.execute('SELECT payload FROM assessment_sessions WHERE id=?',(identity,)).fetchone()[0])
                    after=[tuple(r) for r in conn.execute("SELECT * FROM grade_certifications WHERE candidate_id='c_anna' ORDER BY id")]
                assert before==after,'Practice changed grade'
                assert actual['result']['calibration']=='pilot_unvalidated' and actual['result']['score']<100
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                page.screenshot(path=str(OUT/f'{spec}-result-{width}.png'),full_page=True)
                checks.append({'specialization':spec,'width':width,'question_count':6,'result_score':actual['result']['score'],'grade_preserved':True,'expert_review_pending':True,'overflow':0});context.close()
            assert not errors,errors;browser.close()
    finally:server.should_exit=True;thread.join(timeout=10);sock.close()
report={'checks':checks,'javascript_errors':errors,'scope':'Scripted synthetic practice responses on a temporary local DB; not a human assessment or expert calibration.'}
(OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False))
