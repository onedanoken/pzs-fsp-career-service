"""An explicit changed condition, not a reworded answer or ungraded explanation."""
import copy,json,secrets

def generate(item):
    if item['private']['evaluator']=='bounded_v3':
        from .work_projects_v3 import transfer
        return transfer(item)
    from .work_cases_v2 import solve
    c=copy.deepcopy(item['private']['condition']);family=c['family']
    change=''
    if family=='event_ledger':
        c['maximum']=c['initial']+5;c['events'].append({'id':'transfer_deposit','amount':500})
        c['initial']+=1;change='Теперь отклоняется превышение maximum, а initial изменился.'
    elif family=='cursor_page':
        c['cursor']=[-1,-1];c['limit']=1;c['snapshot_filter']=True;c['as_of']=20
        for row in c['records']:row.update(deleted=False,created=10)
        c['records'].append({'id':9999,'score':-1,'deleted':False,'created':10})
        change='Новый снимок: исключайте deleted=true и created>as_of до выбора следующей страницы.'
    elif family=='response_state':
        c['events'].append({'channel':'transfer','type':'issue','id':100})
        change='Добавлен независимый канал без ответа: у него должно остаться loading/[].'
    elif family=='form_contract':
        c['minimum_age']=30;c['consent_required']=True
        c['rows'].append({'id':'transfer_row','name':' Новый ','age':35,'email':'new@example.org','consent':True})
        change='Минимальный возраст теперь30; согласие обязательно. Новый корректный объект также должен попасть в результат.'
    elif family=='mutation_suite':
        c['allow_null']=not c['allow_null'];change='Политика null изменилась. Сохраните независимые ожидаемые ответы и обнаружение соответствующего дефекта.'
    elif family=='metamorphic_events':
        c['positive_only']=False;c['events'].append({'id':'transfer_debit','amount':-7,'status':'accepted','time':10,'team':'A'})
        change='Теперь отрицательные amount допустимы; добавлено новое принятое событие. Если включён time_filter, team_totals также пересчитывается.'
    elif family=='rollout_budget':
        c['replicas']+=10;change='Изменилось число реплик: заново проверьте одновременно лимиты ресурсов, surge и available_floor.'
    elif family=='failed_dag':
        c['jobs']['release']['duration']+=17;c['failed']=[];change='Проверки успешны, но выпуск занимает дополнительное время. Не используйте прежнюю длину критического пути.'
    elif family=='temporal_labels':
        c['rows'].append({'id':'transfer_test','group':'NEW','time':c['cutoff']+1,'label_time':c['cutoff']+2})
        c['require_available_labels']=True;change='Появилась новая тестовая группа; train требует меток, доступных не позже cutoff.'
    elif family=='threshold_cost':
        c['fp_cost']+=17;c['fn_cost']+=19;change='Изменилась цена обоих видов ошибки. Бюджет и min_recall обязательны; заново выберите допустимый минимальный cost.'
    elif family=='object_access':
        c['require_expiry']=True;c['requests'].append({'id':'transfer_access','tenant':'A','object_tenant':'A','subject':'u','owner':'u','active':True,'action':'read','permissions':['read'],'scopes':['read'],'expires':c['now']+10,'blocked':False})
        change='Отзыв и срок теперь проверяются на каждом запросе: expires>now и blocked=false обязательны. Новый допустимый запрос включите в результат.'
    elif family=='network_rules':
        import ipaddress
        c['check_expiry']=True;c['now']=100
        for row in c['connections']:row.setdefault('expires',110)
        ip=str(next(ipaddress.ip_network(c['cidr']).hosts()))
        c['connections'].append({'id':'transfer_network','ip':ip,'port':c['ports'][0],'protocol':'tcp','expires':110})
        change='Добавлен временный доступ: expires>now требуется для каждого соединения; остальные ограничения сохраняются.'
    else:raise ValueError('No registered transfer')
    expected=solve(c);result=copy.deepcopy(item)
    brief=item['prompt'].split('\nУсловия:')[0]
    if family=='mutation_suite':brief=brief.replace('null ошибочно запрещён','политика null противоположна allow_null').replace('null ошибочно разрешён','политика null противоположна allow_null')
    result.update(id='item_'+secrets.token_hex(8),stage='transfer',version='work-transfer-2026-v1',prompt='Изменённое условие: '+change+'\n'+brief+'\nУсловия:\n'+json.dumps({k:v for k,v in c.items() if k not in ['specialization','grade','level','family']},ensure_ascii=False,indent=2),requirements=['Верните JSON с полями: '+', '.join(expected),'Правила исходного кейса действуют с указанными изменениями.','Проверяется ограниченная модель; код не выполняется.'])
    result['private'].update(condition=c,expected=expected,reference_answer=json.dumps(expected,ensure_ascii=False,indent=2),check_labels=list(expected))
    if family=='mutation_suite':
        from .work_cases_v2 import evaluate
        if evaluate(result,item['private']['reference_answer'])['all_checks_passed']:raise ValueError('Transfer did not distinguish the original solution')
    elif expected==item['private']['expected']:raise ValueError('Transfer requires a changed observable result')
    result['starter']=json.dumps({k:[] if isinstance(v,list) else {} if isinstance(v,dict) else 0 for k,v in expected.items()},ensure_ascii=False,indent=2)
    return result
