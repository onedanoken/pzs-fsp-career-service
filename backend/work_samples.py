"""Owned work artifacts, bounded model checks, and a frozen common review rubric."""
import hashlib,json,secrets

PATH_VERSION='work_sample_v1'
AUTO_PATH_VERSION='work_sample_v2_auto'
BALANCED_PATH_VERSION='work_sample_v3_balanced'
AUTO_PATH_VERSIONS={AUTO_PATH_VERSION,BALANCED_PATH_VERSION}
RUBRIC=[
 {'id':'correctness','label':'Корректность решения','anchors':['Решение не выполняет ключевой инвариант','Есть частичное решение с существенными пробелами','Основной инвариант сохранён, указаны ограничения','Инварианты сохранены и обоснованы при изменении условия']},
 {'id':'verification','label':'Проверки и воспроизводимость','anchors':['Проверки не предложены','Проверен только успешный пример','Есть успешный и отрицательный сценарии','Проверки независимы, покрывают границы и новый сценарий']},
 {'id':'reasoning','label':'Обоснование решения','anchors':['Причина выбора не объяснена','Объяснение общее, без связи с кейсом','Причина связана с конкретным ограничением','Альтернативы сопоставлены с ограничениями и последствиями']},
 {'id':'limits','label':'Границы и компромиссы','anchors':['Пределы решения не обозначены','Указан один риск без способа проверки','Отмечены неизвестные данные и существенный компромисс','Предложены измерения и условия пересмотра решения']},
]

def encode(value):return json.dumps(value,ensure_ascii=False,indent=2)
def unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('Повторяющиеся поля JSON')
        result[key]=value
    return result
def object_keys(value,keys):return isinstance(value,dict) and set(value)==set(keys)
def names(value):return isinstance(value,list) and all(isinstance(x,str) for x in value) and len(value)==len(set(value))

def partner_case(spec,grade,rng):
    from importlib import import_module
    modules={'backend':'stock_reservation','frontend':'resource_cache','qa':'reservation_test_suite','devops':'release_rollout','ml':'temporal_validation','security':'object_access'}
    if spec=='backend' and grade=='Senior':return import_module('.reservation_settlement',__package__).generate(grade,rng)
    if grade not in ('Junior','Middle'):return None
    return import_module('.'+modules[spec],__package__).generate(grade,rng)

def generate_case(spec,grade,rng):
    if spec=='backend' and grade=='Senior':return partner_case(spec,grade,rng)
    if grade in ('Junior','Middle') and rng.choice([False,True]):return partner_case(spec,grade,rng)
    return generate_legacy_case(spec,grade,rng)

def generate_legacy_case(spec,grade,rng):
    if spec not in {'backend','frontend','qa','devops','ml','security'}:raise ValueError('Неизвестное направление')
    condition={'specialization':spec,'grade':grade}
    if spec=='backend':
        family='durable_idempotency'
        starter={'key_storage':'memory','commit':'separate','changed_payload':'reuse','checks':[]}
        reference={'key_storage':'durable','commit':'atomic','changed_payload':'reject','checks':['repeat','restart','race','payload_conflict']}
        brief='Сервис создаёт операцию по ключу запроса. Ответ может потеряться, процесс может перезапуститься, а два запроса — прийти одновременно. Один ключ с другим телом должен дать конфликт, без новой записи. Исправьте план хранения ключа и фиксации операции.'
        constraints=['key_storage: memory или durable','commit: separate или atomic','changed_payload: reuse или reject','checks: названия repeat, restart, race, payload_conflict']
        change='Хранилище ключей теперь очищается через сутки, а повтор может прийти через два дня. Какие гарантии перестали выполняться и как вы измените контракт?'
        competencies=['api_contracts','debugging'];checks=['one_operation','restart_safe','atomic_failure','payload_conflict','negative_checks']
    elif spec=='frontend':
        family='request_state_repair';condition['durations']=[rng.randint(80,120),rng.randint(5,20)]
        starter={'accept':'last_finished','http_errors':False,'empty_204':'parse_json','checks':[]}
        reference={'accept':'latest_issued','http_errors':True,'empty_204':'accept','checks':['out_of_order','http_error','empty_success']}
        brief=f'Экран запустил запрос A ({condition["durations"][0]} мс), затем через 5 мс B ({condition["durations"][1]} мс). Старый ответ не должен заменить результат B, даже если B вернул ошибку. fetch возвращает Response на HTTP 500; ответ 204 пустой. Исправьте правила применения состояния и обработки ответов.'
        constraints=['accept: last_finished или latest_issued','http_errors: true/false — проверяется ли статус HTTP','empty_204: parse_json или accept','checks: out_of_order, http_error, empty_success']
        change='На экране появились два независимых списка со своими фильтрами. Достаточен ли один общий номер последнего запроса? Как изменится состояние и проверка?'
        competencies=['state','api_contracts','debugging'];checks=['latest_request','http_failure','empty_body','negative_checks']
    elif spec=='qa':
        family='boundary_test_suite';low=rng.randint(10,40);high=low+rng.randint(5,12);condition.update(low=low,high=high)
        starter={'tests':[{'value':low+1,'accepted':True}]}
        reference={'tests':[{'value':v,'accepted':low<=v<=high} for v in [low-1,low,high,high+1]]}
        brief=f'Поле API принимает целое от {low} до {high} включительно. Один успешный пример не обнаружил дефекты границ. Дополните набор независимых проверок. В тестах явно укажите вход и ожидаемое принятие.'
        constraints=['tests: массив до 20 объектов value (целое), accepted (true/false)','Нужны обе границы и ближайшие значения вне диапазона','Эталон должен соответствовать включительным границам']
        change='API теперь принимает также null как отсутствие значения. Какие отдельные классы проверок добавите и какие прежние проверки сохраните?'
        competencies=['test_design','debugging','risk_analysis'];checks=['independent_oracle','lower_boundary','upper_boundary','outside_range','mutation_detection']
    elif spec=='devops':
        family='pipeline_graph_repair';condition['durations']={name:rng.randint(10,90) for name in ['build','tests','scan','release']}
        starter={'needs':{'build':[],'tests':[],'scan':['release'],'release':['tests']},'checks':[]}
        reference={'needs':{'build':[],'tests':['build'],'scan':['build'],'release':['tests','scan']},'checks':['dependency_order','both_gates','no_cycles']}
        brief=f'CI состоит из build, tests, scan, release. Длительности в секундах: {encode(condition["durations"])}. tests и scan должны ждать build и затем работать независимо. release требует успешного завершения обеих проверок. Исполнителей достаточно, накладных расходов нет. Исправьте граф зависимостей.'
        constraints=['needs: ровно четыре задания, у каждого массив зависимостей','Граф без циклов; неизвестных заданий нет','checks: dependency_order, both_gates, no_cycles']
        change='Теперь доступен только один исполнитель, а scan иногда завершается ошибкой. Что изменится в оценке времени и в допуске release?'
        competencies=['pipeline','deployment','debugging'];checks=['known_jobs','acyclic','build_first','parallel_branches','release_gates','negative_checks']
    elif spec=='ml':
        family='split_leakage_repair';groups=[f'G{i}' for i in range(1,7)];holdout=rng.sample(groups,2)
        condition.update(groups=groups,holdout=sorted(holdout),available_features=['amount','channel'],future_feature='cancelled_at')
        starter={'train_groups':groups,'test_groups':holdout,'fit_on':'all','features':['amount','channel','cancelled_at'],'checks':[]}
        reference={'train_groups':[g for g in groups if g not in holdout],'test_groups':holdout,'fit_on':'train','features':['amount','channel'],'checks':['group_overlap','fit_train','future_feature']}
        brief=f'Модель предсказывает отмену заказа в момент создания. amount и channel известны тогда, cancelled_at — только после отмены. Независимые группы: {groups}; итоговый контроль целиком {holdout}. Все записи группы должны быть в одном split. Исправьте план разбиения и подготовки признаков.'
        constraints=['train_groups и test_groups: массивы уникальных известных групп','Итоговый контроль неизменен; остальные группы — train','fit_on: all или train; features: непустой набор доступных при прогнозе признаков','checks: group_overlap, fit_train, future_feature']
        change='Требуется предсказывать будущий месяц для уже известных клиентов. Подходит ли прежнее групповое разбиение само по себе? Какие временные условия и доступность меток вы проверите?'
        competencies=['splitting','leakage','validation'];checks=['group_assignment','disjoint_groups','fixed_holdout','train_preprocessing','feature_timing','negative_checks']
    else:
        family='tenant_policy_repair'
        starter={'rule':{'any':[{'eq':['subject.tenant','object.tenant']},{'flag':'token.active'}]},'checks':[]}
        reference={'rule':{'all':[{'eq':['subject.tenant','object.tenant']},{'flag':'token.active'},{'in':['action','subject.permissions']}]},'checks':['other_tenant','inactive_token','forbidden_action','allowed_read']}
        brief='Выдача документа должна одновременно требовать ту же организацию, активный токен и разрешённое действие. По умолчанию доступ запрещён. Исправьте правило политики и предусмотрите отрицательные проверки. Это ограниченная модель прав, не исполнение серверного кода.'
        constraints=['rule: all/any с массивом правил, eq/in с двумя путями либо flag с одним путём','Разрешены пути subject.tenant, object.tenant, token.active, action, subject.permissions','checks: other_tenant, inactive_token, forbidden_action, allowed_read']
        change='Добавлен временный доступ приглашённого пользователя из другой организации. Как выразить исключение, не разрешив остальные чужие документы? Какие проверки и срок действия нужны?'
        competencies=['authorization','least_privilege','incident_response'];checks=['tenant_isolation','token_revocation','action_scope','allowed_access','default_deny','negative_checks']
    private={'condition':condition,'family':family,'reference_answer':encode(reference),'check_labels':checks}
    return {'id':'item_'+secrets.token_hex(8),'kind':'work_sample','competency':'work_sample','competencies':competencies,'stage':'work_sample','family':spec+':'+family,'version':'work-cases-2026-v1','prompt':brief,'starter':encode(starter),'requirements':constraints,'private':private,'followup':change,'max_length':5000}

def graph_end(needs,durations):
    remaining=set(needs);end={}
    while remaining:
        ready=[name for name in remaining if all(parent in end for parent in needs[name])]
        if not ready:raise ValueError('Цикл')
        for name in ready:end[name]=durations[name]+max([end[x] for x in needs[name]] or [0]);remaining.remove(name)
    return end

def policy(rule,context,depth=0):
    if depth>5 or not isinstance(rule,dict) or len(rule)!=1:raise ValueError('Некорректное правило')
    op,value=next(iter(rule.items()))
    if op in ['all','any']:
        if not isinstance(value,list) or not 1<=len(value)<=8:raise ValueError('Некорректные условия')
        values=[policy(x,context,depth+1) for x in value]
        return all(values) if op=='all' else any(values)
    if op=='flag':
        if value!='token.active':raise ValueError('Неизвестный путь')
        return context[value] is True
    if op not in ['eq','in'] or not isinstance(value,list) or len(value)!=2 or any(not isinstance(x,str) or x not in context for x in value):raise ValueError('Неизвестный оператор или путь')
    a,b=(context[x] for x in value)
    if op=='in':
        if not isinstance(b,list):raise ValueError('Нужен массив разрешений')
        return a in b
    return type(a)==type(b) and a==b

def evaluate(item,answer):
    if item.get('private',{}).get('family') in {'stock_reservation','reservation_settlement','resource_cache','reservation_test_suite','release_rollout','temporal_validation','object_access'}:
        from .partner_cases import evaluate as imported_evaluate
        return imported_evaluate(item,answer)
    if item.get('private',{}).get('evaluator')=='bounded_v3':
        from .work_projects_v3 import evaluate as evaluate_v3
        return evaluate_v3(item,answer)
    if item.get('private',{}).get('evaluator')=='bounded_v2':
        from .work_cases_v2 import evaluate as evaluate_v2
        return evaluate_v2(item,answer)
    labels=item['private']['check_labels'];condition=item['private']['condition'];spec=condition['specialization']
    checks={label:False for label in labels}
    try:
        if not isinstance(answer,str) or len(answer)>5000:raise ValueError('Размер ответа')
        value=json.loads(answer,object_pairs_hook=unique_object)
        if spec=='backend':
            if not object_keys(value,['key_storage','commit','changed_payload','checks']):raise ValueError('Поля плана')
            checks.update(one_operation=value['commit']=='atomic',restart_safe=value['key_storage']=='durable',atomic_failure=value['commit']=='atomic' and value['key_storage']=='durable',payload_conflict=value['changed_payload']=='reject',negative_checks=names(value['checks']) and {'repeat','restart','race','payload_conflict'}<=set(value['checks']))
        elif spec=='frontend':
            if not object_keys(value,['accept','http_errors','empty_204','checks']):raise ValueError('Поля политики')
            checks.update(latest_request=value['accept']=='latest_issued',http_failure=value['http_errors'] is True,empty_body=value['empty_204']=='accept',negative_checks=names(value['checks']) and {'out_of_order','http_error','empty_success'}<=set(value['checks']))
        elif spec=='qa':
            if not object_keys(value,['tests']) or not isinstance(value['tests'],list) or not 1<=len(value['tests'])<=20:raise ValueError('Набор проверок')
            rows=value['tests'];low,high=condition['low'],condition['high']
            valid=all(object_keys(x,['value','accepted']) and type(x['value']) is int and type(x['accepted']) is bool for x in rows)
            if not valid:raise ValueError('Формат теста')
            checks.update(independent_oracle=all(x['accepted']==(low<=x['value']<=high) for x in rows),lower_boundary=any(x['value']==low and x['accepted'] for x in rows),upper_boundary=any(x['value']==high and x['accepted'] for x in rows),outside_range={low-1,high+1}<={x['value'] for x in rows if not x['accepted']},mutation_detection=any(x['value']==low and x['accepted'] for x in rows) and any(x['value']==high+1 and not x['accepted'] for x in rows))
        elif spec=='devops':
            if not object_keys(value,['needs','checks']) or not object_keys(value['needs'],['build','tests','scan','release']):raise ValueError('Задания')
            needs=value['needs'];known=all(names(x) and set(x)<=set(needs) for x in needs.values());checks['known_jobs']=known
            if known:
                end=graph_end(needs,condition['durations']);checks['acyclic']=True
                checks['build_first']=needs['build']==[] and needs['tests']==['build'] and needs['scan']==['build']
                checks['parallel_branches']=needs['tests']==['build'] and needs['scan']==['build']
                checks['release_gates']={'tests','scan'}<=set(needs['release']) and end['release']>=max(end['tests'],end['scan'])+condition['durations']['release']
            checks['negative_checks']=names(value['checks']) and {'dependency_order','both_gates','no_cycles'}<=set(value['checks'])
        elif spec=='ml':
            if not object_keys(value,['train_groups','test_groups','fit_on','features','checks']):raise ValueError('Поля плана')
            train,test,features=value['train_groups'],value['test_groups'],value['features']
            if not all(names(x) for x in [train,test,features]):raise ValueError('Наборы групп или признаков')
            checks.update(group_assignment=set(train)|set(test)==set(condition['groups']),disjoint_groups=not set(train)&set(test),fixed_holdout=set(test)==set(condition['holdout']),train_preprocessing=value['fit_on']=='train',feature_timing=bool(features) and set(features)<=set(condition['available_features']),negative_checks=names(value['checks']) and {'group_overlap','fit_train','future_feature'}<=set(value['checks']))
        else:
            if not object_keys(value,['rule','checks']):raise ValueError('Поля политики')
            results={k:True for k in ['tenant_isolation','token_revocation','action_scope','allowed_access','default_deny']}
            for tenant in ['A','B','C']:
                for other,active,action in [(tenant,True,'read'),('other',True,'read'),(tenant,False,'read'),(tenant,True,'write'),(tenant,True,'unknown')]:
                    allowed=policy(value['rule'],{'subject.tenant':tenant,'object.tenant':other,'token.active':active,'action':action,'subject.permissions':['read']})
                    label='allowed_access' if other==tenant and active and action=='read' else 'tenant_isolation' if other!=tenant else 'token_revocation' if not active else 'default_deny' if action=='unknown' else 'action_scope'
                    results[label]=results[label] and allowed==(label=='allowed_access')
            checks.update(results);checks['negative_checks']=names(value['checks']) and {'other_tenant','inactive_token','forbidden_action','allowed_read'}<=set(value['checks'])
    except (ValueError,TypeError,KeyError,RecursionError,OverflowError):
        return {'score':0,'method':'bounded_work_model','checks':[{'id':k,'passed':False} for k in labels],'format_error':'Проверьте формат JSON и разрешённые поля. Произвольный код не исполняется.'}
    return {'score':round(100*sum(checks.values())/len(checks),2),'method':'bounded_work_model','checks':[{'id':k,'passed':v} for k,v in checks.items()],'all_checks_passed':all(checks.values())}

def add_journey(base,spec,grade,path_version=PATH_VERSION):
    if path_version==BALANCED_PATH_VERSION:return balanced_journey(base,spec,grade)
    import random
    from .professional_items import LEVEL_CRITERIA
    rng=random.Random(int(base['seed']));objective=[x for x in base['items'] if x['kind']!='explanation']
    selected=[];seen=set()
    for item in objective:
        if item['competency'] in seen:continue
        selected.append(item);seen.add(item['competency'])
        if len(selected)==3:break
    for item in selected:item['stage']='screening'
    if path_version==AUTO_PATH_VERSION:
        from .work_cases_v2 import generate_case as generate_auto_case
        case=generate_auto_case(spec,grade,rng)
    else:case=generate_case(spec,grade,rng)
    rubric=case.get('review_rubric',RUBRIC)
    reflection={'id':'item_'+secrets.token_hex(8),'kind':'explanation','competency':'tradeoffs','stage':'review','family':spec+':work_reflection','version':'work-cases-2026-v1','prompt':'Объясните изменения рабочего кейса: какой инвариант вы сохраняете и какими проверками это подтверждается? Затем разберите изменённое условие: '+case.pop('followup')+' Ориентиры выбранного пилотного уровня '+grade+': '+'; '.join(LEVEL_CRITERIA[grade])+'.','max_length':3000,'private':{'rubric':[x['label'] for x in rubric],'rubric_criteria':rubric}}
    base=dict(base);base['items']=selected+[case,reflection];base['total_items']=len(base['items'])
    for item in base['items']:
        item.pop('sha256',None)
        item['sha256']=hashlib.sha256(json.dumps(item,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    signatures=[]
    for item in selected:
        if 'condition' in item['private']:
            from .professional_variants import condition_signature
            signatures.append(condition_signature([item]));continue
        public={'kind':item['kind'],'competency':item['competency'],'family':item.get('family')}
        if item['competency']=='data_processing' and base.get('numeric_conditions'):
            condition=dict(base['numeric_conditions']);condition['values']=sorted(condition['values']);public['condition']=condition
        elif item.get('choices'):
            expected=item['private']['expected'];expected=expected if isinstance(expected,list) else [expected]
            public['condition']={'prompt':item['prompt'],'choices':sorted([(x['text'],x['id'] in expected) for x in item['choices']])}
        elif item['kind']=='sql':
            public['condition']={'prompt':item['prompt'].replace(json.dumps(item['private']['rows'],ensure_ascii=False),'ROWS'),'rows':sorted(item['private']['rows'])}
        else:public['condition']={'prompt':item['prompt'],'expected':item['private'].get('expected')}
        signatures.append(json.dumps(public,sort_keys=True,ensure_ascii=False))
    signature=json.dumps({'screening':signatures,'case':case['private']['condition'],'family':case['family'],'reflection':reflection['prompt'],'case_version':case['version'],'rubric_version':case.get('rubric_version'),'rubric':rubric},sort_keys=True,ensure_ascii=False)
    base['condition_signature']=hashlib.sha256(signature.encode()).hexdigest()
    base['assessment_path']={'version':path_version,'stages':['screening','work_sample','review'],'rubric':rubric,'state':'answering','review_required':path_version!=AUTO_PATH_VERSION,'notice':'Категория определяется автоматическими проверками. Объяснение и ручной разбор дополнительны; пороги пилотные.' if path_version==AUTO_PATH_VERSION else 'Три отдельных основания. 70% — пилотный порог; полноценная калибровка требует независимых участников.'}
    if path_version==AUTO_PATH_VERSION:
        reflection['optional']=True
        reflection['prompt']='Необязательно: '+reflection['prompt']
        reflection.pop('sha256',None);reflection['sha256']=hashlib.sha256(json.dumps(reflection,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return base

def balanced_journey(base,spec,grade):
    import random
    from .assessment_forms import screening,signature
    from .work_cases_v2 import generate_case as legacy_case
    from .work_projects_v3 import generate_case as project_case,FAMILIES
    from .work_transfers import generate as transfer_case
    rng=random.Random(int(base['seed']));selected,plan=screening(spec,grade,int(base['seed']))
    options=['legacy']+(['project'] if spec in FAMILIES else [])+(['partner'] if grade in ('Junior','Middle') or (spec=='backend' and grade=='Senior') else [])
    source=rng.choice(options)
    case=partner_case(spec,grade,rng) if source=='partner' else project_case(spec,grade,rng) if source=='project' else legacy_case(spec,grade,rng)
    if source=='partner':
        # A different bounded work context tests transfer at the same requested level.
        transfer=transfer_case(legacy_case(spec,grade,rng))
        transfer['prompt']='Перенос знаний в другой рабочий контекст того же направления и уровня. Примените инварианты самостоятельно; JSON-контракт отличается от первого кейса.\n'+transfer['prompt']
    else:transfer=transfer_case(case)
    case=broken_starter(case);transfer=broken_starter(transfer)
    rubric=case.get('review_rubric',RUBRIC)
    reflection={'id':'item_'+secrets.token_hex(8),'kind':'explanation','competency':'tradeoffs','stage':'review','family':spec+':work_reflection','version':'reflection-v3','optional':True,'prompt':'Необязательно: объясните, почему изменённое условие потребовало нового результата, какие инварианты сохранены и что не проверяет эта ограниченная модель.','max_length':3000,'private':{'rubric':[x['label'] for x in rubric],'rubric_criteria':rubric}}
    result=dict(base);result['items']=selected+[case,transfer,reflection];result['total_items']=len(result['items'])
    result['assembly_plan']=plan;result['generator']='balanced_owned_forms';result['neural_items']=0
    from .assessment_catalog import POLICY
    result['policy_snapshot']=dict(base.get('policy_snapshot') or POLICY,version='grade-policy-v3-balanced',review_required=False,core_floor=50,screening_count=len(selected),work_checks_required=['original','transfer'])
    result['condition_signature']=hashlib.sha256(json.dumps({'screening':sorted(signature(i) for i in selected),'case':case['private']['condition'],'case_version':case['version'],'family':case['family'],'rubric_version':case.get('rubric_version'),'transfer':transfer['private']['condition']},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    result['assessment_path']={'version':BALANCED_PATH_VERSION,'stages':['screening','work_sample','transfer','review'],'screening_count':len(selected),'rubric':rubric,'state':'answering','review_required':False,'notice':'Категория определяется тестом по квотам, рабочим кейсом и проверяемым изменённым условием. Пороги пилотные; объяснение дополнительно.'}
    for item in result['items']:
        item.pop('sha256',None);item['sha256']=hashlib.sha256(json.dumps(item,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return result

def broken_starter(item):
    """Keep valid empty legacy answers valid; only new forms promise a repair exercise."""
    if not evaluate(item,item['starter'])['all_checks_passed']:return item
    import copy
    expected=copy.deepcopy(item['private']['expected']);key=next(iter(expected));value=expected[key]
    expected[key]=value+['unverified_entry'] if isinstance(value,list) else dict(value,unverified_entry=1) if isinstance(value,dict) else not value if type(value) is bool else value+1 if type(value) in [int,float] else 0
    result=dict(item,starter=json.dumps(expected,ensure_ascii=False,indent=2))
    if evaluate(result,result['starter'])['all_checks_passed']:raise ValueError('Starter remains valid')
    return result
