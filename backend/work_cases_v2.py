"""Owned, bounded work models: two families per role and visible grade conditions.

No arbitrary code or network execution. Difficulty mapping is a team proposal;
reference/model validation does not claim psychometric or independent human proof.
"""
import ipaddress,json,math,secrets
from .assessment_catalog import GRADES

VERSION='work-models-2026-v2'
FAMILIES={
 'backend':['event_ledger','cursor_page'],
 'frontend':['response_state','form_contract'],
 'qa':['mutation_suite','metamorphic_events'],
 'devops':['rollout_budget','failed_dag'],
 'ml':['temporal_labels','threshold_cost'],
 'security':['object_access','network_rules'],
}
SOURCES={'backend':['python-docs','postgres-select','python-interview-yakimka'],
 'frontend':['mdn-fetch','h5bp-interviews','react-interviews'],
 'qa':['h5bp-interviews','qa-interview-bank','istqb-reference'],
 'devops':['k8s-deploy','k8s-resources','sysadmin-interviews','dnx-devops-interviews'],
 'ml':['sklearn-metrics','sklearn-pitfalls','data-science-interviews'],
 'security':['owasp-authorization','owasp-wstg']}

def dumps(v):return json.dumps(v,ensure_ascii=False,indent=2)
def normalize_number(value,c):
    if value is None:return c['allow_null']
    if type(value) is int:number=value
    elif isinstance(value,str) and c['numeric_strings'] and value.strip().isdigit():
        try:number=int(value.strip())
        except ValueError:return False
    else:return False
    return c['low']<=number<=c['high']

def ledger(rows,base,maximum=None):
    seen={};balance=base;accepted=[]
    for row in rows:
        identity=row['id'];amount=row['amount']
        if identity in seen:continue
        seen[identity]=amount
        if balance+amount<0 or maximum is not None and balance+amount>maximum:continue
        balance+=amount;accepted.append(identity)
    return balance,accepted

def solve(c):
    f=c['family']
    if f=='event_ledger':
        balance,ids=ledger(c['events'],c['initial'],c.get('maximum'))
        return {'balance':balance,'accepted_ids':ids}
    if f=='cursor_page':
        ordered=sorted(c['records'],key=lambda x:(x['score'],x['id']))
        rows=[r for r in ordered if (r['score'],r['id'])>tuple(c['cursor']) and (not c.get('snapshot_filter') or not r['deleted'] and r['created']<=c['as_of'])][:c['limit']]
        return {'page_ids':[r['id'] for r in rows],'next_cursor':[rows[-1]['score'],rows[-1]['id']] if rows else None}
    if f=='response_state':
        latest={};views={}
        for event in c['events']:
            name=event['channel']
            if event['type']=='issue':latest[name]=event['id'];views[name]={'id':event['id'],'state':'loading','items':[]}
            elif event['id']==latest.get(name):views[name]={'id':event['id'],'state':'ready' if 200<=event['status']<300 else 'error','items':event['items'] if event['status']==200 else []}
        return {'views':views}
    if f=='form_contract':
        accepted=[];normalized={};seen=set()
        for row in c['rows']:
            age=row['age'];name=row['name'].strip();email=row['email'].strip().lower()
            valid=bool(name) and type(age) is int and c['minimum_age']<=age<=c['maximum_age']
            valid=valid and (not c['email_required'] or email.count('@')==1 and '.' in email.split('@')[-1])
            valid=valid and (not c['consent_required'] or row['consent'] is True)
            if c.get('unique_email'):valid=valid and email not in seen
            if valid:accepted.append(row['id']);normalized[row['id']]={'name':name,'email':email};seen.add(email)
        return {'accepted_ids':accepted,'normalized':normalized}
    if f=='mutation_suite':
        inputs=[c['low']-1,c['low'],c['high'],c['high']+1,None,str(c['low']),True,'bad']
        return {'tests':[{'input':v,'accepted':normalize_number(v,c)} for v in inputs]}
    if f=='metamorphic_events':
        rows=list({r['id']:r for r in c['events']}.values())
        valid=[r for r in rows if (not c.get('positive_only') or r['amount']>0) and (not c.get('accepted_only') or r['status']=='accepted') and (not c.get('time_filter') or r['time']<=c['as_of'])]
        total=sum(r['amount'] for r in valid);result={'base_total':total,'reordered_total':total,'duplicate_total':total,'extra_total':total+c['extra']['amount']}
        if c.get('time_filter'):
            groups={}
            for r in valid:groups[r['team']]=groups.get(r['team'],0)+r['amount']
            result['team_totals']=groups
        return result
    if f=='rollout_budget':
        surge=math.ceil(c['replicas']*c['surge_percent']/100)
        cpu_slots=c['free_cpu']//c['cpu_per_pod'];ram_slots=c['free_ram']//c['ram_per_pod']
        fresh=min(surge,cpu_slots,ram_slots)
        if c.get('zone_slots'):
            fresh=max(n for n in range(fresh+1) if all(sum(i%len(c['zone_slots'])==z for i in range(n))<=slots for z,slots in enumerate(c['zone_slots'])))
        unavailable=math.floor(c['replicas']*c['unavailable_percent']/100)
        return {'max_new_pods':fresh,'peak_cpu':fresh*c['cpu_per_pod'],'peak_ram':fresh*c['ram_per_pod'],'available_floor':c['replicas']-unavailable}
    if f=='failed_dag':
        end={};success={};pending=set(c['jobs'])
        while pending:
            ready=sorted(k for k in pending if all(p in end for p in c['jobs'][k]['needs']))
            if not ready:raise ValueError('Invalid generated DAG')
            for name in ready:
                job=c['jobs'][name];end[name]=max([end[p] for p in job['needs']] or [0])+job['duration']
                success[name]=name not in c['failed'] and all(success[p] for p in job['needs']);pending.remove(name)
        return {'critical_seconds':end['release'],'release_allowed':success['release']}
    if f=='temporal_labels':
        test_groups={r['group'] for r in c['rows'] if r['time']>=c['cutoff']}
        train=[r['id'] for r in c['rows'] if r['time']<c['cutoff']-c['gap'] and (not c['require_available_labels'] or r['label_time']<=c['cutoff']) and (not c['disjoint_groups'] or r['group'] not in test_groups)]
        test=[r['id'] for r in c['rows'] if r['time']>=c['cutoff']]
        features=[name for name,available in c['feature_catalog'].items() if available<=c['prediction_time']] if c.get('feature_catalog') else c['available_features']
        return {'train_ids':train,'test_ids':test,'fit_on':'train','features':features}
    if f=='threshold_cost':
        options=[]
        for row in c['candidates']:
            recall=row['tp']/(row['tp']+row['fn'])
            if recall>=c['min_recall'] and row['tp']+row['fp']<=c['review_budget']:
                options.append((row['fp']*c['fp_cost']+row['fn']*c['fn_cost'],row['threshold']))
        cost,threshold=min(options)
        return {'threshold':threshold,'cost':cost}
    if f=='object_access':
        ids=[]
        for r in c['requests']:
            valid=r['tenant']==r['object_tenant'] and r['active'] and r['action'] in r['permissions']
            if c['require_owner']:valid=valid and r['subject']==r['owner']
            if c['require_scope']:valid=valid and r['action'] in r['scopes']
            if c['require_expiry']:valid=valid and r['expires']>c['now'] and not r['blocked']
            if valid:ids.append(r['id'])
        return {'allowed_ids':ids}
    if f=='network_rules':
        ids=[]
        for row in c['connections']:
            allow=ipaddress.ip_address(row['ip']) in ipaddress.ip_network(c['cidr']) and row['port'] in c['ports']
            if c['check_protocol']:allow=allow and row['protocol']=='tcp'
            if c['denies_override']:allow=allow and row['ip'] not in c['denied_ips']
            if c.get('check_expiry'):allow=allow and row['expires']>c['now']
            if allow:ids.append(row['id'])
        return {'allowed_ids':ids}
    raise ValueError('Unknown family')

def generate_case(spec,grade,rng,family=None):
    level=GRADES.index(grade)+1;family=family or rng.choice(FAMILIES[spec])
    if family not in FAMILIES[spec]:raise ValueError('Family/role mismatch')
    c={'specialization':spec,'grade':grade,'level':level,'family':family}
    if family=='event_ledger':
        c['initial']=rng.randint(20,60);events=[]
        for i in range(3+level):events.append({'id':'e'+str(i),'amount':rng.randint(-30,30)})
        events.insert(2,dict(events[0]));events.append({'id':'overdraw','amount':-200})
        if level>=3:events.append({'id':events[1]['id'],'amount':events[1]['amount']+5})
        c['events']=events;rule='Обработайте события по порядку. Каждый id рассматривается только при первом появлении, включая отклонённый. Повтор или конфликт этого id игнорируется. Событие, делающее баланс отрицательным, отклоняется. Начальный баланс initial.'
        if level==4:c['maximum']=c['initial']+rng.randint(5,20);rule+=' Также отклоняйте превышение maximum.'
        follow='Как изменится гарантия при хранении обработанных id только в памяти?'
    elif family=='cursor_page':
        c['records']=[{'id':i,'score':rng.randint(1,3+level)} for i in range(1,6+2*level)]
        ordered=sorted(c['records'],key=lambda r:(r['score'],r['id']));cursor=ordered[rng.randint(0,len(ordered)-2)]
        c.update(cursor=[cursor['score'],cursor['id']],limit=level+1)
        rule='Сортировка по (score,id) по возрастанию. Страница содержит строго следующие после cursor записи, не более limit. next_cursor — пара последней записи страницы, либо null. Порядок page_ids важен.'
        if level==4:
            c.update(snapshot_filter=True,as_of=20)
            for row in c['records']:row.update(deleted=rng.choice([True,False]),created=rng.randint(10,30))
            rule+=' Снимок исключает deleted=true и created>as_of до отбора страницы.'
        follow='Что произойдёт, если score записи изменился между страницами?'
    elif family=='response_state':
        events=[]
        for channel in ['main']+(['sidebar'] if level>=3 else []):
            for i in [1,2]:events.append({'channel':channel,'type':'issue','id':i})
            events.extend([{'channel':channel,'type':'response','id':2,'status':rng.choice([200,204,500]),'items':[rng.randint(1,20)]},{'channel':channel,'type':'response','id':1,'status':200,'items':[999]}])
        if level==4:events.extend([{'channel':'main','type':'issue','id':3},{'channel':'main','type':'response','id':2,'status':200,'items':[77]}])
        c['events']=events;rule='Независимое состояние каждого channel. issue заменяет состояние на loading и пустые items. Применяется ответ только последнего выданного id канала: 200 — ready и items ответа, остальные 2xx — ready/[], ошибка — error/[]. id сохраняется.'
        follow='Как разделить инвалидирование кеша для двух экранов?'
    elif family=='form_contract':
        c.update(minimum_age=18,maximum_age=50+level*5,email_required=level>=2,consent_required=level>=3)
        c['rows']=[{'id':'r'+str(i),'name':name,'age':age,'email':email,'consent':consent} for i,(name,age,email,consent) in enumerate([(' Анна ',20,' A@EXAMPLE.ORG ',True),('',22,'b@example.org',True),('Борис',17,'x',True),('Вера',32,'x',True),('Глеб',30,'g@example.org',False),('Дина',True,'d@example.org',True)])]
        rule='name после trim непустое. age — именно целое (bool запрещён), в заданном диапазоне. Если email_required: ровно один @ и точка в домене. Если consent_required: consent должен быть true. Для принятых верните normalized: name.trim и email.trim.lower. id по порядку.'
        if level==4:
            c['unique_email']=True;c['rows'].append({'id':'duplicate','name':' Другая ','age':22,'email':'a@example.org','consent':True})
            rule+=' Среди принятых email уникален после нормализации, более поздний дубль отклоняется.'
        follow='Почему клиентская проверка не заменяет серверный контракт?'
    elif family=='mutation_suite':
        c.update(low=rng.randint(10,30));c.update(high=c['low']+rng.randint(3,10),numeric_strings=level>=2,allow_null=level==4)
        rule='API принимает целые low..high включительно; bool запрещён. При numeric_strings принимает строки из цифр после trim; иначе строки запрещены. null принимается только при allow_null. Предложите tests из {input,accepted}, которые отличат эталон от дефектов: нижняя/верхняя граница исключены, bool разрешён'+(', числовые строки запрещены' if c['numeric_strings'] else '')+(', null ошибочно запрещён' if c['allow_null'] else ', null ошибочно разрешён')+'.'
        follow='Как обнаружить тест, копирующий дефект самого приложения?'
    elif family=='metamorphic_events':
        rows=[{'id':'e'+str(i),'amount':rng.randint(-10 if level>=2 else 1,40),'status':rng.choice(['accepted','failed']),'time':rng.randint(10,30),'team':rng.choice(['A','B'])} for i in range(3+level)];c['events']=rows+[dict(rows[0])]
        c.update(positive_only=level>=2,accepted_only=level>=3,time_filter=level==4,as_of=20)
        c['extra']={'id':'new','amount':rng.randint(1,40),'status':'accepted','time':10,'team':'A'}
        rule='Агрегат суммирует amount один раз по уникальному id. Дубли одинаковы. При positive_only: amount>0; при accepted_only: только status=accepted; при time_filter: time<=as_of. Верните итоги исходного списка, перестановки, добавления копии первого события и extra. Все суммы — целые.'
        if level==4:rule+=' Также team_totals по исходным допустимым событиям.'
        follow='Какие свойства перестанут работать при конфликтующих суммах одного id?'
    elif family=='rollout_budget':
        c.update(replicas=rng.randint(4,16),surge_percent=rng.choice([20,25,50]),unavailable_percent=rng.choice([0,10,25]),cpu_per_pod=rng.choice([100,200,300]),ram_per_pod=rng.choice([128,256,512]),free_cpu=rng.randint(3,10)*100,free_ram=rng.randint(2,10)*128)
        if level==1:c['surge_percent']=25;c['unavailable_percent']=0;c['free_ram']=99999
        if level==2:c['free_cpu']=99999
        if level==4:c['zone_slots']=[rng.randint(0,3) for _ in range(3)]
        rule='Surge = ceil(replicas*surge_percent/100), unavailable = floor(replicas*unavailable_percent/100). Новые pod ограничены одновременно surge и свободными CPU/RAM. Верните max_new_pods, их peak_cpu/peak_ram и available_floor=replicas-unavailable. CPU в millicores, RAM в MiB. Исключены init/overhead/affinity; готовность по этому расчёту не гарантируется.'
        if level==4:rule+=' Дополнительно размещение по кругу в зонах0,1,2: количество в каждой не превышает zone_slots.'
        follow='Как изменится расчёт при Pod overhead и разных зонах?'
    elif family=='failed_dag':
        jobs={'build':{'needs':[],'duration':rng.randint(5,30)},'unit':{'needs':['build'],'duration':rng.randint(10,40)},'scan':{'needs':['build'],'duration':rng.randint(5,30)}}
        if level>=3:jobs['integration']={'needs':['unit'],'duration':rng.randint(10,50)}
        jobs['release']={'needs':['integration' if level>=3 else 'unit','scan'],'duration':5}
        if level==4:
            jobs['migration']={'needs':['build'],'duration':rng.randint(5,20)}
            jobs['canary']={'needs':['integration','scan','migration'],'duration':rng.randint(10,20)}
            jobs['release']['needs']=['canary']
        c.update(jobs=jobs,failed=rng.choice([[],['scan'],['unit']]) if level>=2 else [])
        rule='Неограниченные исполнители. Расчёт critical_seconds по полному DAG без прерывания: конец задания = max концов родителей + duration. release_allowed true только если все его предки и сам release успешны; failed явно задан. Запуск реального CI не выполняется.'
        follow='Почему одного критического пути недостаточно при лимите исполнителей?'
    elif family=='temporal_labels':
        cutoff=rng.randint(40,70);rows=[]
        for i in range(8+level*2):
            t=cutoff+rng.randint(-20,20);rows.append({'id':'r'+str(i),'time':t,'label_time':t+rng.randint(0,20),'group':'G'+str(rng.randint(1,5))})
        rows.extend([{'id':'known','time':cutoff-20,'label_time':cutoff-1,'group':'TRAIN_FIXED'},{'id':'future','time':cutoff+1,'label_time':cutoff+5,'group':'TEST_FIXED'}])
        c.update(rows=rows,cutoff=cutoff,gap=0 if level<3 else level-1,require_available_labels=level>=2,disjoint_groups=level>=3,available_features=['amount','channel'])
        if level==4:
            c.pop('available_features');c.update(prediction_time=0,feature_catalog={'amount':-1,'channel':0,'request_count':0,'cancelled_at':10,'computed_target_rate':20})
        rule='В train: time < cutoff-gap. Если require_available_labels: label_time<=cutoff. Если disjoint_groups: исключите из train все группы, присутствующие в test. В test: time>=cutoff. id в порядке rows. fit только на train; features=available_features, cancelled_at исключён.'
        if level==4:rule=rule.replace('features=available_features, cancelled_at исключён.','features — только имена из feature_catalog с временем доступности <=prediction_time, в порядке каталога.')
        follow='Что делать, если данные одного клиента пересекают временной контроль?'
    elif family=='threshold_cost':
        c.update(candidates=[{'threshold':10,'tp':90,'fp':40,'fn':10},{'threshold':30,'tp':75,'fp':15,'fn':25},{'threshold':60,'tp':50,'fp':3,'fn':50}],fp_cost=rng.randint(1,5),fn_cost=rng.randint(1,5),min_recall=0 if level==1 else .5 if level==2 else .7,review_budget=999 if level<4 else 100)
        rule='Выберите допустимый threshold с минимальной стоимостью FP*fp_cost+FN*fn_cost. Ограничения: recall=TP/(TP+FN) >= min_recall и TP+FP <= review_budget. При равной цене выбирайте меньший threshold. Верните threshold и целую cost.'
        follow='Почему оптимальный порог зависит от распространённости класса и цены ошибки?'
    elif family=='object_access':
        now=rng.randint(100,200);rows=[]
        for i in range(4+level*2):rows.append({'id':'q'+str(i),'tenant':rng.choice(['A','B']),'object_tenant':'A','subject':'u','owner':rng.choice(['u','v']),'active':rng.choice([True,False]),'action':rng.choice(['read','write','unknown']),'permissions':['read'],'scopes':rng.choice([['read'],[]]),'expires':now+rng.choice([-1,10]),'blocked':rng.choice([True,False])})
        rows.append({'id':'positive','tenant':'A','object_tenant':'A','subject':'u','owner':'u','active':True,'action':'read','permissions':['read'],'scopes':['read'],'expires':now+10,'blocked':False})
        c.update(requests=rows,now=now,require_owner=level>=2,require_scope=level>=3,require_expiry=level==4)
        rule='Доступ требует одинакового tenant, active=true и action в permissions. Если require_owner: subject==owner. Если require_scope: action в scopes. Если require_expiry: expires>now и blocked=false. Неизвестное действие не разрешается само по себе. Верните allowed_ids по порядку.'
        follow='Какие отрицательные проверки нужны для временного исключения другой организации?'
    else:
        network=ipaddress.ip_network('10.0.'+str(rng.randint(1,100))+'.0/28');hosts=list(network.hosts());denied=str(hosts[2])
        rows=[{'id':'n'+str(i),'ip':str(rng.choice(hosts)) if i%3 else '192.0.2.1','port':rng.choice([443,80,22]),'protocol':rng.choice(['tcp','udp'])} for i in range(6+level)]
        rows.append({'id':'deny','ip':denied,'port':443,'protocol':'tcp'})
        c.update(cidr=str(network),ports=[443] if level<3 else [443,22],connections=rows,check_protocol=level>=2,denies_override=level>=3,denied_ips=[denied])
        if level==4:
            c.update(check_expiry=True,now=100)
            for row in rows:row['expires']=rng.choice([99,110])
        rule='Разрешены адреса внутри cidr и только перечисленные ports. Если check_protocol: только tcp. Если denies_override: denied_ips запрещены даже при совпадении allow. Верните allowed_ids по порядку. Это модель firewall, сетевые запросы не выполняются.'
        if level==4:rule+=' Также требуется expires>now для каждого соединения.'
        follow='Как учесть несколько правил allow/deny и отзыв доступа?'
    reference=solve(c);starter={k:[] if isinstance(v,list) else {} if isinstance(v,dict) else 0 for k,v in reference.items()}
    competencies={'backend':['data_processing','api_contracts'],'frontend':['state','api_contracts'],'qa':['test_design','risk_analysis'],'devops':['capacity','pipeline'],'ml':['validation','metrics'],'security':['authorization','least_privilege']}
    if family=='network_rules':competencies[spec]=['network','least_privilege']
    return {'id':'item_'+secrets.token_hex(8),'kind':'work_sample','competency':'work_sample','competencies':competencies[spec],'stage':'work_sample','family':spec+':'+family,'version':VERSION,'prompt':rule+'\nУсловия:\n'+dumps({k:v for k,v in c.items() if k not in ['specialization','grade','level','family']}),'starter':dumps(starter),'requirements':['Верните JSON с полями: '+', '.join(reference), 'Не добавляйте неизвестные поля. Числовые результаты — целые; порядок id значим, если указан.','Проверяется ограниченная модель, не выполнение произвольного кода.'],'source_ids':SOURCES[spec],'followup':follow,'max_length':5000,'private':{'condition':c,'family':family,'evaluator':'bounded_v2','expected':reference,'reference_answer':dumps(reference),'check_labels':list(reference)}}

def evaluate(item,answer):
    from .work_samples import unique_object
    c=item['private']['condition'];expected=item['private']['expected'];checks={k:False for k in expected}
    try:
        if not isinstance(answer,str) or len(answer)>5000:raise ValueError('Answer size')
        value=json.loads(answer,object_pairs_hook=unique_object)
        if not isinstance(value,dict) or set(value)!=set(expected):raise ValueError('Fields')
        if c['family']=='mutation_suite':
            rows=value['tests']
            if not isinstance(rows,list) or not 1<=len(rows)<=20 or any(not isinstance(x,dict) or set(x)!={'input','accepted'} or type(x['accepted']) is not bool or type(x['input']) not in [int,str,bool,type(None)] for x in rows):raise ValueError('Tests format')
            oracle=all(x['accepted']==normalize_number(x['input'],c) for x in rows)
            mutants=[]
            for mutation in ['low','high','bool','null']+(['strings'] if c['numeric_strings'] else []):
                hit=False
                for x in rows:
                    v=x['input'];correct=normalize_number(v,c);wrong=correct
                    parsed=int(v.strip()) if type(v) is str and c['numeric_strings'] and v.strip().isdecimal() else v if type(v) is int else None
                    if mutation=='low' and parsed==c['low']:wrong=False
                    if mutation=='high' and parsed==c['high']:wrong=False
                    if mutation=='bool' and type(v) is bool:wrong=True
                    if mutation=='null' and v is None:wrong=not c['allow_null']
                    if mutation=='strings' and isinstance(v,str) and correct:wrong=False
                    hit=hit or wrong!=correct
                mutants.append(hit)
            checks={'independent_expected':oracle,'mutations_detected':all(mutants)}
        else:checks={k:json.dumps(value[k],sort_keys=True,ensure_ascii=False)==json.dumps(v,sort_keys=True,ensure_ascii=False) for k,v in expected.items()}
    except (ValueError,TypeError,KeyError,RecursionError,OverflowError):
        return {'score':0,'method':'bounded_work_model_v2','all_checks_passed':False,'checks':[{'id':k,'passed':False} for k in checks],'format_error':'Нужен JSON с указанными полями и типами. Код не исполняется.'}
    return {'score':round(100*sum(checks.values())/len(checks),2),'method':'bounded_work_model_v2','all_checks_passed':all(checks.values()),'checks':[{'id':k,'passed':v} for k,v in checks.items()]}
