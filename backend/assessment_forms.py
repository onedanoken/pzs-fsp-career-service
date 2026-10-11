"""Frozen quota assembly. Scope and difficulty are authored, not human-calibrated."""
import hashlib,json,random,secrets
from .assessment_catalog import GRADES

VERSION='balanced-core-2026-v3'
PATH_VERSION='work_sample_v3_balanced'
CORE={
 'backend':['data_processing','sql','api_contracts'],
 'frontend':['data_processing','state','accessibility'],
 'qa':['test_design','api_contracts','debugging'],
 'devops':['capacity','deployment','pipeline'],
 'ml':['metrics','splitting','leakage'],
 'security':['authorization','network','least_privilege'],
}
COUNT=9

def signature(item):
    private=item['private'];value={'kind':item['kind'],'competency':item['competency'],'family':item.get('family'),'prompt':item['prompt']}
    if item.get('choices'):
        expected=private['expected'];expected=expected if isinstance(expected,list) else [expected]
        value['choices']=sorted((c['text'],c['id'] in expected) for c in item['choices'])
    else:value['key']=private
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def accessibility(grade,rng):
    exists=rng.choice([True,False]);removed=rng.choice([True,False]);changed=rng.choice([True,False])
    scenarios=[
      ('focus_return',f'Диалог закрыт. Кнопка, открывшая его, {"ещё доступна" if exists else "удалена при обновлении списка"}. Куда вернуть клавиатурный фокус?', 'На кнопку открытия.' if exists else 'На следующий логичный доступный элемент экрана.', ['Оставить фокус на удалённом элементе.','Всегда отправлять фокус в адресную строку.']),
      ('tab_navigation',f'Форма содержит кнопку удаления, которая {"недоступна для данного объекта" if removed else "доступна"}. Что проверить при последовательном обходе клавишей Tab?', 'Недоступное действие не должно быть активируемым; обход остаётся логичным.' if removed else 'Действие достижимо с клавиатуры и имеет понятное имя.', ['Проверять только цвет кнопки.','Проверять исключительно клики мышью.']),
      ('status_announcement',f'После асинхронного сохранения форма {"остаётся на экране" if changed else "обновляет список без перехода на другой документ"}. Как сделать результат понятным пользователю скринридера без принудительного сброса ввода?', 'Объявить краткий статус через подходящую live-region, сохранив текущий фокус.', ['Перезагрузить страницу независимо от черновика.','Сообщить об успехе только цветом рамки.'])]
    result=[]
    for family,prompt,correct,wrong in scenarios:
        if grade in ['Middle','Senior']:prompt+=' После обновления DOM старые узлы могут исчезнуть; нельзя переносить фокус на недоступный элемент.'
        wrong.append({'focus_return':'На скрытую кнопку закрытого диалога.','tab_navigation':'Оставить активируемым действие, недоступное для данного объекта.','status_announcement':'Обновлять заголовок страницы на каждое нажатие клавиши вместо статуса.'}[family])
        options=[(True,correct)]+[(False,x) for x in wrong];rng.shuffle(options);choices=[];expected=None
        for valid,label in options:
            identity=secrets.token_hex(5);choices.append({'id':identity,'text':label})
            if valid:expected=identity
        result.append({'id':'item_'+secrets.token_hex(8),'kind':'choice','competency':'accessibility','family':'frontend:'+family,'version':VERSION,'prompt':prompt,'choices':choices,'source_ids':['w3c-dialog-pattern'],'private':{'expected':expected,'rationale':correct}})
    return result

def screening(spec,grade,seed,count=COUNT):
    from .item_generator import generate
    if count%3 or count<3 or count>12:raise ValueError('Three equal bounded competency quotas required')
    rng=random.Random(seed);quota=count//3;pools={key:[] for key in CORE[spec]};seen=set()
    if spec=='frontend':
        for _ in range(4):
            for item in accessibility(grade,rng):
                key=signature(item)
                if key not in seen:seen.add(key);pools['accessibility'].append(item)
    for _ in range(64):
        if all(len(v)>=quota for v in pools.values()):break
        batch=generate(spec,grade,allow_neural=False,qa_professional=spec=='qa',seed=rng.getrandbits(64))
        for item in batch['items']:
            if item['kind']=='explanation' or item['competency'] not in pools:continue
            key=signature(item)
            if key in seen:continue
            seen.add(key);pools[item['competency']].append(item)
    if not all(len(v)>=quota for v in pools.values()):raise ValueError('Insufficient distinct validated content')
    from .screening_operations import operations
    for item in operations(spec,grade,rng):
        key=signature(item)
        if key not in seen:
            seen.add(key);pools[item['competency']].append(item)
    from .grade_scenarios import operations as grade_operations
    decisions={item['competency']:item for item in grade_operations(spec,grade,rng)}
    selected=[]
    for competency,items in pools.items():
        invariants=[item for item in items if item.get('version')=='core-operations-2026-v2']
        rng.shuffle(invariants)
        # Fixed evidence roles prevent a random easy form from losing a grade case.
        items=items[:1]+invariants[:1]+[decisions[competency]]+invariants[1:]+items[1:]
        # Prefer distinct structural families, then distinct conditions within them.
        order=[];families=set()
        for item in items:
            family=item.get('family') or item['kind']
            if family not in families:order.append(item);families.add(family)
        order.extend(item for item in items if item not in order)
        for position,item in enumerate(order[:quota]):
            role='grade_decision' if item.get('version')=='grade-decisions-2026-v1' else 'generated_observation' if position==0 else 'professional_invariant'
            item=dict(item,stage='screening',assembly_competency=competency,evidence_role=role,difficulty_level=GRADES.index(grade)+1)
            selected.append(item)
    # Interleave areas; no three-question block can dominate the whole form.
    selected=[selected[group*quota+position] for position in range(quota) for group in range(3)]
    plan={'version':VERSION,'count':count,'quota':{key:quota for key in CORE[spec]},'core_competencies':CORE[spec], 'requested_grade':grade,'difficulty_level':GRADES.index(grade)+1,'difficulty_validation':'authored_constraints_not_empirical_irt','evidence_roles':['generated_observation','professional_invariant','grade_decision'],'scoring':'equal_item_weights_and_core_floor','coverage_scope':'Three core areas plus bounded work artifacts; not the entire profession.'}
    return selected,plan
