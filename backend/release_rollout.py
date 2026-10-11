"""Versioned release authorization and bounded replica/schema transition model."""
import copy
import itertools
import json
import secrets

FAMILY = 'devops:release_rollout'
VERSION = 'release-rollout-v1'
MODEL_VERSION = 'release-rollout-model-v1'
RUBRIC_VERSION = 'release-rollout-rubric-v1'
CHECKS = ['release_checks', 'artifact_binding', 'failed_pipeline_stop']
MIDDLE_CHECKS = ['rollout_availability', 'rollout_capacity', 'schema_compatibility', 'rollback_recovery', 'rollout_completion']
STEPS = ['expand_schema', 'create_new', 'await_ready', 'remove_old', 'verify', 'complete']
VARIANTS = {'Junior': ['missing_scan', 'foreign_artifact'],
            'Middle': ['premature_drain', 'destructive_rollback']}


def rubric(grade):
    middle = grade == 'Middle'
    return [
        {'id': 'correctness', 'label': 'Корректность решения', 'anchors': [
            'Релиз проходит без допуска или с чужим артефактом.' + (' Rollout нарушает доступность, бюджет или совместимость старой версии.' if middle else ''),
            'Обоснован только успешный путь, отказ или откат не разобран.',
            'Обоснованы оба обязательных допуска, остановка при fail/skipped и тот же build.' + (' Готовность предшествует удалению; expand сохраняет старую версию, откат сохраняет данные.' if middle else ''),
            'Инварианты допуска и восстановления обоснованы также при изменённом условии.'
        ]},
        {'id': 'verification', 'label': 'Проверки и воспроизводимость', 'anchors': [
            'Названия проверок перечислены без входов и наблюдаемого результата.',
            'Есть лишь успешный релиз.',
            'Приведены pass/fail/skipped и чужой build с ожидаемым допуском.' + (' Есть состояния после действий, все места прерывания и конечный откат.' if middle else ''),
            'Проверки независимы от плана; промежуточные нарушения, границы бюджета и новое условие покрыты.'
        ]},
        {'id': 'reasoning', 'label': 'Обоснование решения', 'anchors': [
            'Не объяснена связь допуска с проверяемым артефактом.',
            'Сказано «добавить scan/rollback» без механизма.',
            'Различаются завершение задания, успешный допуск и артефакт.' + (' Разделены readiness, ресурсный запас, расширение схемы и разрушительное сжатие.' if middle else ''),
            'Альтернативы сопоставлены по времени релиза, ресурсам и последствиям отказа.'
        ]},
        {'id': 'limits', 'label': 'Границы и компромиссы', 'anchors': [
            'Модель объявлена проверкой работающего CI/кластера.',
            'Риск назван без способа проверки.',
            'Указаны непроверенные настоящий раннер, подпись образа и секреты.' + (' Отмечены непроверенные БД, маршрутизация, CPU/RAM и внешние зависимости.' if middle else ''),
            'Для нового условия заданы измерения, риск и условия пересмотра плана.'
        ]},
    ]


def generate(grade, rng, variant=None):
    if grade not in VARIANTS:
        raise ValueError('Семейство предназначено для Junior/Middle')
    variant = variant or rng.choice(VARIANTS[grade])
    if variant not in VARIANTS[grade]:
        raise ValueError('Неизвестный дефект уровня')
    build = rng.choice(['build-17', 'build-29'])
    condition = {'specialization': 'devops', 'grade': grade, 'variant': variant,
                 'build': build, 'model_version': MODEL_VERSION}
    reference = {'gates': ['tests', 'scan'], 'failure': 'stop', 'artifact': 'same_build'}
    requirements = [
        'gates: один или два разных задания из tests/scan. Оба должны завершиться pass; fail и skipped запрещают релиз.',
        'failure: stop или continue. stop требует pass каждого gate; continue принимает и fail/skipped. Исправьте допуск, а не только порядок заданий.',
        f'artifact: same_build или any. Релиз разрешён только для проверенного {build}; другой артефакт нельзя выпускать даже после успешных проверок.',
        'Сервер проверяет все сочетания pass/fail/skipped и проверенного/чужого артефакта. Решение — ограниченный JSON-план; checks, shell, YAML и другие поля не принимаются.',
    ]
    if grade == 'Middle':
        replicas, headroom = rng.choice([2, 3, 4]), rng.choice([1, 2])
        condition.update(replicas=replicas, slots=replicas + headroom)
        reference.update(steps=['expand_schema'] + ['create_new', 'await_ready', 'remove_old'] * replicas + ['verify', 'complete'],
                         max_surge=1, on_failure='rollback', schema_on_rollback='keep_expand')
        requirements += [
            f'В начале {replicas} готовых старых реплик; всего {replicas + headroom} слотов. Каждый экземпляр занимает один слот. В каждом состоянии готовых совместимых реплик не меньше {replicas}; слотов занято не больше бюджета.',
            f'max_surge: целое 0…{replicas + 2}; допустимый запас не больше slots−replicas. Поле ограничивает суммарные старые/новые/ожидающие реплики сверху replicas+max_surge.',
            'steps: 1…20 действий из expand_schema, create_new, await_ready, remove_old, verify, complete. create/await/remove можно повторять; complete ровно один раз в конце.',
            'expand_schema: аддитивно расширить old в expanded. Старое приложение читает old/expanded, новое — expanded/new_only. create_new: создать одну ожидающую реплику при доступном слоте; новая версия требует expanded до создания.',
            'await_ready: все ожидающие новые реплики становятся готовыми. remove_old: удалить одну старую. Удаление до готовности новой действительно снижает доступность — сервер проверяет промежуточное состояние.',
            'verify: требует готовую новую группу размера replicas без старых/ожидающих экземпляров. complete: требует успешный verify и тот же конечный состав; схема остаётся expanded.',
            'on_failure: rollback или leave. schema_on_rollback: keep_expand или contract. Безопасный откат возвращает старые replicas и удаляет новые/ожидающие, сохраняя expanded; contract переводит расширенную схему в new_only и ломает старое приложение.',
            'Прерывание возможно после любого действия до complete. Модель отката убирает ожидающие реплики, возвращает недостающие старые до удаления готовых новых; её промежуточные состояния тоже проверяются. Для неправильного плана места/доступности может не хватить.',
        ]
        followup = 'Теперь при expand появился внешний потребитель событий, который не поддерживает новые поля. Как изменятся совместимость, допуск, наблюдаемость и условия завершения миграции?'
    else:
        followup = 'После успешного scan артефакт пересобрали с тем же именем. Достаточно ли сравнения имени? Какие идентификаторы, повторные проверки и отрицательные сценарии нужны?'
    starter = copy.deepcopy(reference)
    if variant == 'missing_scan':
        starter['gates'] = ['tests']
        defect = 'После tests релиз проходит, даже если scan провален или пропущен.'
    elif variant == 'foreign_artifact':
        starter['artifact'] = 'any'
        defect = 'Допуски относятся к одному build, но релиз выпускает другой артефакт.'
    elif variant == 'premature_drain':
        starter['steps'] = ['expand_schema'] + ['create_new', 'remove_old', 'await_ready'] * condition['replicas'] + ['verify', 'complete']
        defect = 'Старая реплика удаляется до готовности новой; конечный состав выглядит верно, но доступность кратковременно падает.'
    else:
        starter['schema_on_rollback'] = 'contract'
        defect = 'Откат возвращает старое приложение и одновременно сжимает схему до new_only, которую оно не умеет читать.'
    return {'id': 'item_' + secrets.token_hex(8), 'kind': 'work_sample', 'competency': 'work_sample',
            'competencies': ['pipeline', 'deployment', 'observability'], 'stage': 'work_sample',
            'family': FAMILY, 'version': VERSION, 'rubric_version': RUBRIC_VERSION,
            'prompt': f'Выпускается проверенный артефакт {build}. {defect} Исправьте JSON-план допуска' + (' и rollout с восстановлением.' if grade == 'Middle' else '. Rollout и миграция БД этим уровнем не проверяются.'),
            'starter': json.dumps(starter, ensure_ascii=False, indent=2), 'requirements': requirements,
            'followup': followup, 'review_rubric': rubric(grade), 'max_length': 5000,
            'private': {'family': 'release_rollout', 'condition': condition,
                        'reference_answer': json.dumps(reference, ensure_ascii=False, indent=2),
                        'check_labels': CHECKS + (MIDDLE_CHECKS if grade == 'Middle' else [])}}


def validate_plan(plan, condition):
    fields = {'gates', 'failure', 'artifact'}
    if condition['grade'] == 'Middle':
        fields |= {'steps', 'max_surge', 'on_failure', 'schema_on_rollback'}
    if not isinstance(plan, dict) or set(plan) != fields:
        raise ValueError('Поля релиза')
    gates = plan['gates']
    if not isinstance(gates, list) or not 1 <= len(gates) <= 2 or any(x not in ('tests', 'scan') for x in gates) or len(set(gates)) != len(gates):
        raise ValueError('Допуски')
    if plan['failure'] not in ('stop', 'continue') or plan['artifact'] not in ('same_build', 'any'):
        raise ValueError('Артефакт/ошибка')
    if condition['grade'] == 'Middle':
        steps = plan['steps']
        if not isinstance(steps, list) or not 1 <= len(steps) <= 20 or any(x not in STEPS for x in steps) or steps[-1] != 'complete' or steps.count('complete') != 1:
            raise ValueError('Действия rollout')
        if type(plan['max_surge']) is not int or not 0 <= plan['max_surge'] <= condition['replicas'] + 2:
            raise ValueError('Запас реплик')
        if plan['on_failure'] not in ('rollback', 'leave') or plan['schema_on_rollback'] not in ('keep_expand', 'contract'):
            raise ValueError('Откат')


def gate_model(plan, event, build):
    passed = plan['failure'] == 'continue' or all(event[x] == 'pass' for x in plan['gates'])
    return passed and (plan['artifact'] == 'any' or event['artifact'] == build)


def gate_oracle(event, build):
    return event['tests'] == 'pass' and event['scan'] == 'pass' and event['artifact'] == build


def snapshot(state):
    old_healthy = state['old'] if state['schema'] in ('old', 'expanded') else 0
    new_healthy = state['new'] if state['schema'] in ('expanded', 'new_only') else 0
    return dict(state, available=old_healthy + new_healthy, total=state['old'] + state['new'] + state['pending'])


def execute(plan, condition, event, fault_after=None):
    replicas, slots = condition['replicas'], condition['slots']
    state = {'old': replicas, 'new': 0, 'pending': 0, 'schema': 'old', 'status': 'running'}
    trace = [snapshot(state)]
    if not gate_model(plan, event, condition['build']):
        state['status'] = 'blocked'
        return trace + [snapshot(state)]
    verified = False
    def recover():
        state['status'] = 'failed'
        trace.append(snapshot(state))
        if plan['on_failure'] == 'leave':
            return
        if plan['schema_on_rollback'] == 'contract' and state['schema'] == 'expanded':
            state['schema'] = 'new_only'
            trace.append(snapshot(state))
        state['pending'] = 0
        trace.append(snapshot(state))
        while state['old'] < replicas:
            if snapshot(state)['total'] >= slots:
                if state['new'] == 0:
                    return
                state['new'] -= 1
                trace.append(snapshot(state))
            state['old'] += 1
            trace.append(snapshot(state))
        while state['new']:
            state['new'] -= 1
            trace.append(snapshot(state))
        state['status'] = 'rolled_back'
        trace.append(snapshot(state))
    for index, step in enumerate(plan['steps']):
        failed = False
        if step == 'expand_schema':
            state['schema'] = 'expanded'
        elif step == 'create_new':
            if state['schema'] != 'expanded' or snapshot(state)['total'] >= min(slots, replicas + plan['max_surge']):
                failed = True
            else:
                state['pending'] += 1
                verified = False
        elif step == 'await_ready':
            state['new'] += state['pending']
            state['pending'] = 0
        elif step == 'remove_old':
            if state['old'] == 0:
                failed = True
            else:
                state['old'] -= 1
                verified = False
        elif step == 'verify':
            verified = state['old'] == state['pending'] == 0 and state['new'] == replicas and state['schema'] == 'expanded'
            failed = not verified
        else:
            if verified and state['old'] == state['pending'] == 0 and state['new'] == replicas and state['schema'] == 'expanded':
                state['status'] = 'deployed'
            else:
                failed = True
        trace.append(snapshot(state))
        if failed or fault_after == index:
            recover()
            break
    return trace


def rollout_oracle(trace, condition, interrupted):
    """Observe invariants/terminal contract without a submitted or reference plan."""
    replicas, slots = condition['replicas'], condition['slots']
    terminal = trace[-1]
    compatible = all((x['old'] == 0 or x['schema'] in ('old', 'expanded')) and (x['new'] + x['pending'] == 0 or x['schema'] in ('expanded', 'new_only')) for x in trace)
    recovered = terminal['status'] == 'rolled_back' and terminal['old'] == replicas and terminal['new'] == terminal['pending'] == 0 and terminal['schema'] in ('old', 'expanded')
    completed = terminal['status'] == 'deployed' and terminal['new'] == replicas and terminal['old'] == terminal['pending'] == 0 and terminal['schema'] == 'expanded'
    available = lambda x: (x['old'] if x['schema'] in ('old', 'expanded') else 0) + (x['new'] if x['schema'] in ('expanded', 'new_only') else 0)
    return {'availability': all(available(x) >= replicas for x in trace),
            'capacity': all(0 <= x['old'] + x['new'] + x['pending'] <= slots and min(x['old'], x['new'], x['pending']) >= 0 for x in trace),
            'compatibility': compatible, 'terminal': recovered if interrupted else completed}


def evaluate(plan, condition):
    validate_plan(plan, condition)
    build = condition['build']
    states = ['pass', 'fail', 'skipped']
    checks = dict.fromkeys(CHECKS, True)
    for tests, scan, artifact in itertools.product(states, states, [build, 'unverified-build']):
        event = {'tests': tests, 'scan': scan, 'artifact': artifact}
        actual, expected = gate_model(plan, event, build), gate_oracle(event, build)
        checks['release_checks'] &= actual == expected
        if tests == scan == 'pass':
            checks['artifact_binding'] &= actual == expected
        if tests != 'pass' or scan != 'pass':
            checks['failed_pipeline_stop'] &= actual == expected
    if condition['grade'] == 'Middle':
        event = {'tests': 'pass', 'scan': 'pass', 'artifact': build}
        normal = execute(plan, condition, event)
        observations = [rollout_oracle(normal, condition, False)]
        failures = [rollout_oracle(execute(plan, condition, event, index), condition, True) for index in range(len(plan['steps']) - 1)]
        observations += failures
        checks.update(rollout_availability=all(x['availability'] for x in observations),
                      rollout_capacity=plan['max_surge'] <= condition['slots'] - condition['replicas'] and all(x['capacity'] for x in observations),
                      schema_compatibility=all(x['compatibility'] for x in observations),
                      rollback_recovery=all(x['terminal'] for x in failures),
                      rollout_completion=observations[0]['terminal'])
    return checks
