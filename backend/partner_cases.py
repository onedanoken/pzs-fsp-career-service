"""Seven versioned bounded case interpreters adapted from egor; no user code execution."""
import json
from .work_samples import unique_object, object_keys, names, graph_end, policy
def evaluate(item,answer):
    if item.get('private',{}).get('evaluator')=='bounded_v2':
        from .work_cases_v2 import evaluate as evaluate_v2
        return evaluate_v2(item,answer)
    labels=item['private']['check_labels'];condition=item['private']['condition'];spec=condition['specialization']
    checks={label:False for label in labels}
    if item['private']['family']=='reservation_settlement':
        from .reservation_settlement import evaluate as evaluate_settlement,VERSION,MODEL_VERSION
        if item.get('version')!=VERSION or condition.get('model_version')!=MODEL_VERSION:
            from fastapi import HTTPException
            raise HTTPException(503,'Версия проверки расчёта резерва не поддерживается; ответы сохранены.')
    elif item['private']['family']=='stock_reservation':
        from .stock_reservation import evaluate as evaluate_reservation,VERSION,V1_VERSION
        if item.get('version') not in (V1_VERSION,VERSION) or condition.get('oracle_version')!=V1_VERSION:
            from fastapi import HTTPException
            raise HTTPException(503,'Версия проверки кейса не поддерживается; ответы сохранены.')
    elif item['private']['family']=='resource_cache':
        from .resource_cache import evaluate as evaluate_cache,VERSION,MODEL_VERSION
        if item.get('version')!=VERSION or condition.get('model_version')!=MODEL_VERSION:
            from fastapi import HTTPException
            raise HTTPException(503,'Версия проверки кеша не поддерживается; ответы сохранены.')
    elif item['private']['family']=='reservation_test_suite':
        from .reservation_test_suite import evaluate as evaluate_suite,VERSION,MODEL_VERSION
        if item.get('version')!=VERSION or condition.get('model_version')!=MODEL_VERSION:
            from fastapi import HTTPException
            raise HTTPException(503,'Версия проверки набора тестов не поддерживается; ответы сохранены.')
    elif item['private']['family']=='release_rollout':
        from .release_rollout import evaluate as evaluate_release,VERSION,MODEL_VERSION
        if item.get('version')!=VERSION or condition.get('model_version')!=MODEL_VERSION:
            from fastapi import HTTPException
            raise HTTPException(503,'Версия проверки релиза не поддерживается; ответы сохранены.')
    elif item['private']['family']=='temporal_validation':
        from .temporal_validation import evaluate as evaluate_temporal,VERSION,MODEL_VERSION
        if item.get('version')!=VERSION or condition.get('model_version')!=MODEL_VERSION:
            from fastapi import HTTPException
            raise HTTPException(503,'Версия проверки ML не поддерживается; ответы сохранены.')
    elif item['private']['family']=='object_access':
        from .object_access import evaluate as evaluate_access,VERSION,MODEL_VERSION
        if item.get('version')!=VERSION or condition.get('model_version')!=MODEL_VERSION:
            from fastapi import HTTPException
            raise HTTPException(503,'Версия проверки доступа не поддерживается; ответы сохранены.')
    try:
        if not isinstance(answer,str) or len(answer)>5000:raise ValueError('Размер ответа')
        value=json.loads(answer,object_pairs_hook=unique_object)
        if item['private']['family']=='reservation_settlement':
            checks.update(evaluate_settlement(value,condition))
        elif item['private']['family']=='stock_reservation':
            checks.update(evaluate_reservation(value,condition))
        elif item['private']['family']=='resource_cache':
            checks.update(evaluate_cache(value,condition))
        elif item['private']['family']=='reservation_test_suite':
            checks.update(evaluate_suite(value,condition))
        elif item['private']['family']=='release_rollout':
            checks.update(evaluate_release(value,condition))
        elif item['private']['family']=='temporal_validation':
            checks.update(evaluate_temporal(value,condition))
        elif item['private']['family']=='object_access':
            checks.update(evaluate_access(value,condition))
        elif spec=='backend':
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
        return {'score':0,'all_checks_passed':False,'method':'bounded_work_model','checks':[{'id':k,'passed':False} for k in labels],'format_error':'Проверьте формат JSON и разрешённые поля. Произвольный код не исполняется.'}
    return {'score':round(100*sum(checks.values())/len(checks),2),'method':'bounded_work_model','checks':[{'id':k,'passed':v} for k,v in checks.items()],'all_checks_passed':all(checks.values())}
