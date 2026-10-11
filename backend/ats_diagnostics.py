"""Organization-owned integration activity, without credentials or profile data."""
import json,time,logging
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool
from .auth import session_user, require_role
from .db import connection, audit

ERRORS={401:'Ключ недействителен или истёк',403:'Недостаточно прав или разрешение отозвано',404:'Связь с объектом не найдена',409:'Данные или состояние изменились',422:'Проверьте параметры запроса',429:'Лимит запросов; повторите позже'}

def record_exchange(client,operation,method,status):
    with connection() as conn:
        audit(conn,client['payload']['owner_id'],'ats_exchange',client['id'],{'operation':operation,'method':method,'status_code':status})

class ObservedATSRoute(APIRoute):
    def get_route_handler(self):
        handler=super().get_route_handler()
        async def observed(request:Request):
            status=500
            try:
                response=await handler(request);status=response.status_code;return response
            except HTTPException as error:status=error.status_code;raise
            except RequestValidationError:status=422;raise
            finally:
                client=getattr(request.state,'ats_client',None)
                if client and request.url.path.startswith('/api/ats/v1/'):
                    try:await run_in_threadpool(record_exchange,client,self.path,request.method,status)
                    except Exception:logging.getLogger(__name__).warning('ATS activity recording is temporarily unavailable')
        return observed

router=APIRouter(prefix='/api',tags=['ATS synchronization diagnostics'])

def owned_client(conn,identity,user):
    row=conn.execute('SELECT * FROM ats_connections WHERE id=? AND org_id=?',(identity,user['org_id'])).fetchone()
    if not row:raise HTTPException(404,'Подключение не найдено')
    return row

@router.get('/ats-connections/{identity}/diagnostics')
def diagnostics(identity:str,user=Depends(session_user)):
    require_role(user,'employer')
    with connection() as conn:
        row=owned_client(conn,identity,user);settings=json.loads(row['payload'])
        exchanges=conn.execute("SELECT * FROM audit WHERE action='ats_exchange' AND entity_id=? ORDER BY id DESC LIMIT 30",(identity,)).fetchall()
        activity=[]
        for item in exchanges:
            meta=json.loads(item['metadata']);status=meta['status_code']
            activity.append({'at':item['created_at'],'operation':meta['operation'],'method':meta['method'],'status_code':status,'success':status<400,'message':None if status<400 else ERRORS.get(status,'Временный сбой обмена')})
        events=conn.execute('SELECT id,direction,status,created_at,completed_at,payload FROM integration_events WHERE connection_id=? ORDER BY created_at DESC,id DESC LIMIT 30',(identity,)).fetchall()
        timeline=[]
        for event in events:
            payload=json.loads(event['payload'])
            kind='withdrawal' if payload.get('event_type')=='consent_revoked' else 'synchronization' if event['direction']=='sync' else 'exchange'
            timeline.append({'id':event['id'],'kind':kind,'status':event['status'],'created_at':event['created_at'],'completed_at':event['completed_at'],'reason':payload.get('reason') if kind=='withdrawal' else None})
        counts={item['status']:item['count'] for item in conn.execute('SELECT status,COUNT(*) AS count FROM integration_events WHERE connection_id=? GROUP BY status',(identity,))}
        successful=conn.execute("SELECT MAX(completed_at) AS at FROM integration_events WHERE connection_id=? AND status IN ('completed','acknowledged')",(identity,)).fetchone()['at']
        sync=settings.get('sync',{})
        if sync.get('status')=='running' and sync.get('lease_until',0)<time.time():sync={**sync,'status':'failed','error_code':'worker_interrupted'}
        return {'connection_id':identity,'provider':row['provider'],'active':settings.get('active',False),'last_activity_at':activity[0]['at'] if activity else None,'last_completed_exchange_at':successful,'awaiting_ack':counts.get('awaiting_ack',0),'completed_events':counts.get('completed',0),'sync':sync,'activity':activity,'events':timeline,'notice':'Активный ключ разрешает API-доступ. Обмен подтверждают события и успешный запуск адаптера; подтверждение удаления является заявлением внешней системы.'}
