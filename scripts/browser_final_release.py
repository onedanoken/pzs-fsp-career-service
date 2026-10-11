"""Real browser inputs on a disposable PostgreSQL schema; no production accounts."""
import json
import os
import re
import socket
import sys
import threading
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.postgres_test_context import postgres_test_schema


def serve(app,sock=None):
    import uvicorn
    if sock is None:
        sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
    server=uvicorn.Server(uvicorn.Config(app,log_level='error'))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    for _ in range(100):
        if server.started:break
        time.sleep(.05)
    assert server.started
    return server,thread,sock.getsockname()[1]


def main():
    import httpx
    from playwright.sync_api import sync_playwright,expect
    out=ROOT/'.local/final-browser';out.mkdir(exist_ok=True)
    checks=[];errors=[]
    with postgres_test_schema():
        os.environ.update(FSP_DEMO='0',FSP_EMAIL_REQUIRED='1',FSP_LEGACY_EMAIL_AUTH='0',
                          FSP_TEST_LOGIN='0',FSP_JOB_AUTOSTART='0',FSP_ALLOW_REGISTRATION='1',FSP_ENABLE_ML='0')
        app_socket=socket.socket();app_socket.bind(('127.0.0.1',0));app_socket.listen(128)
        url='http://127.0.0.1:'+str(app_socket.getsockname()[1])
        os.environ.update(FSP_PUBLIC_URL=url,FSP_ALLOWED_ORIGINS=url)
        from backend.main import app
        server,thread,port=serve(app,app_socket)
        from scripts.mock_fsp_id import mock_app
        # Reserve provider socket first so issuer and discovery agree.
        probe=socket.socket();probe.bind(('127.0.0.1',0));provider_port=probe.getsockname()[1];probe.close()
        issuer='http://localhost:'+str(provider_port)+'/realms/fsp'
        provider=mock_app(issuer,'Mock-browser-password-123',url+'/api/fsp/oidc/callback')
        import uvicorn
        mockserver=uvicorn.Server(uvicorn.Config(provider,host='127.0.0.1',port=provider_port,log_level='error'))
        mockthread=threading.Thread(target=mockserver.run,daemon=True);mockthread.start()
        for _ in range(100):
            if mockserver.started:break
            time.sleep(.05)
        os.environ.update(FSP_OIDC_ISSUER=issuer,FSP_OIDC_CLIENT_ID='fsp-career-mock',FSP_OIDC_DEMO_ISSUER='1',FSP_OIDC_BACKCHANNEL_BASE='')
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch()
                for width in [320,390,750,1440]:
                    ctx=browser.new_context(viewport={'width':width,'height':1000});page=ctx.new_page()
                    def record_error(e):
                        errors.append(str(e));(out/'js-errors.json').write_text(json.dumps(errors,ensure_ascii=False),encoding='utf-8')
                    page.on('pageerror',record_error)
                    page.goto(url);page.get_by_role('button',name='Создать аккаунт',exact=True).click()
                    page.get_by_label('Как вас зовут').fill('Проверка интерфейса')
                    page.get_by_label('Логин',exact=True).fill('review.'+str(width))
                    page.get_by_label('Email',exact=True).fill('review-'+str(width)+'@example.test')
                    page.get_by_label('Пароль',exact=True).last.fill('Browser-test-password-123')
                    page.get_by_role('checkbox').check();page.get_by_role('button',name='Зарегистрироваться',exact=True).click()
                    expect(page.get_by_role('heading',name='Подтвердите email')).to_be_visible()
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    page.screenshot(path=str(out/('email-'+str(width)+'.png')),full_page=True)
                    from backend import db
                    with db.connection() as conn:
                        row=conn.execute("SELECT j.payload FROM jobs j JOIN users u ON u.id=j.actor_id WHERE u.username=? AND j.kind='mail.send'",('review.'+str(width),)).fetchone()
                        token=json.loads(row[0])['text'].split('#verify=')[1].split('\n')[0]
                    # The private test mailbox supplies the link, not a production UI bypass.
                    page.goto(url+'/#verify='+token)
                    page.wait_for_function("async()=>{const r=await fetch('/api/auth/me');return r.ok&&(await r.json()).email_verified}")
                    page.reload()
                    expect(page.get_by_role('heading',name='Подтвердите email')).not_to_be_visible()
                    page.screenshot(path=str(out/('confirmed-'+str(width)+'.png')),full_page=True)
                    expect(page.get_by_role('main')).to_be_visible()
                    if not page.locator('.sidebar').is_visible():
                        page.get_by_role('button',name='Открыть меню',exact=True).click()
                        expect(page.locator('.sidebar')).to_be_visible()
                        page.get_by_role('button',name='Закрыть меню',exact=True).click()
                    page.goto(url+'/candidate/profile');page.goto(url+'/candidate/vacancies');page.go_back()
                    assert '/candidate/profile' in page.url
                    checks.append('email confirmation and history at '+str(width))
                    if width==1440:
                        page.goto(url+'/candidate/fsp')
                        # Initiate with the authenticated page's public API; then use provider UI inputs.
                        start=page.evaluate("async()=>{const r=await fetch('/api/fsp/oidc/start');if(!r.ok)throw Error('OIDC start');return r.json()}")
                        page.goto(start['url']);expect(page.get_by_role('heading',name='Тестовый ФСП ID')).to_be_visible()
                        page.get_by_label('Тестовый логин').fill('fsp-test-001');page.get_by_label('Пароль',exact=True).fill('Mock-browser-password-123')
                        page.get_by_role('button',name='Подтвердить тестовую привязку').click()
                        page.wait_for_timeout(700)
                        from urllib.parse import urlparse
                        current=urlparse(page.url);(out/'oidc-debug.txt').write_text(current.netloc+current.path+'\n'+page.locator('body').inner_text()[:1000],encoding='utf-8')
                        page.wait_for_url(url+'/**')
                        identities=page.evaluate("async()=> (await fetch('/api/fsp/identities')).json()")
                        assert identities['items'][0]['status']=='demo'
                        checks.append('OIDC real browser login/callback: mock never verified')
                    ctx.close()
                browser.close()
            assert not errors,errors
            (out/'report.json').write_text(json.dumps({'checks':checks,'js_errors':errors,'scope':'Disposable normal accounts; private fixture mailbox; real browser inputs; no human UX pilot'},ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'checks':len(checks),'js_errors':len(errors)}))
        except Exception:
            if False:
                from urllib.parse import urlparse
                current=urlparse(page.url)
                (out/'last-failure.txt').write_text(str(page.viewport_size)+'\n'+current.netloc+current.path+'\n'+page.locator('body').inner_text()[:2000],encoding='utf-8')
            raise
        finally:
            mockserver.should_exit=True;server.should_exit=True;mockthread.join(5);thread.join(5)


if __name__=='__main__':main()
