"""Fresh candidate/employer journeys and expired-v2 history on disposable data."""
import json,os,re,socket,sys,tempfile,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
OUT=Path(os.environ.get('FSP_BALANCED_BROWSER_OUT',str(ROOT/'tmp/balanced-browser')));OUT.mkdir(parents=True,exist_ok=True)
checks=[];errors=[]
from scripts.postgres_test_context import postgres_test_schema

with tempfile.TemporaryDirectory(prefix='fsp-balanced-ui-') as temp, postgres_test_schema():
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128);url='http://127.0.0.1:'+str(sock.getsockname()[1])
    os.environ.update(FSP_DEMO='1',FSP_TEST_LOGIN='1',FSP_ENABLE_ML='0',FSP_ALLOW_REGISTRATION='0',FSP_JOB_AUTOSTART='1',FSP_OLLAMA_URL='',FSP_SMTP_HOST='',FSP_DELIVERY_API_URL='',FSP_ALLOWED_ORIGINS=url,FSP_PUBLIC_URL=url,FSP_SECURE_COOKIE='0',FSP_ASSESSMENT_PATH='work_sample_v3_balanced',FSP_MATCH_POLICY='category-evidence-v2')
    import uvicorn,httpx
    from backend.main import app
    from backend import db
    from playwright.sync_api import sync_playwright,expect
    server=uvicorn.Server(uvicorn.Config(app,log_level='error'));thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    try:
        for _ in range(100):
            if server.started:break
            time.sleep(.1)
        with httpx.Client(base_url=url,trust_env=False) as client:
            me=client.post('/api/auth/demo',json={'account':'u_anna'}).json();client.headers['X-CSRF-Token']=me['csrf']
            os.environ['FSP_ASSESSMENT_PATH']='work_sample_v2_auto'
            old=client.post('/api/assessments',json={'specialization':'qa','grade':'Junior','mode':'practice'}).json()['id']
            for _ in range(100):
                state=client.get('/api/assessments/'+old).json()
                if state['status']=='active':break
                time.sleep(.1)
            assert state['status']=='active'
            with db.connection() as conn:
                conn.execute('UPDATE assessment_sessions SET expires=? WHERE id=?',(time.time()-10,old))
                original=tuple(conn.execute('SELECT payload,status,version FROM assessment_sessions WHERE id=?',(old,)).fetchone())
            os.environ['FSP_ASSESSMENT_PATH']='work_sample_v3_balanced'
        def navigate(page,route):
            if page.viewport_size['width']<=900:page.get_by_role('button',name='Открыть меню',exact=True).click()
            button=page.locator('nav button[data-route="'+route+'"]')
            if not button.is_visible():page.locator('.sidebar-more>summary').click()
            button.click();expect(page.locator('main h1')).to_be_visible()
        def overflow(page):assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+1')
        with sync_playwright() as runtime:
            browser=runtime.chromium.launch(headless=True)
            for width,spec in [(320,'backend'),(390,'frontend'),(750,'qa'),(1440,'devops'),(1440,'ml'),(1440,'security')]:
                context=browser.new_context(viewport={'width':width,'height':950});page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.goto(url)
                page.get_by_role('button',name='Кандидат · Анна Смирнова',exact=True).click();expect(page.locator('main h1')).to_contain_text('Ваши навыки')
                navigate(page,'profile');expect(page.get_by_role('button',name='Сохранить профиль',exact=True)).to_be_visible()
                assert page.get_by_role('button',name='Сохранить подтверждённые сведения',exact=True).count()==0
                navigate(page,'assessment')
                if width==320:
                    page.locator('.history-panel>summary').click();page.locator('.attempt-row').first.click()
                    expect(page.locator('.assessment-heading')).to_contain_text('Время истекло')
                    assert 'Можно отвечать' not in page.locator('.assessment-heading').inner_text()
                    assert 'Можно отвечать' not in page.locator('.attempt-list').inner_text()
                    assert all(page.locator('.answer-options input').nth(i).is_disabled() for i in range(page.locator('.answer-options input').count()))
                    overflow(page);page.evaluate('window.scrollTo(0,0)');page.screenshot(path=str(OUT/'expired-320.png'),full_page=True)
                    page.get_by_role('button',name='Выбрать новую тренировку',exact=True).click()
                    expect(page.get_by_label('Режим',exact=False)).to_have_value('practice')
                    with db.connection() as conn:assert original==tuple(conn.execute('SELECT payload,status,version FROM assessment_sessions WHERE id=?',(old,)).fetchone())
                else:
                    if page.get_by_role('button',name='Выбрать новую попытку',exact=True).count():page.get_by_role('button',name='Выбрать новую попытку',exact=True).click()
                    elif page.get_by_role('button',name='Выбрать новую тренировку',exact=True).count():page.get_by_role('button',name='Выбрать новую тренировку',exact=True).click()
                page.get_by_label('Направление',exact=False).select_option(spec);page.get_by_label('Режим',exact=False).select_option('practice')
                page.get_by_role('button',name='Начать проверку',exact=True).click();expect(page.get_by_role('heading',name='Задание 1 из 12',exact=True)).to_be_visible(timeout=20000)
                with db.connection() as conn:
                    row=conn.execute('SELECT id,payload FROM assessment_sessions WHERE specialization=? AND status=\'active\' ORDER BY created_at DESC,id DESC LIMIT 1',(spec,)).fetchone();identity=row['id'];data=json.loads(row['payload'])
                    before=[tuple(x) for x in conn.execute("SELECT id,active FROM grade_certifications WHERE candidate_id='c_anna'")]
                assert data['assembly_plan']['count']==9
                for index,item in enumerate(data['items']):
                    if item.get('optional'):continue
                    if index:page.get_by_role('button',name='Перейти к заданию '+str(index+1),exact=True).click()
                    if item.get('choices'):
                        wanted=item['private']['expected'];wanted=wanted if isinstance(wanted,list) else [wanted]
                        for choice in item['choices']:
                            if choice['id'] in wanted:page.get_by_label(choice['text'],exact=True).check()
                    elif item['kind']=='work_sample':page.get_by_label('Исправленное решение (JSON)',exact=True).fill(item['private'].get('alternative_answer',item['private']['reference_answer']))
                    elif item['kind']=='sql':
                        threshold=re.search(r'amount ≥ (\d+)',item['prompt'])[1];page.get_by_label('Ответ',exact=True).fill(f"SELECT team,SUM(amount) FROM events WHERE status='ok' AND amount>={threshold} GROUP BY team")
                    else:page.get_by_label('Ответ',exact=True).fill(str(item['private']['expected']))
                    expect(page.get_by_text('Ответы сохранены',exact=True)).to_be_visible()
                    if item['stage']=='transfer':overflow(page);page.evaluate('window.scrollTo(0,0)');page.screenshot(path=str(OUT/('transfer-'+spec+'-'+str(width)+'.png')),full_page=True)
                expect(page.get_by_text('Обязательные ответы: 11 из 11',exact=True)).to_be_visible()
                page.get_by_role('button',name='Завершить и проверить',exact=True).click();page.get_by_role('button',name='Отправить на проверку',exact=True).click()
                expect(page.get_by_role('heading',name='Автоматическая проверка пройдена',exact=True)).to_be_visible(timeout=20000)
                state=page.request.get(url+'/api/assessments/'+identity).json();expect(page.get_by_text('Новое условие',exact=True)).to_be_visible()
                assert state['result']['assessment_path']['transfer_passed'] and state['skill_passport']['snapshot']['schema']=='fsp-skill-passport-v2'
                with db.connection() as conn:assert before==[tuple(x) for x in conn.execute("SELECT id,active FROM grade_certifications WHERE candidate_id='c_anna'")]
                page.get_by_label('Показывать работодателям в сервисе',exact=True).check();overflow(page);page.evaluate('window.scrollTo(0,0)');page.screenshot(path=str(OUT/('result-'+spec+'-'+str(width)+'.png')),full_page=True)
                page.reload();expect(page.get_by_role('heading',name='Автоматическая проверка пройдена',exact=True)).to_be_visible();overflow(page)
                page.locator('button.user-button').click();page.locator('.switcher button').filter(has_text='СигмаТех').click();navigate(page,'candidates')
                expect(page.locator('#matching-need')).to_have_value('demo_need_backend')
                page.get_by_role('button',name='Найти кандидатов',exact=True).click();expect(page.get_by_role('button',name='Найти кандидатов',exact=True)).to_be_enabled()
                expect(page.locator('main')).to_contain_text('откроются после принятия приглашения')
                assert 'Browser acceptance' not in page.locator('#matching-need').inner_text()
                overflow(page);page.evaluate('window.scrollTo(0,0)');page.screenshot(path=str(OUT/('employer-'+spec+'-'+str(width)+'.png')),full_page=True)
                page.get_by_role('button',name='Выйти',exact=True).click();context.close()
                checks.append({'width':width,'specialization':spec,'first_candidate_employer_routes':True,'new_form_items':12,'mandatory_answers':11,'transfer_and_passport':True,'practice_preserves_category':True,'history_preserved':True,'contacts_masked':True,'overflow':0})
            browser.close()
        assert not errors,errors
        report={'checks':checks,'javascript_errors':errors,'scope':'Owned browser on disposable synthetic data with reference answers. Functional first journeys, not unassisted human UX or real-device observation.'}
        (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(report),flush=True)
    finally:server.should_exit=True;thread.join(timeout=10)
