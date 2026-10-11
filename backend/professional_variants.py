"""Owned, explicit professional scenarios; deterministic keys are not calibrated grades."""
import copy,hashlib,ipaddress,json,secrets
from fractions import Fraction

class Builder:
    def __init__(self,spec,grade,rng,criteria):
        self.spec,self.grade,self.rng,self.criteria=spec,grade,rng,criteria;self.items=[]
    def add(self,kind,competency,family,prompt,condition,expected=None,**fields):
        private={'condition':condition}
        if expected is None:private['rubric']=self.criteria+['Проверяемые инварианты','Измеримые проверки и границы применимости']
        else:private['expected']=expected
        self.items.append(dict(id='item_'+secrets.token_hex(8),kind=kind,competency=competency,family=self.spec+':'+family,prompt=prompt,private=private,**fields))
    def choice(self,competency,family,prompt,pairs,multi=False):
        condition={'prompt':prompt,'options':sorted(pairs,key=lambda x:x[1])}
        pairs=list(pairs);self.rng.shuffle(pairs);options=[];correct=[]
        for ok,text in pairs:
            identity=secrets.token_hex(5);options.append({'id':identity,'text':text})
            if ok:correct.append(identity)
        assert correct and len(correct)<len(options)
        self.add('multi' if multi else 'choice',competency,family,prompt,condition,correct if multi else correct[0],choices=options)

def condition_signature(items):
    # IDs, option order, wording outside conditions and presentation order are not diversity.
    conditions=[]
    for item in items:
        condition=copy.deepcopy(item['private']['condition']);family=item['family'].split(':',1)[1]
        if family=='owner_access':condition['owners'].sort()
        if family=='tenant_action':condition['rows'].sort()
        if family=='network_allowlist':condition['connections'].sort()
        if family in ['false_positives','error_cost']:
            condition['pairs']=sorted(zip(condition.pop('actual'),condition.pop('predicted')))
        if family in ['active_role_write','scoped_write']:
            roles=condition.pop('roles');disabled=condition.pop('disabled');scope=condition.pop('scope',[True]*len(roles))
            condition['requests']=sorted(zip(roles,disabled,scope))
        conditions.append({'kind':item['kind'],'competency':item['competency'],'family':item['family'],'condition':condition})
    encoded=[json.dumps(x,sort_keys=True,ensure_ascii=False,separators=(',',':')) for x in conditions]
    return hashlib.sha256(json.dumps(sorted(encoded),ensure_ascii=False).encode()).hexdigest()

def devops(b,level):
    r=b.rng;n=r.randint(5,23)
    percent=level>=2 and r.choice([True,False])
    if percent:
        u=r.choice([10,20,25,30]);allowed=n*u//100
        prompt=f'Deployment RollingUpdate: replicas={n}, maxUnavailable="{u}%", maxSurge=1. Бюджет maxUnavailable округляется вниз. Какое минимальное число доступных реплик допускает этот бюджет? Внешние сбои не учитываются; фактическую доступность приложение этим не гарантирует.'
        family='rollout_percent'
    else:
        u=r.randint(1,min(4,n-1));allowed=u
        prompt=f'Deployment RollingUpdate: replicas={n}, maxUnavailable={u}, maxSurge=1. Какое минимальное число доступных реплик допускает этот бюджет? Внешних сбоев нет; бюджет не является измерением фактической доступности.';family='rollout_integer'
    b.add('number','deployment',family,prompt,{'replicas':n,'unavailable':u,'percent':percent},n-allowed)
    memory=r.randint(8,24)*1024;reserve=r.randint(1,3)*1024;pod=r.choice([256,512,1024]);sidecar=r.choice([128,256])
    condition={'memory':memory,'reserve':reserve,'pod':pod,'sidecar':sidecar}
    prompt=f'Узел: {memory} MiB RAM, резерв ОС {reserve} MiB. В каждом pod requests приложения {pod} MiB и обычного sidecar {sidecar} MiB. Других pod, init-контейнеров и overhead нет.'
    expected=(memory-reserve)//(pod+sidecar)
    if level>=3 or level==2 and r.choice([True,False]):
        cpu=r.choice([2000,4000,8000]);cpu_reserve=500;pod_cpu=r.choice([250,500,1000]);sidecar_cpu=100
        condition.update(cpu=cpu,cpu_reserve=cpu_reserve,pod_cpu=pod_cpu,sidecar_cpu=sidecar_cpu)
        prompt+=f' CPU: {cpu}m, резерв {cpu_reserve}m; requests приложения {pod_cpu}m и sidecar {sidecar_cpu}m. Сколько pod одновременно поместится по обоим ресурсам? Affinity, лимит количества pod и диски не ограничивают размещение.'
        expected=min(expected,(cpu-cpu_reserve)//(pod_cpu+sidecar_cpu));family='cpu_memory_requests'
    else:prompt+=' Сколько pod поместится по requests памяти? CPU и остальные ограничения не учитываются.';family='memory_requests'
    b.add('number','capacity',family,prompt,condition,expected)
    a,c,d,e,f=[r.randint(10,90) for _ in range(5)]
    if level>=3 or level==2 and r.choice([True,False]):
        prompt=f'CI: A({a} с) и B({c} с) стартуют в момент 0. C({d} с) ждёт только A, D({e} с) ждёт только B. E({f} с) ждёт C и D. Исполнителей достаточно; накладных расходов и сбоев нет. Через сколько секунд завершится E?'
        b.add('debug','pipeline','branched_pipeline',prompt,{'durations':[a,c,d,e,f]},max(a+d,c+e)+f)
    else:
        b.add('debug','pipeline','parallel_join',f'CI: A({a} с) и B({c} с) запускаются параллельно; C({d} с) ждёт обе. Исполнителей достаточно, накладных расходов нет. Сколько секунд до окончания C?',{'durations':[a,c,d]},max(a,c)+d)
    options=[('semantic_sli','Новая версия возвращает HTTP 200, но теряет данные в ответе. Как обнаружить этот дефект?',[(True,'Проверять содержимое ответа и показатель корректных бизнес-операций.'),(False,'Считать все ответы 200 корректными независимо от содержимого.'),(False,'Проверять только время установления TCP-соединения.')]),('readiness_routing','Контейнер работает; liveness успешна, readiness неуспешна. Обычный Service не публикует неготовые endpoints. Какое действие связано именно с readiness?',[(True,'Исключить pod из готовых endpoints до восстановления готовности.'),(False,'Обязательно перезапустить контейнер только из-за readiness.'),(False,'Считать readiness подтверждением правильности любых бизнес-данных.')])]
    family,prompt,pairs=r.choice(options);b.choice('observability',family,prompt,pairs)
    options=[('schema_transition','Выберите условия совместимой миграции при одновременной работе старой и новой версии.',[(True,'Сохранить совместимость используемых обеими версиями полей.'),(True,'Проверить миграцию и сценарий возврата версии на отдельной копии.'),(False,'Первым шагом удалить столбец, который читает старая версия.'),(False,'Считать наличие backup доказательством успешного восстановления.')]),('release_gate','Какие проверки помогают ограничить ущерб от дефектного релиза?',[(True,'Заранее задать критерий остановки по бизнес-SLI и ошибкам.'),(True,'Проверить сохранность пользовательских данных при смене версии.'),(False,'Оценивать успех только по факту запуска процесса.'),(False,'Удалить предыдущий образ до проверки новой версии.')])]
    family,prompt,pairs=r.choice(options);b.choice('deployment',family,prompt,pairs,True)
    return r.choice(['отказ одной зоны во время релиза','совместимое изменение схемы БД','рост очереди доставки после сбоя зависимого сервиса'])

def ml(b,level):
    r=b.rng;tp,fp,fn,tn=[r.randint(4,30)*10 for _ in range(4)]
    metric=r.choice(['precision','recall'] if level==1 else ['precision','recall','F1','balanced accuracy'] if level==2 else ['F1','balanced accuracy'])
    value={'precision':Fraction(tp,tp+fp),'recall':Fraction(tp,tp+fn),'F1':Fraction(2*tp,2*tp+fp+fn),'balanced accuracy':(Fraction(tp,tp+fn)+Fraction(tn,tn+fp))/2}[metric]*100
    expected=(2*value.numerator+value.denominator)//(2*value.denominator)
    b.add('number','metrics','metric_'+metric.replace(' ','_'),f'Бинарная классификация без весов: TP={tp}, FP={fp}, FN={fn}, TN={tn}. Вычислите {metric} в процентах, округлите до целого: ровно .5 округляется вверх. Для balanced accuracy используйте средний recall двух классов.',{'tp':tp,'fp':fp,'fn':fn,'tn':tn,'metric':metric},expected)
    family=r.choice(['equal_groups'] if level==1 else ['equal_groups','unequal_groups','time_gap'])
    if family=='equal_groups':
        n=r.randint(12,40);size=r.randint(3,10);rate=r.choice([10,20,30])
        b.add('number','splitting',family,f'{n} независимых клиентов, у каждого {size} записи. В тест выделены {rate}% клиентов с округлением числа клиентов вниз. Все записи клиента находятся в одном split. Сколько записей в тесте?',{'groups':n,'size':size,'rate':rate},n*rate//100*size)
    elif family=='unequal_groups':
        sizes={f'G{i}':r.randint(3,15) for i in range(1,5+level)};selected=r.sample(list(sizes),2)
        b.add('number','splitting',family,f'Размеры независимых групп: {json.dumps(sizes)}. В тест целиком выделены {selected}, остальные в train. Сколько записей теста? Делить одну группу между split нельзя.',{'sizes':sizes,'selected':sorted(selected)},sum(sizes[x] for x in selected))
    else:
        n=r.randint(35,80);train=r.randint(15,25);gap=r.randint(3,8)
        b.add('number','splitting',family,f'{n} последовательных дневных наблюдений. Train — первые {train}; следующие {gap} полностью исключаются как временной gap; все остальные составляют test. Никакие наблюдения не перекрываются. Сколько наблюдений test?',{'total':n,'train':train,'gap':gap},n-train-gap)
    options=[('train_preprocessing','Scaler обучен на всех данных до выделения теста. Что исправляет утечку?',[(True,'Сначала выделить контроль; обучать preprocessing только на train.'),(False,'Увеличить число эпох, оставив preprocessing прежним.'),(False,'Перемешать test и train после обучения scaler.')]),('future_feature','Модель должна предсказывать отмену заказа в момент создания. В признаках есть дата фактической отмены, появляющаяся позже. Как подготовить признаки?',[(True,'Оставить только сведения, доступные в момент предсказания; исключить дату будущей отмены.'),(False,'Оставить дату отмены, потому что она повышает test-метрику.'),(False,'Заменить название столбца, сохранив его значения.')]),('group_leakage','Записи одного пользователя встречаются в train и test, хотя оцениваются новые пользователи. Как исправить разбиение?',[(True,'Разнести пользователей между train и test целиком и повторить оценку.'),(False,'Достаточно перемешать строки, сохранив тех же пользователей.'),(False,'Удалить только идентификатор, не меняя разбиение.')])]
    family,prompt,pairs=r.choice(options);b.choice('leakage',family,prompt,pairs)
    actual=[r.randint(0,1) for _ in range(8+2*level)];predicted=[r.randint(0,1) for _ in actual]
    # Every generated case includes both error directions, so an all-zero shortcut fails.
    actual[:2]=[0,1];predicted[:2]=[1,0]
    pairs=list(zip(actual,predicted));r.shuffle(pairs)
    actual,predicted=map(list,zip(*pairs))
    fp_count=sum(y==0 and p==1 for y,p in zip(actual,predicted));fn_count=sum(y==1 and p==0 for y,p in zip(actual,predicted))
    condition={'actual':actual,'predicted':predicted}
    if level>=3 or level==2 and r.choice([True,False]):
        fp_cost=r.randint(1,5);fn_cost=r.randint(6,15);condition.update(fp_cost=fp_cost,fn_cost=fn_cost)
        b.add('debug','validation','error_cost',f'В таблице все наблюдения этой задачи. Цена FP={fp_cost}, FN={fn_cost}, верных ответов=0. Положительный класс — 1. Какова суммарная стоимость ошибок?',condition,fp_count*fp_cost+fn_count*fn_cost,example_table={'name':'predictions','columns':['Истина','Прогноз'],'rows':list(zip(actual,predicted))})
    else:b.add('debug','validation','false_positives','В таблице все наблюдения этой задачи. Положительный класс — 1. Сколько FP: истина 0 и прогноз 1?',condition,fp_count,example_table={'name':'predictions','columns':['Истина','Прогноз'],'rows':list(zip(actual,predicted))})
    options=[('independent_control','Выберите условия воспроизводимой независимой оценки.',[(True,'Зафиксировать версии кода, данных и процедуру разбиения.'),(True,'Не выбирать параметры и лучший seed по итоговому test.'),(False,'Использовать test для выбора признаков и объявить оценку независимой.'),(False,'Сохранять только лучший результат, не записывая процедуру.')]),('deployment_validation','Какие проверки полезны после запуска модели?',[(True,'Сравнивать распределения признаков и проверять задержку появления истинных меток.'),(True,'Измерять качество на появившихся метках по важным группам.'),(False,'Считать отсутствие HTTP 500 доказательством качества предсказаний.'),(False,'Сразу считать все прогнозы истинными метками для переобучения.')])]
    family,prompt,pairs=r.choice(options);b.choice('validation',family,prompt,pairs,True)
    return r.choice(['переход от случайного разбиения к прогнозу будущих событий','дорогие ложные отрицания при ограниченном ручном разборе','изменение распределения после выпуска модели'])

def security(b,level):
    r=b.rng
    if level==1:
        actor=r.randint(1,8);owners=[actor,actor%8+1]+[r.randint(1,8) for _ in range(8)]
        b.add('number','authorization','owner_access',f'Пользователь {actor} читает документы с владельцами {owners}. Разрешён read только владельцу, иных прав нет. Сколько документов доступно?',{'actor':actor,'owners':owners},owners.count(actor))
    else:
        rows=[['A','write',True],['B','write',True]]+[[r.choice(['A','B']),r.choice(['read','write','delete']),r.choice([True,False])] for _ in range(5+level)]
        b.add('number','authorization','tenant_action','Текущая организация A; роль разрешает read/write, delete запрещён. Политика также требует активный объект той же организации. В таблице все запросы. Сколько разрешить?',{'rows':rows,'tenant':'A','actions':['read','write']},sum(org=='A' and action in ['read','write'] and active for org,action,active in rows),example_table={'name':'requests','columns':['Организация','Действие','Активен'],'rows':[[org,action,'Да' if active else 'Нет'] for org,action,active in rows]})
    if level==1 or level==2 and r.choice([True,False]):
        prefix=r.randint(24,28)
        b.add('number','network','subnet_hosts',f'Обычная IPv4-подсеть /{prefix}. Сколько адресов хостов после исключения network и broadcast? Исключения /31 и /32 не рассматриваются.',{'prefix':prefix},2**(32-prefix)-2)
    else:
        subnet=f'10.{r.randint(10,100)}.0.0/24';network=ipaddress.ip_network(subnet);base=str(network.network_address).split('.')[:2]
        connections=[['.'.join(base+['0','20']),443],['.'.join(base+['1','20']),443],['.'.join(base+['0','30']),80]]
        connections += [['.'.join(base+[r.choice(['0','1']),str(r.randint(1,254))]),r.choice([80,443,5432])] for _ in range(3+level)]
        b.add('number','network','network_allowlist',f'Исходящее соединение разрешено только к адресу в {subnet} и TCP-порту 443. Всё остальное запрещено. В таблице все попытки. Сколько разрешить?',{'subnet':subnet,'connections':connections,'port':443},sum(ipaddress.ip_address(ip) in network and port==443 for ip,port in connections),example_table={'name':'connections','columns':['Адрес назначения','TCP-порт'],'rows':connections})
    options=[('object_authorization','API проверяет вход пользователя, но не доступ к document_id. Какая проверка нужна?',[(True,'На сервере проверять пользователя, объект, организацию и действие в каждом запросе.'),(False,'Считать знание document_id разрешением читать объект.'),(False,'Разрешать любой объект после успешной проверки пароля.')]),('revoked_access','Согласие отозвано, а поисковый кеш ещё содержит профиль. Как принимать решение о выдаче?',[(True,'Проверить актуальное разрешение; при невозможности проверки не раскрывать профиль.'),(False,'Разрешить выдачу до истечения кеша независимо от отзыва.'),(False,'Считать старую выдачу постоянным разрешением на новые запросы.')])]
    family,prompt,pairs=r.choice(options);b.choice('authorization',family,prompt,pairs)
    roles=[r.choice(['reader','writer','admin']) for _ in range(8+level)];disabled=[r.choice([True,False]) for _ in roles]
    roles[:2]=['writer','writer'];disabled[:2]=[False,True]
    if level>=3:
        scope=[r.choice([True,False]) for _ in roles];scope[0]=True;scope[1]=True
        b.add('debug','least_privilege','scoped_write',f'Роли: {roles}; аккаунт заблокирован: {disabled}; scope write выдан: {scope}. Изменение требует writer/admin, незаблокированный аккаунт и write scope одновременно. Сколько запросов разрешить?',{'roles':roles,'disabled':disabled,'scope':scope},sum(role in ['writer','admin'] and not blocked and granted for role,blocked,granted in zip(roles,disabled,scope)))
    else:b.add('debug','least_privilege','active_role_write',f'Роли: {roles}; аккаунт заблокирован: {disabled}. Изменение разрешено writer/admin только при незаблокированном аккаунте; других правил нет. Сколько запросов разрешить?',{'roles':roles,'disabled':disabled},sum(role in ['writer','admin'] and not blocked for role,blocked in zip(roles,disabled)))
    options=[('token_incident','Выберите действия при подтверждённой утечке токена.',[(True,'Отозвать токен и проверить прекращение новых обращений с ним.'),(True,'Сохранить защищённые журналы и определить область доступа токена.'),(False,'Только убрать токен из репозитория, оставив его действующим.'),(False,'Публиковать токен в открытом сообщении об инциденте.')]),('document_incident','Резюме доступно по прямой ссылке без проверки пользователя. Какие действия устраняют причину и помогают проверить исправление?',[(True,'Закрыть неавторизованную выдачу на сервере и проверить права на документ.'),(True,'Проверить прямой запрос постороннего пользователя и защищённые журналы доступа.'),(False,'Только спрятать кнопку скачивания в интерфейсе.'),(False,'Считать длинный URL достаточным доказательством авторизации.')])]
    family,prompt,pairs=r.choice(options);b.choice('incident_response',family,prompt,pairs,True)
    return r.choice(['отзыв доступа при наличии кешей и внешней интеграции','нарушение изоляции документов двух организаций','локализация утечки сервисного ключа без потери журналов'])

def build(spec,grade,rng,criteria):
    from .assessment_catalog import GRADES
    level=GRADES.index(grade)+1;b=Builder(spec,grade,rng,criteria)
    focus={'devops':devops,'ml':ml,'security':security}[spec](b,level)
    context={'focus':focus,'services':rng.randint(3,12)*level,'teams':rng.randint(1,4),'grade':grade}
    b.add('explanation','tradeoffs','scenario_review',f'Команда поддерживает {context["services"]} сервисов; участвуют {context["teams"]} команды. Сценарий: {focus}. Объясните инварианты, проверки, компромиссы и границы решения. Укажите, каких данных не хватает. Ориентир уровня {grade}: '+ '; '.join(criteria)+'. Этот ответ разбирается экспертом отдельно.',context,max_length=3000)
    assert len(b.items)==6 and {x['kind'] for x in b.items}=={'number','debug','choice','multi','explanation'}
    return b.items
