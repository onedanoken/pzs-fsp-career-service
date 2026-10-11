"""External adapter runner: durable metadata only; no profile cache or raw secrets."""
import hashlib
import json
import os
import secrets
import socket
import time
from urllib.parse import urlsplit
import httpx
from pydantic import ValidationError
from .ats_client import UniversalATSClient
from .ats_odoo import OdooRecruitment, AdapterError
from .integrations import clean_html
from .recruiting import Need

PROTECTED={'192.144.15.182','111.88.146.220','vehicle-reid-lct.duckdns.org'}

def checked_url(value,allow_loopback=False):
    parsed=urlsplit(value)
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.hostname in PROTECTED:
        raise AdapterError('configuration_error')
    if parsed.scheme!='https' and not (allow_loopback and parsed.scheme=='http' and parsed.hostname in {'127.0.0.1','localhost','::1'}):raise AdapterError('configuration_error')
    try:addresses={entry[4][0] for entry in socket.getaddrinfo(parsed.hostname,parsed.port or (443 if parsed.scheme=='https' else 80))}
    except OSError:raise AdapterError('network_error') from None
    if addresses&PROTECTED:raise AdapterError('configuration_error')
    return value.rstrip('/')

def error_code(error):
    if isinstance(error,AdapterError):return error.code
    if isinstance(error,httpx.HTTPStatusError):
        return 'access_denied' if error.response.status_code in {401,403} else 'remote_error' if error.response.status_code>=500 else 'invalid_data'
    if isinstance(error,(httpx.TransportError,OSError)):return 'network_error'
    if isinstance(error,(ValueError,KeyError,TypeError,ValidationError)):return 'configuration_error'
    return 'sync_failed'

def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def synchronize(config,platform=None,vendor=None):
    """Run once. Scheduling belongs to the employer's integration operator."""
    owned_platform=platform is None;owned_vendor=vendor is None
    state=None;run_id=secrets.token_hex(16);started=False
    counts={'needs':0,'closed':0,'profiles':0,'stages':0,'deleted':0}
    try:
        if config.get('provider')!='odoo19':raise AdapterError('configuration_error')
        if platform is None:
            platform=UniversalATSClient(checked_url(config['platform_url'],config.get('allow_test_loopback',False)),os.environ[config['platform_token_env']])
        if vendor is None:
            vendor=OdooRecruitment(checked_url(config['provider_url'],config.get('allow_test_loopback',False)),os.environ[config['provider_token_env']],config['database'],int(config['company_id']))
        def report(status,code=None):
            body={'run_id':run_id,'provider':'odoo19','status':status,'counts':counts if status!='running' else {},'error_code':code}
            return platform.request('POST','/sync-report',body)
        result=report('running');started=True;namespace=result['connection_id']
        from .ats_state import AdapterState
        state=AdapterState(config,test_connection=not owned_platform and not owned_vendor)
        old=state.get_meta('namespace')
        if old and old!=namespace:raise AdapterError('configuration_error')
        state.set_meta('namespace',namespace)
        pulse=time.monotonic()
        def heartbeat():
            nonlocal pulse
            if time.monotonic()-pulse>25:report('running');pulse=time.monotonic()
        cursor=state.get_meta('events_cursor')
        # Drain durable removals first. Lost replies repeat idempotent vendor deletion/ack.
        for _ in range(1000):
            page=platform.events(cursor)
            for event in page['items']:
                if not event.get('withdrawal'):continue
                invitation=event['withdrawal']['invitation_id'];reference=namespace+':'+invitation
                deleted=vendor.forget(reference)
                if deleted.get('deleted') is not True:raise AdapterError('remote_error')
                platform.acknowledge_deletion(event['id'],'Odoo bridge removed its owned application and contact')
                state.remove_profile(invitation);counts['deleted']+=1;heartbeat()
            cursor=page.get('next_cursor')
            if cursor:state.set_meta('events_cursor',cursor)
            if not page.get('has_more'):break
        else:raise AdapterError('invalid_data')
        positions=vendor.positions();rules=config['job_rules'];seen=set();mapping={}
        db_ref=hashlib.sha256(config['database'].encode()).hexdigest()[:8]
        for position in positions:
            identity=str(position['id']);external='ODOO-'+db_ref+'-'+str(config['company_id'])+'-'+identity
            known=state.need(external)
            if identity not in rules:
                if known:platform.close_need(external,fingerprint(['close',run_id,external]));state.close_job(external);counts['closed']+=1;seen.add(external)
                continue
            seen.add(external);active=bool(position['active'] and position['no_of_recruitment']>0)
            if active:
                need=Need(**{**rules[identity],'title':position['name'],'description':clean_html(position.get('description','')).strip()}).model_dump()
                imported=platform.import_need(external,need,fingerprint(['need',run_id,external,need]));counts['needs']+=1
                state.save_job(external,position['id'],imported['id'])
                mapping[imported['id']]=(position['id'],True)
            elif known:
                platform.close_need(external,fingerprint(['close',run_id,external]));state.close_job(external);counts['closed']+=1;mapping[known]=(position['id'],False)
            heartbeat()
        # Only a successful, complete inventory may close previously imported missing jobs.
        for external,job_id,need_id,active in state.jobs():
            if external not in seen and active:platform.close_need(external,fingerprint(['missing',run_id,external]));state.close_job(external);counts['closed']+=1
            mapping.setdefault(need_id,(job_id,False))
        profiles=platform.profiles();allowed={entry['invitation_id'] for entry in profiles}
        for invitation,reference in state.profiles():
            if invitation not in allowed:
                if vendor.forget(reference).get('deleted') is not True:raise AdapterError('remote_error')
                state.remove_profile(invitation);counts['deleted']+=1;heartbeat()
        for entry in profiles:
            target=mapping.get(entry['need_id'])
            if not target:continue
            reference=namespace+':'+entry['invitation_id']
            # Recheck current permissions immediately before each external delivery.
            current=platform.profile(entry['invitation_id'])
            if current is None:continue
            if target[1]:
                application=vendor.upsert(reference,entry['candidate_id'],target[0],current['profile']);counts['profiles']+=1
                try:latest=platform.profile(entry['invitation_id'])
                except Exception:
                    vendor.forget(reference);raise
                if latest is None or fingerprint(latest['profile'])!=fingerprint(current['profile']):
                    vendor.forget(reference);state.remove_profile(entry['invitation_id']);counts['deleted']+=1;continue
                state.save_profile(entry['invitation_id'],reference)
            else:application=vendor.status(reference)
            if application:
                stage=vendor.status(reference) or application
                if stage.get('stage'):
                    key=fingerprint(['stage',entry['invitation_id'],stage['id'],stage['stage'],stage.get('stage_revision')])
                    platform.stage(entry['invitation_id'],str(stage['id']),stage['stage'],key);counts['stages']+=1
            heartbeat()
        state.commit();report('completed')
        return {'status':'completed','counts':counts}
    except Exception as error:
        if state:state.rollback()
        code=error_code(error)
        if started:
            try:report('failed',code)
            except Exception:pass
        raise AdapterError(code) from None
    finally:
        if state:state.close()
        if owned_vendor and vendor:vendor.close()
        if owned_platform and platform:platform.close()
