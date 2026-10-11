import ssl
from types import SimpleNamespace
import pytest
from backend import identity
from backend.jobs import JobError


@pytest.fixture
def mailbox(monkeypatch):
    messages=[];calls=[]
    class Transport:
        def __init__(self,host,port,**options):calls.append(('connect',host,port,options))
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def starttls(self,**options):calls.append(('starttls',options))
        def login(self,*args):calls.append(('login',))
        def send_message(self,message):messages.append(message)
    monkeypatch.setattr(identity.smtplib,'SMTP_SSL',Transport)
    monkeypatch.setattr(identity.smtplib,'SMTP',Transport)
    monkeypatch.delenv('FSP_DELIVERY_API_URL',raising=False)
    monkeypatch.setenv('FSP_SMTP_HOST','smtp.mail.ru')
    monkeypatch.setenv('FSP_SMTP_FROM','sender@example.test')
    monkeypatch.setenv('FSP_SMTP_USER','sender@example.test')
    monkeypatch.setenv('FSP_SMTP_PASSWORD','disposable-test-only')
    monkeypatch.delenv('FSP_SMTP_PORT',raising=False)
    monkeypatch.setenv('FSP_SMTP_SSL','0')
    monkeypatch.setenv('FSP_SMTP_STARTTLS','1')
    return messages,calls


def deliver():
    return identity.send_mail(SimpleNamespace(id='isolated-test'),dict(to='recipient@example.test',subject='Проверка',text='Одноразовая ссылка'))


def test_implicit_tls_validates_certificate_and_preserves_standard_headers(mailbox,monkeypatch):
    monkeypatch.setenv('FSP_SMTP_SSL','1');monkeypatch.setenv('FSP_SMTP_STARTTLS','0')
    assert deliver()=={'transport':'smtp'}
    messages,calls=mailbox
    assert calls[0][2]==465
    context=calls[0][3]['context']
    assert context.check_hostname and context.verify_mode==ssl.CERT_REQUIRED
    assert not any(c[0]=='starttls' for c in calls)
    assert messages[0]['Date'] and messages[0]['Message-ID'].endswith('@example.test>')


def test_starttls_before_authentication_with_certificate_validation(mailbox):
    deliver();messages,calls=mailbox
    assert [c[0] for c in calls]==['connect','starttls','login']
    assert calls[1][1]['context'].check_hostname and messages


def test_external_plaintext_does_not_send_password_or_message(mailbox,monkeypatch):
    monkeypatch.setenv('FSP_SMTP_STARTTLS','0')
    with pytest.raises(JobError):deliver()
    messages,calls=mailbox
    assert not messages and not any(c[0]=='login' for c in calls)
