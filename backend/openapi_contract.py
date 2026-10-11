"""Explicit ATS result schemas and shared API errors for generated OpenAPI."""
from fastapi.openapi.utils import get_openapi

def install(app):
    def schema():
        if app.openapi_schema:return app.openapi_schema
        result=get_openapi(title=app.title,version=app.version,description=app.description,routes=app.routes)
        schemas=result.setdefault('components',{}).setdefault('schemas',{})
        schemas['APIError']={'type':'object','required':['detail'],'properties':{'detail':{'oneOf':[{'type':'string'},{'type':'array','items':{'type':'object','additionalProperties':True}}]}}}
        definitions={
            'ATSNeedResult':{'id':'string','external_id':'string','state':'string','event_id':'string'},
            'ATSStageResult':{'invitation_id':'string','stage':'string','event_id':'string'},
            'ATSClosedNeed':{'id':'string','state':'string'},
            'ATSSyncStatus':{'run_id':'string','status':'string','connection_id':'string'},
        }
        for name,fields in definitions.items():schemas[name]={'type':'object','required':list(fields),'properties':{k:{'type':v} for k,v in fields.items()}}
        pagination={'next_cursor':{'type':['string','null'],'description':'Persist this opaque cursor, including on pages with zero visible items'},'has_more':{'type':'boolean'},'next_offset':{'type':'integer','description':'Legacy pagination; prefer cursor'}}
        schemas['ATSProfilesPage']={'type':'object','required':['items','limit','offset','next_offset','next_cursor','has_more'],'properties':{'items':{'type':'array','items':{'type':'object','required':['invitation_id','need_id','candidate_id','profile','consent','schema_version'],'properties':{'invitation_id':{'type':'string'},'need_id':{'type':'string'},'candidate_id':{'type':'string'},'profile':{'type':'object','description':'Only explicitly selected fields; never assessment answers or raw resume','additionalProperties':True},'consent':{'type':'object','additionalProperties':True},'schema_version':{'type':'string'}}}},'limit':{'type':'integer'},'offset':{'type':'integer'},**pagination}}
        schemas['ATSDeletionAcknowledgement']={'type':'object','required':['deleted','note','at'],'properties':{'deleted':{'type':'boolean','const':True},'note':{'type':'string','maxLength':500},'at':{'type':'string'}}}
        schemas['ATSWithdrawal']={'type':'object','required':['event_type','invitation_id','reason','occurred_at'],'properties':{'event_type':{'type':'string','const':'consent_revoked'},'invitation_id':{'type':'string'},'reason':{'type':'string'},'occurred_at':{'type':'string'}}}
        schemas['ATSEventsPage']={'type':'object','required':['items','next_offset','next_cursor','has_more'],'properties':{'items':{'type':'array','items':{'type':'object','required':['id','direction','status','created_at'],'properties':{'id':{'type':'string'},'direction':{'type':'string','enum':['inbound','outbound']},'external_key':{'type':'string'},'status':{'type':'string'},'error':{'type':['string','null']},'created_at':{'type':'string'},'completed_at':{'type':['string','null']},'withdrawal':{'$ref':'#/components/schemas/ATSWithdrawal'},'acknowledgement':{'oneOf':[{'$ref':'#/components/schemas/ATSDeletionAcknowledgement'},{'type':'null'}]}}}},**pagination}}
        schemas['ATSDeletionResult']={'type':'object','required':['ok','event_id','acknowledgement'],'properties':{'ok':{'type':'boolean'},'event_id':{'type':'string'},'acknowledgement':{'$ref':'#/components/schemas/ATSDeletionAcknowledgement'}}}
        schemas['ATSProfile']=schemas['ATSProfilesPage']['properties']['items']['items']
        schemas['ATSEventsPage']['properties']['items']['items']['properties']['direction']['enum'].append('sync')
        outputs={('/api/ats/v1/needs','post'):('201','ATSNeedResult'),('/api/ats/v1/stages','post'):('200','ATSStageResult'),('/api/ats/v1/needs/{external_id}','delete'):('200','ATSClosedNeed'),('/api/ats/v1/profiles','get'):('200','ATSProfilesPage'),('/api/ats/v1/profiles/{identity}','get'):('200','ATSProfile'),('/api/ats/v1/sync-report','post'):('200','ATSSyncStatus'),('/api/ats/v1/events','get'):('200','ATSEventsPage'),('/api/ats/v1/events/{identity}/ack','post'):('200','ATSDeletionResult')}
        descriptions={401:'Authentication expired, absent or invalid',403:'Role, scope, organization, consent or CSRF denies access',404:'Entity is unavailable to this account',409:'Version, state or idempotency conflict',422:'Request validation failed',429:'Quota or cooldown exceeded',503:'Configured dependency temporarily unavailable; retain saved work'}
        for path,methods in result['paths'].items():
            for method,operation in methods.items():
                if method not in {'get','post','put','patch','delete'}:continue
                for code,description in descriptions.items():operation['responses'][str(code)]={'description':description,'content':{'application/json':{'schema':{'$ref':'#/components/schemas/APIError'}}}}
                if (path,method) in outputs:
                    status,name=outputs[(path,method)];operation['responses'][status]['content']={'application/json':{'schema':{'$ref':'#/components/schemas/'+name}}}
        app.openapi_schema=result;return result
    app.openapi=schema
