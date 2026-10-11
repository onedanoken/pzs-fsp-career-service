"""Owned API/React/QA mini-models. User code, commands and URLs are never executed."""
import copy,json,secrets
from .assessment_catalog import GRADES
from .work_reference_v3 import api_reference,react_reference

VERSION='bounded-projects-2026-v3'
FAMILIES={'backend':'api_retry_patch','frontend':'react_reducer_patch','qa':'api_regression_suite'}

def replay_api(c,strategy='atomic_record',namespace='tenant_key',mutation=None):
    stored={};output=[]
    for row in c['requests']:
        key=row['key'] if namespace=='global' or mutation=='global_key' else (row['tenant'],row['key'])
        allowed=row['authorized'] and not row['revoked'] and (not c['require_expiry'] or row['expires']>c['now'] or mutation=='ignore_expiry')
        if mutation=='cached_authorization' and key in stored:allowed=True
        if not allowed:output.append(403);continue
        if key not in stored:stored[key]=row['body'];output.append(202 if strategy=='transactional_outbox' else 201)
        else:output.append(200 if stored[key]==row['body'] or mutation=='ignore_conflict' else 409)
    return output

def replay_react(c):
    state={};closed=set()
    for row in c['events']:
        channel=row['channel']
        if row['type']=='issue':closed.discard(channel);state[channel]={'request':row['id'],'status':'loading','items':[]}
        elif row['type']=='unmount':closed.add(channel);state[channel]['status']='closed';state[channel]['items']=[]
        elif channel not in closed and row['id']==state[channel]['request']:
            state[channel]={'request':row['id'],'status':'ready' if 200<=row['status']<300 else 'error','items':row['items'] if row['status']==200 else []}
    return state

def reference(c,alternative=False):
    family=c['family']
    if family=='api_retry_patch':
        strategy='transactional_outbox' if alternative else 'atomic_record'
        return {'strategy':strategy,'namespace':'tenant_key','statuses':api_reference(c,strategy)}
    if family=='react_reducer_patch':return {'strategy':'abort_and_id' if alternative else 'latest_issued','states':react_reference(c)}
    statuses=api_reference(c)
    # Multiple independently labelled suites are valid if they kill all required mutants.
    indices=list(range(len(statuses)))
    if not alternative:
        chosen=set()
        for mutant in c['mutants']:
            wrong=replay_api(c,mutation=mutant)
            chosen.add(next(i for i,(a,b) in enumerate(zip(statuses,wrong)) if a!=b))
        chosen.add(next(i for i,s in enumerate(statuses) if s==201));chosen.add(next(i for i,s in enumerate(statuses) if s==403))
        indices=sorted(chosen)
    return {'tests':[{'request_id':c['requests'][i]['id'],'expected_status':statuses[i]} for i in indices]}

def pack(c,transfer=False):
    family=c['family'];spec=c['specialization']
    rules='Проверяйте authorized, revoked и, если require_expiry, expires>now на каждом запросе до чтения результата повтора. Ключ состоит из tenant и key. Первый допустимый запрос сохраняет body, повтор того же body возвращает200, другой body409. Отказ403 не записывает операцию. atomic_record создаёт201; transactional_outbox создаёт202. Оба плана фиксируют ключ и операцию атомарно.'
    if family=='react_reducer_patch':rules='Отдельное состояние каждого channel. issue задаёт latest id и loading/[]; ответ применяется только к этому id. 200 даёт ready/items; другие2xx ready/[]; остальные error/[]. unmount закрывает канал; поздние ответы не меняют closed/[]. Стратегии latest_issued и abort_and_id допустимы; только отмена запроса без проверкиid недостаточна.'
    elif family=='api_regression_suite':rules+=' Для этого стенда используется atomic_record. Напишите независимые ожидаемые статусы. Набор должен различать правильную модель и перечисленные mutants, включать успех и отказ. Любой достаточно полный корректный набор принимается.'
    expected=reference(c)
    starter={'tests':[]} if spec=='qa' else {'strategy':'cache_only','namespace':'global','statuses':[]} if spec=='backend' else {'strategy':'abort_only','states':{}}
    visible={k:v for k,v in c.items() if k not in ['family','specialization','grade','level']}
    return {'id':'item_'+secrets.token_hex(8),'kind':'work_sample','competency':'work_sample','competencies':{'backend':['api_contracts','debugging'],'frontend':['state','api_contracts'],'qa':['test_design','risk_analysis']}[spec],'stage':'transfer' if transfer else 'work_sample','family':spec+':'+family,'version':VERSION,'prompt':('Изменённое условие: проверьте решение заново.\n' if transfer else 'Мини-проект: исправьте контракт и проверяемый артефакт.\n')+rules+'\nУсловия:\n'+json.dumps(visible,ensure_ascii=False,indent=2),'starter':json.dumps(starter,ensure_ascii=False,indent=2),'requirements':['Верните JSON с полями: '+', '.join(expected),'Только указанные варианты стратегии; код не выполняется.','Порядок статусов запросов соответствует requests; поля states сравниваются по содержимому.'],'followup':'Объясните, как сохранён инвариант после изменения условия, и границы выбранного плана.','max_length':5000,'private':{'evaluator':'bounded_v3','condition':c,'expected':expected,'reference_answer':json.dumps(expected,ensure_ascii=False,indent=2),'alternative_answer':json.dumps(reference(c,True),ensure_ascii=False,indent=2)}}

def generate_case(spec,grade,rng):
    level=GRADES.index(grade)+1;c={'family':FAMILIES[spec],'specialization':spec,'grade':grade,'level':level,'now':100,'require_expiry':level==4}
    if spec=='frontend':
        rows=[]
        for channel in ['main']+(['aside'] if level>=2 else []):
            rows.extend([{'channel':channel,'type':'issue','id':1},{'channel':channel,'type':'issue','id':2},{'channel':channel,'type':'response','id':2,'status':rng.choice([200,204,503]),'items':[rng.randint(1,99)]},{'channel':channel,'type':'response','id':1,'status':200,'items':[999]}])
        if level==4:rows.extend([{'channel':'main','type':'unmount'},{'channel':'main','type':'response','id':2,'status':200,'items':[888]}])
        c['events']=rows
    else:
        key='k'+str(rng.randint(1,999));body=rng.randint(1,50)
        descriptions=[('create','A',body,True,False),('repeat','A',body,True,False),('conflict','A',body+1,True,False),('tenant','B',body+2,True,False),('forbidden','A',body,False,False),('revoked','A',body,True,True),('expired','A',body,True,False)]
        c['requests']=[{'id':name,'tenant':tenant,'key':key,'body':amount,'authorized':auth,'revoked':revoked,'expires':99 if name=='expired' else 110} for name,tenant,amount,auth,revoked in descriptions]
        c['mutants']=['global_key','ignore_conflict','cached_authorization']+(['ignore_expiry'] if c['require_expiry'] else [])
    return pack(c)

def transfer(item):
    c=copy.deepcopy(item['private']['condition'])
    if c['family']=='react_reducer_patch':
        c['events'].extend([{'channel':'transfer','type':'issue','id':3},{'channel':'transfer','type':'response','id':2,'status':200,'items':[777]}])
    else:
        c['require_expiry']=True;c['now']=160;c['mutants']=['global_key','ignore_conflict','cached_authorization','ignore_expiry']
        for row in c['requests']:
            if row['id'] in ['tenant','conflict','revoked']:row['expires']=180
        c['requests'].append({'id':'fresh','tenant':'C','key':'fresh','body':1,'authorized':True,'revoked':False,'expires':180})
        c['requests'].append({'id':'fresh_conflict','tenant':'A','key':c['requests'][0]['key'],'body':999,'authorized':True,'revoked':False,'expires':180})
    return pack(c,True)

def evaluate(item,answer):
    from .work_samples import unique_object
    c=item['private']['condition'];checks={'format':False,'contract':False,'independent_expected':False,'negative_checks':False}
    try:
        if not isinstance(answer,str) or len(answer)>5000:raise ValueError('size')
        value=json.loads(answer,object_pairs_hook=unique_object)
        if not isinstance(value,dict) or set(value)!=set(item['private']['expected']):raise ValueError('keys')
        family=c['family'];checks['format']=True
        if family=='api_retry_patch':
            checks['contract']=value['strategy'] in ['atomic_record','transactional_outbox'] and value['namespace']=='tenant_key'
            statuses=value['statuses']
            checks['independent_expected']=isinstance(statuses,list) and all(type(x) is int for x in statuses) and statuses==api_reference(c,value['strategy'])
            checks['negative_checks']=checks['contract'] and replay_api(c,value['strategy'],value['namespace'])==statuses
        elif family=='react_reducer_patch':
            checks['contract']=value['strategy'] in ['latest_issued','abort_and_id']
            checks['independent_expected']=json.dumps(value['states'],sort_keys=True)==json.dumps(react_reference(c),sort_keys=True)
            checks['negative_checks']=checks['contract'] and replay_react(c)==value['states']
        else:
            tests=value['tests'];ids={r['id']:i for i,r in enumerate(c['requests'])};expected=api_reference(c)
            if not isinstance(tests,list) or not 1<=len(tests)<=40 or any(not isinstance(t,dict) or set(t)!={'request_id','expected_status'} or not isinstance(t['request_id'],str) or type(t['expected_status']) is not int for t in tests):raise ValueError('tests')
            if len({t['request_id'] for t in tests})!=len(tests) or any(t['request_id'] not in ids for t in tests):raise ValueError('ids')
            checks['contract']=any(t['expected_status']==201 for t in tests) and any(t['expected_status']==403 for t in tests)
            checks['independent_expected']=all(t['expected_status']==expected[ids[t['request_id']]] for t in tests)
            checks['negative_checks']=all(any(replay_api(c,mutation=mutant)[ids[t['request_id']]]!=t['expected_status'] for t in tests) for mutant in c['mutants'])
    except (ValueError,TypeError,KeyError,RecursionError,OverflowError):pass
    return {'score':round(sum(checks.values())*100/len(checks),2),'method':'bounded_project_replay_v3','checks':[{'id':key,'passed':valid} for key,valid in checks.items()],'all_checks_passed':all(checks.values())}
