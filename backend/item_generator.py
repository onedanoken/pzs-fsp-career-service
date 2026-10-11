"""Constrained neural conditions; objective keys computed independently."""
import hashlib,json,random,secrets
from .assessment_catalog import BLUEPRINT_VERSION,GRADES,SOURCES,source_snapshot

from .neural_provider import Variant,neural_variant

def numeric_signature(values,threshold,operation):
    """Context wording and array order cannot disguise identical conditions."""
    return hashlib.sha256(json.dumps({'values':sorted(values),'threshold':threshold,'operation':operation},sort_keys=True).encode()).hexdigest()

def generate(specialization,grade,allow_neural=True,qa_professional=False,seed=None):
    seed=secrets.randbits(64) if seed is None else seed;rng=random.Random(seed);level=GRADES.index(grade)+1
    if specialization=='qa' and qa_professional:
        from .qa_screening import generate as qa_generate
        return qa_generate(grade,rng,seed)
    if specialization in {'devops','ml','security'}:
        from .professional_items import tasks
        items=tasks(specialization,grade,rng)
        for task in items:
            task['version']=BLUEPRINT_VERSION;task['sha256']=hashlib.sha256(json.dumps(task,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        from .professional_variants import condition_signature
        return {'items':items,'condition_signature':condition_signature(items),'seed':str(seed),'blueprint_version':BLUEPRINT_VERSION,'generator':'professional_validated_variants','neural_items':0,'total_items':len(items),'sources':[s['id'] for s in SOURCES],'sources_snapshot':source_snapshot(),'tested_skills':[],'calibration':'pilot_unvalidated','limits':{'duration_minutes':45}}
    variant,metadata=neural_variant(specialization,grade,rng) if allow_neural else (None,{'generator':'validated_variants','generator_error':'neural_variant_duplicate'})
    values=variant.values if variant else rng.choices(rng.sample(range(5,161),5+level),k=7+2*level)
    limit=variant.threshold if variant else rng.randint(min(values)+1,max(values))
    rule=variant.operation if variant else rng.choice(['sum','count','max'])
    teams=rng.sample(['Альфа','Бета','Гамма','Дельта','Омега'],3)
    subset=[x for x in values if x>=limit]
    expected={'sum':sum(subset),'count':len(subset),'max':max(subset,default=0)}[rule]
    context=variant.context if variant else {'backend':'Сервис обрабатывает размеры поступивших пакетов.','frontend':'Панель показывает длительность загрузки карточек в миллисекундах.','qa':'Тестовый прогон собирает длительности ответов в миллисекундах.'}[specialization]
    items=[]
    def item(kind,competency,prompt,private,**public):
        items.append({'id':'item_'+secrets.token_hex(8),'kind':kind,'competency':competency,'prompt':prompt,'private':private,**public})
    item('number','data_processing',context+f'\nДанные: {values}. Отберите значения ≥ {limit}. Верните '+{'sum':'сумму','count':'количество','max':'максимум (0 для пустой выборки)'}[rule]+'.',{'expected':expected})
    items[-1]['origin']='neural_conditions' if variant and metadata.get('neural_items')==1 else 'reference_protocol_conditions' if variant else 'validated_variant'
    if specialization=='frontend':
        a,b=rng.sample(range(10,200),2)
        item('debug','state',f'Компонент запускает запрос A ({a} мс), затем через 5 мс запрос B ({b} мс). Каждый завершившийся запрос записывает результат в состояние без проверки актуальности. Какой номер запроса останется: 1 для A, 2 для B? Отмена и кэширование отсутствуют.',{'expected':1 if a>b+5 else 2})
    else:
        rows=[[rng.choice(teams),rng.randrange(10,160),rng.choice(['ok','failed'])] for _ in range(8+3*level)];threshold=rng.randrange(20,60)
        def sql_expected(data):
            result={}
            for team,amount,status in data:
                if status=='ok' and amount>=threshold:result[team]=result.get(team,0)+amount
            return [[k,v] for k,v in result.items()]
        hidden=[[[rng.choice(teams),rng.randrange(10,160),rng.choice(['ok','failed'])] for _ in range(11)] for _ in range(2)]
        item('sql','sql',f'Таблица events(team TEXT, amount INTEGER, status TEXT): {json.dumps(rows,ensure_ascii=False)}. Верните team и SUM(amount) для status="ok" и amount ≥ {threshold}, сгруппировав по team. Диалект SQLite; порядок строк не важен. Запрос также проверяется на скрытых наборах данных.',{'rows':rows,'expected':sql_expected(rows),'hidden_tests':[{'rows':r,'expected':sql_expected(r)} for r in hidden]})
    families={
        'backend':[
            ('Запрос создания записи повторён после потери ответа. Как обеспечить ровно одну запись?',[(True,'Хранить ключ запроса и результат атомарно; вернуть сохранённый результат повтора.'),(False,'Создавать новую запись при каждом повторе.'),(False,'Добавить задержку перед записью.')]),
            ('Два запроса читают один остаток, затем каждый списывает деньги. Как сохранить инвариант остаток ≥ 0?',[(True,'Проверять достаточность и изменять остаток в одной атомарной операции.'),(False,'Проверять остаток только в интерфейсе.'),(False,'Сначала сообщить об успехе, потом записать новый остаток.')]),
            ('Клиент использует прежний ключ идемпотентности с другой суммой. Как обработать изменение?',[(True,'Отклонить конфликт данных ключа без создания новой записи.'),(False,'Вернуть успех и проигнорировать новую сумму.'),(False,'Создать вторую запись с тем же ключом.')])],
        'frontend':[
            ('После быстрого переключения фильтра более старый fetch завершается последним. Как защитить актуальность экрана?',[(True,'Сравнить идентификатор запроса с текущим перед обновлением состояния.'),(False,'Всегда отображать последний полученный ответ.'),(False,'Увеличить длительность анимации загрузки.')]),
            ('fetch вернул HTTP 404. Как интерфейсу определить ошибку HTTP?',[(True,'Проверить response.ok или response.status и показать состояние ошибки.'),(False,'Ожидать, что любой HTTP 404 автоматически попадёт в catch.'),(False,'Считать любой ответ fetch успешным результатом.')])],
        'qa':[
            ('Как проверить правильность суммы выбранных элементов независимо от порядка входных данных?',[(True,'Переставить элементы и проверить неизменность суммы.'),(False,'Проверить только порядок цветов в интерфейсе.'),(False,'Повторить ровно один исходный пример без изменений.')]),
            ('Как проверить повтор API после потери ответа?',[(True,'Повторить тот же ключ и проверить число записей и сохранённый результат.'),(False,'Использовать новый ключ и ожидать одну запись.'),(False,'Проверить только HTTP 200 без проверки состояния.')])]
    }
    from .case_bank import select_case,diagnostic_case
    family,competency,choice_prompt,choices=select_case(specialization,grade,rng,families[specialization])
    rng.shuffle(choices);options=[];correct=None
    for key,label in choices:
        identity=secrets.token_hex(5);options.append({'id':identity,'text':label})
        if key:correct=identity
    item('choice',competency,choice_prompt,{'expected':correct},choices=options,family=specialization+':'+family)
    family,competency,prompt,expected=diagnostic_case(specialization,grade,rng)
    item('debug',competency,prompt,{'expected':expected},family=specialization+':'+family)
    labels=[('repeat','Повтор запроса после потери ответа'),('other_key','Два разных ключа запросов'),('race','Два одновременных запроса с одним ключом'),('color','Цвет кнопки')]
    if level>=3:labels.append(('changed','Тот же ключ с изменёнными данными'))
    rng.shuffle(labels);options=[];expected=[]
    for key,label in labels:
        identity=secrets.token_hex(5);options.append({'id':identity,'text':label})
        if key!='color':expected.append(identity)
    item('multi','test_design','Выберите проверки корректности идемпотентного API создания записи. Рассматриваем серверный контракт, не оформление интерфейса.',{'expected':expected},choices=options)
    focus={'backend':'атомарность записи и результат повторного запроса','frontend':'актуальность состояния, отмену запросов и доступное отображение ошибок','qa':'границы данных, конкурентные операции и воспроизводимость дефекта'}[specialization]
    item('explanation','tradeoffs',f'Команда «{teams[0]}» ожидает {level*25} одновременных пользователей. Предложите решение: {focus}. Объясните проверки на ошибках сети. Назовите измеримые критерии и ограничения.',{'rubric':['Конкретный проверяемый инвариант','Учтена конкуренция','Есть измеримый критерий','Названы ограничения']},max_length=3000)
    for task in items:
        task['version']=BLUEPRINT_VERSION;task['sha256']=hashlib.sha256(json.dumps(task,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return {'items':items,'condition_signature':numeric_signature(values,limit,rule),'legacy_condition_signature':hashlib.sha256(items[0]['prompt'].encode()).hexdigest(),'numeric_conditions':{'values':values,'threshold':limit,'operation':rule},'seed':str(seed),'blueprint_version':BLUEPRINT_VERSION,**metadata,'neural_items':1 if variant else 0,'total_items':len(items),'sources':[s['id'] for s in SOURCES],'sources_snapshot':source_snapshot(),'tested_skills':(['sql'] if specialization!='frontend' else []),'calibration':'pilot_unvalidated','limits':{'duration_minutes':45}}

def public_item(item):
    result={k:v for k,v in item.items() if k not in {'private','sha256'}}
    if item['kind']=='sql' and 'rows' in item['private']:
        rows=item['private']['rows']
        result['example_table']={'name':'events','columns':['team','amount','status'],'rows':rows}
        result['prompt']=result['prompt'].replace(json.dumps(rows,ensure_ascii=False),'(пример данных в таблице ниже)',1)
    return result

def grade_answer(item,answer):
    from .sandbox import sql_check,python_check
    kind=item['kind'];expected=item['private'].get('expected')
    if kind=='work_sample':
        from .work_samples import evaluate
        return evaluate(item,answer)
    if kind in {'number','debug'}:
        try:return {'score':100 if float(answer)==expected else 0,'method':'numeric_oracle'}
        except (ValueError,TypeError):return {'score':0,'method':'numeric_oracle'}
    if kind=='choice':
        result={'score':100 if answer==expected else 0,'method':'contract_oracle'}
        if item['private'].get('rationale'):result['feedback']=item['private']['rationale']
        return result
    if kind=='multi':
        try:
            decoded=json.loads(answer)
            if not isinstance(decoded,list) or any(not isinstance(v,str) for v in decoded):raise ValueError()
            selected=set(decoded);target=set(expected);score=max(0,(len(selected&target)-len(selected-target))/len(target))*100
        except (ValueError,TypeError):score=0
        return {'score':round(score,2),'method':'test_coverage_rubric'}
    if kind=='sql':
        tests=[{'rows':item['private']['rows'],'expected':expected}]+item['private'].get('hidden_tests',[])
        passed=sum(sql_check(answer,t['rows'],t['expected'])['passed'] for t in tests)
        return {'score':100*passed/len(tests),'method':'sql_oracle'}
    if kind=='python':
        result=python_check(answer,item['private']['tests']);return {'score':100*result['passed']/result['total'],'method':'isolated_python'}
    return {'score':None,'method':'human_review_required','rubric':item['private']['rubric']}
