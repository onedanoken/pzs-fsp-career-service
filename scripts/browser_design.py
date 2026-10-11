"""Design regression on owned synthetic data: responsive layouts, motion and interaction."""
import json,os,socket,sys,tempfile,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
OUT=Path(os.environ.get('FSP_DESIGN_BROWSER_OUT',str(ROOT/'tmp/design-browser')));OUT.mkdir(parents=True,exist_ok=True)
checks=[];errors=[]
from scripts.postgres_test_context import postgres_test_schema

with tempfile.TemporaryDirectory(prefix='fsp-design-') as temp, postgres_test_schema():
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128);url='http://127.0.0.1:'+str(sock.getsockname()[1])
    os.environ.update(FSP_DEMO='1',FSP_TEST_LOGIN='1',FSP_ENABLE_ML='0',FSP_ALLOW_REGISTRATION='0',FSP_JOB_AUTOSTART='0',FSP_OLLAMA_URL='',FSP_SMTP_HOST='',FSP_DELIVERY_API_URL='',FSP_ALLOWED_ORIGINS=url,FSP_PUBLIC_URL=url,FSP_SECURE_COOKIE='0')
    import uvicorn
    from backend.main import app
    from playwright.sync_api import sync_playwright,expect
    server=uvicorn.Server(uvicorn.Config(app,log_level='error'));thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    try:
        for _ in range(100):
            if server.started:break
            time.sleep(.1)
        # Owned historical probe fixture: visible label changes, stored snapshot does not.
        import httpx
        with httpx.Client(base_url=url,trust_env=False) as client:
            me=client.post('/api/auth/demo',json={'account':'u_employer'}).json();client.headers['X-CSRF-Token']=me['csrf']
            need=client.post('/api/needs',json={'title':'Browser acceptance API 1791199194','description':'Разработка интеграций продуктовой платформы и API','team':'Команда инфраструктуры и интеграций','specialization':'backend','grade':'Middle','skills':['python'],'salary_min':120000,'salary_max':180000,'employer_contact':'hr@example.org'});need.raise_for_status()
            invitation=client.post('/api/invitations',json={'need_id':need.json()['id'],'candidate_id':'c_anna','message':'Обсудим разработку интеграций команды.','idempotency_key':'readable-demo-title-fixture'});invitation.raise_for_status()
        def navigate(page,route):
            if page.viewport_size['width']<=900:page.get_by_role('button',name='Открыть меню',exact=True).click()
            button=page.locator('nav button[data-route="'+route+'"]')
            if not button.is_visible():page.locator('.sidebar-more>summary').click()
            button.click();expect(page.locator('main h1').first).to_be_visible();expect(page.locator('main .loading')).to_have_count(0)
        def geometry(page):
            assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+1')
            if page.locator('.workspace').count():
                rect=page.locator('main h1').first.bounding_box();head=page.locator('.topbar').bounding_box()
                assert rect['y']>=head['y']+head['height']-1
        def capture(page,name):
            page.evaluate('window.scrollTo(0,0)');geometry(page);page.screenshot(path=str(OUT/name),full_page=True)
        with sync_playwright() as runtime:
            browser=runtime.chromium.launch(headless=True)
            for width in [320,390,750,1440]:
                context=browser.new_context(viewport={'width':width,'height':740 if width<=390 else 950});page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.goto(url)
                expect(page.get_by_role('button',name='Кандидат · Анна Смирнова',exact=True)).to_be_visible()
                expect(page.locator('.login-copy h1')).to_be_visible()
                if width<=390:
                    role=page.get_by_role('button',name='Кандидат · Анна Смирнова',exact=True).bounding_box()
                    assert role['y']+role['height']<=740,'First role action must be visible without scrolling'
                expect(page.locator('.scene-core')).to_contain_text('Паспорт результата')
                assert page.get_by_role('button',name='Остановить анимацию',exact=True).count()==0
                deadline=time.monotonic()+6
                while not page.evaluate("Array.from(document.querySelector('.scene-art').getAnimations({subtree:true})).every(a=>a.playState==='finished')"):
                    assert time.monotonic()<deadline,'Decorative animation must settle without a pause button'
                    time.sleep(.1)
                capture(page,'login-'+str(width)+'.png')
                page.get_by_role('button',name='Кандидат · Анна Смирнова',exact=True).click();expect(page.locator('main h1')).to_contain_text('Ваши навыки')
                expect(page.locator('.career-certificate-compact')).to_contain_text('Демо-уровень')
                assert '%' not in page.locator('.career-certificate-compact').inner_text(),'Do not display fabricated demo scores in the first-entry profile'
                capture(page,'home-'+str(width)+'.png')
                for route in ['profile','assessment','vacancies','offers','passports']:
                    navigate(page,route)
                    if route=='offers':
                        expect(page.locator('main')).to_contain_text('Backend-разработчик: интеграции и API')
                        assert 'Browser acceptance' not in page.locator('main').inner_text()
                    capture(page,'candidate-'+route+'-'+str(width)+'.png')
                page.locator('button.user-button').click();page.locator('.switcher button').filter(has_text='СигмаТех').click();expect(page.locator('.sidebar-context')).to_contain_text('РАБОТОДАТЕЛЯ')
                for route in ['home','candidates','company','vacancies','ats']:
                    navigate(page,route);capture(page,'employer-'+route+'-'+str(width)+'.png')
                page.locator('button.user-button').click();page.locator('.switcher button').filter(has_text='Эксперт ФСП').click();expect(page.locator('nav button[data-route="moderation"]')).to_have_count(1)
                for route in ['moderation','quality','integrations']:
                    navigate(page,route);geometry(page)
                if width==1440:
                    page.evaluate("document.documentElement.style.fontSize='28px'");geometry(page)
                page.get_by_role('button',name='Выйти',exact=True).click();context.close()
                checks.append({'width':width,'roles':3,'routes':14,'logo_unchanged':True,'finite_motion':True,'overflow':0})
            context=browser.new_context(viewport={'width':390,'height':950},reduced_motion='reduce');page=context.new_page();page.goto(url)
            expect(page.get_by_role('button',name='Кандидат · Анна Смирнова',exact=True)).to_be_visible()
            assert page.evaluate("document.getAnimations().filter(a=>a.playState==='running').length===0")
            assert page.locator('.scene-motion-control').is_hidden();capture(page,'reduced-motion-390.png');context.close()
            browser.close()
        assert not errors,errors
        report={'checks':checks,'reduced_motion':True,'javascript_errors':errors,'scope':'Owned headless Chromium, synthetic disposable data; visual/interaction regression, not human usability research.'}
        (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(report),flush=True)
    finally:server.should_exit=True;thread.join(timeout=10)
