"""Bounded object authorization and revocation/state traces, never real IAM."""
import copy
import itertools
import json
import secrets

FAMILY = 'security:object_access'
VERSION = 'object-access-v1'
MODEL_VERSION = 'object-access-model-v1'
RUBRIC_VERSION = 'object-access-rubric-v1'
FACTORS = ['token_active', 'same_tenant', 'object_permission', 'action_scope']
CHECKS = ['sec_allowed_paths', 'sec_tenant_isolation', 'sec_object_authorization', 'sec_scope_token', 'sec_default_deny']
MIDDLE_CHECKS = ['sec_object_state', 'sec_live_decision', 'sec_use_recheck']
VARIANTS = {'Junior': ['missing_object', 'or_policy'],
            'Middle': ['cached_allow', 'stale_use']}


def rubric(grade):
    middle = grade == 'Middle'
    return [
        {'id': 'correctness', 'label': 'Корректность решения', 'anchors': [
            'Токен или роль заменяют проверку чужого объекта/организации.' + (' Старое разрешение используется после отзыва или изменения объекта.' if middle else ''),
            'Верна лишь позитивная ветвь; существенный отрицательный сценарий не обоснован.',
            'Обоснованы одновременно токен, организация, право на объект и scope; неизвестное действие запрещено.' + (' Состояние и текущий контекст перепроверяются перед использованием.' if middle else ''),
            'Инварианты доступа обоснованы также при изменённом условии.'
        ]},
        {'id': 'verification', 'label': 'Проверки и воспроизводимость', 'anchors': [
            'Нет входных контекстов и ожидаемого разрешения/запрета.',
            'Есть только успешное чтение своего документа.',
            'Есть чужой tenant/owner, неверная роль, scope, отозванный токен и неизвестное действие.' + (' Приведены revoke/move/lock между authorize и use.' if middle else ''),
            'Ожидания независимы от политики; проверены каждый переход, положительные контроли и новое условие.'
        ]},
        {'id': 'reasoning', 'label': 'Обоснование решения', 'anchors': [
            'Аутентификация объявлена достаточным правом на любой объект.',
            'Названы tenant или revoke без связи с доступом к конкретному объекту.',
            'Разделены аутентификация, объектное право и разрешённое действие.' + (' Объяснено, почему кеш и ранний ticket не заменяют проверку на момент use.' if middle else ''),
            'Альтернативы сопоставлены по свежести решений, времени отзыва и проверке риска.'
        ]},
        {'id': 'limits', 'label': 'Границы и компромиссы', 'anchors': [
            'JSON-политика объявлена доказательством защищённости настоящего API.',
            'Риск назван без способа проверки.',
            'Указаны непроверенные подпись токена, реальная БД, сеть и аудит.' + (' Отмечена атомарность recheck/use в этой модели, а не в настоящей распределённой системе.' if middle else ''),
            'Для нового условия заданы область исключения, измерения и условия пересмотра.'
        ]},
    ]


def generate(grade, rng, variant=None):
    if grade not in VARIANTS:
        raise ValueError('Семейство предназначено для Junior/Middle')
    variant = variant or rng.choice(VARIANTS[grade])
    if variant not in VARIANTS[grade]:
        raise ValueError('Неизвестный дефект уровня')
    tenants = rng.choice([['A', 'B'], ['north', 'south']])
    document = rng.choice(['doc-17', 'doc-29'])
    condition = {'specialization': 'security', 'grade': grade, 'variant': variant,
                 'tenants': tenants, 'document': document, 'model_version': MODEL_VERSION}
    reference = {'require': FACTORS[:], 'logic': 'all', 'unknown': 'deny'}
    requirements = [
        'require: массив разных известных факторов token_active, same_tenant, object_permission, action_scope. logic: all или any; unknown: deny или allow.',
        'token_active требует token.active=true; same_tenant сравнивает организацию субъекта и документа. Ни вход, ни роль editor не разрешают чужую организацию.',
        'object_permission: editor читает/изменяет документы своей организации; reader только читает собственный документ. Другие роли не имеют прав.',
        'action_scope требует присутствия действия в token.scopes. Только read/write известны приложению; export запрещён, даже если такой scope есть в токене.',
        'Все условия должны выполняться одновременно. Сервер меняет роли, владельца/организацию, scopes, активность и действие; правильный allow и каждый deny проверяются независимо.',
        f'Примеры организаций {tenants}; ID документа {document}. Это модель контекстов, не реальная подпись токена или middleware.',
    ]
    if grade == 'Middle':
        reference['require'].append('object_state')
        reference.update(cache='live', use='recheck')
        requirements += [
            'Дополнительный фактор object_state: active разрешает read/write, locked — только read, deleted и неизвестные состояния запрещают всё.',
            'cache: live или memoized. live заново вычисляет authorize по текущему контексту; memoized хранит решение по subject.id/object.id/action и не учитывает изменения token, tenant, owner, роли, scopes или state.',
            'use: recheck или authorize_only. Требуется ранее выданный allow-ticket и текущий allow. recheck вычисляет все факторы заново, обходя кеш; authorize_only доверяет раннему ticket.',
            'Ticket связан с subject.id/object.id/action. Отсутствующий ticket или другая идентичность запроса запрещены; этот обязательный контроль не отключается настройкой use.',
            'Между authorize/use могут произойти revoke, смена scope/tenant/owner/роли, lock/delete. Неодобренный ticket не становится действительным сам: после новых прав нужен новый authorize.',
            'Recheck и использование ресурса атомарны только в заданной модели. После каждого authorize/use сравнивается решение; проверяется и новое разрешение после восстановления доступа.',
        ]
        followup = 'Появилось временное исключение для внешнего рецензента. Как ограничить объект, действие и срок, обеспечить отзыв и не разрешить все документы чужой организации?'
    else:
        followup = 'Добавлено скачивание вложений документа. Как связать вложение с родительским объектом и повторить отрицательные проверки, чтобы нельзя было обойти политику прямым ID вложения?'
    starter = copy.deepcopy(reference)
    if variant == 'missing_object':
        starter['require'].remove('object_permission')
        defect = 'Reader получает чужой документ своей организации: право на объект не проверяется.'
    elif variant == 'or_policy':
        starter['logic'] = 'any'
        defect = 'Условия соединены OR: активный токен позволяет обойти организацию, владельца и scope.'
    elif variant == 'cached_allow':
        starter['cache'] = 'memoized'
        defect = 'Закешированное allow сохраняется после отзыва/переноса документа при новом authorize.'
    else:
        starter['use'] = 'authorize_only'
        defect = 'Ранний allow-ticket используется после отзыва доступа без повторной проверки текущего состояния.'
    return {'id': 'item_' + secrets.token_hex(8), 'kind': 'work_sample', 'competency': 'work_sample',
            'competencies': ['authorization', 'least_privilege', 'incident_response'], 'stage': 'work_sample',
            'family': FAMILY, 'version': VERSION, 'rubric_version': RUBRIC_VERSION,
            'prompt': 'Исправьте объектную политику доступа. ' + defect + (' Учитывайте переходы состояния и использование разрешения.' if grade == 'Middle' else ' Проверяется один текущий контекст; гонки/кеш этим уровнем не проверяются.'),
            'starter': json.dumps(starter, ensure_ascii=False, indent=2), 'requirements': requirements,
            'followup': followup, 'review_rubric': rubric(grade), 'max_length': 5000,
            'private': {'family': 'object_access', 'condition': condition,
                        'reference_answer': json.dumps(reference, ensure_ascii=False, indent=2),
                        'check_labels': CHECKS + (MIDDLE_CHECKS if grade == 'Middle' else [])}}


def validate_plan(plan, grade):
    fields = {'require', 'logic', 'unknown'} | ({'cache', 'use'} if grade == 'Middle' else set())
    if not isinstance(plan, dict) or set(plan) != fields:
        raise ValueError('Поля политики')
    factors = plan['require']
    allowed = FACTORS + (['object_state'] if grade == 'Middle' else [])
    if not isinstance(factors, list) or not 1 <= len(factors) <= len(allowed) or any(not isinstance(x, str) or x not in allowed for x in factors) or len(set(factors)) != len(factors):
        raise ValueError('Факторы')
    if plan['logic'] not in ('all', 'any') or plan['unknown'] not in ('deny', 'allow'):
        raise ValueError('Логика доступа')
    if grade == 'Middle' and (plan['cache'] not in ('live', 'memoized') or plan['use'] not in ('recheck', 'authorize_only')):
        raise ValueError('Кеш/использование')


def decide(plan, context):
    action, subject, obj, token = context['action'], context['subject'], context['object'], context['token']
    if action not in ('read', 'write') and plan['unknown'] == 'deny':
        return False
    values = {'token_active': token['active'] is True,
              'same_tenant': subject['tenant'] == obj['tenant'],
              'object_permission': subject['role'] == 'editor' or subject['role'] == 'reader' and action == 'read' and subject['id'] == obj['owner'],
              'action_scope': action in token['scopes'],
              'object_state': obj['state'] in ('active', 'locked') and (action != 'write' or obj['state'] == 'active')}
    factors = [values[name] for name in plan['require']]
    return all(factors) if plan['logic'] == 'all' else any(factors)


def oracle(context, grade):
    """Direct object contract, with no submitted factors, cache or saved key."""
    subject, obj, token = context['subject'], context['object'], context['token']
    if token['active'] is not True or subject['tenant'] != obj['tenant']:
        return False
    action = context['action']
    if action not in ('read', 'write') or action not in token['scopes']:
        return False
    if subject['role'] != 'editor' and not (subject['role'] == 'reader' and action == 'read' and obj['owner'] == subject['id']):
        return False
    if grade == 'Middle' and (obj['state'] not in ('active', 'locked') or obj['state'] == 'locked' and action != 'read'):
        return False
    return True


def context_for(condition, action='read', role='reader', owned=True, same_tenant=True, active=True, scoped=True, state='active'):
    a, b = condition['tenants']
    return {'subject': {'id': 'u1', 'tenant': a, 'role': role},
            'object': {'id': condition['document'], 'tenant': a if same_tenant else b, 'owner': 'u1' if owned else 'u2', 'state': state},
            'token': {'active': active, 'scopes': [action] if scoped else []}, 'action': action}


def execute(plan, initial, events):
    """Apply fixed context updates and decisions to one bounded request trace."""
    context = copy.deepcopy(initial)
    cache, tickets, observations = {}, {}, []
    for event in events:
        if event['type'] == 'change':
            if event['area']=='request':context[event['field']]=copy.deepcopy(event['value'])
            else:context[event['area']][event['field']] = copy.deepcopy(event['value'])
        elif event['type'] == 'authorize':
            key = (context['subject']['id'], context['object']['id'], context['action'])
            if plan['cache'] == 'memoized' and key in cache:
                allowed = cache[key]
            else:
                allowed = decide(plan, context)
                cache[key] = allowed
            tickets[event['ticket']] = {'allowed': allowed, 'key': key}
            observations.append({'type': 'authorize', 'ticket': event['ticket'], 'allowed': allowed})
        else:
            ticket = tickets.get(event['ticket'])
            key = (context['subject']['id'], context['object']['id'], context['action'])
            allowed = ticket is not None and ticket['key'] == key and ticket['allowed']
            if plan['use'] == 'recheck':
                allowed = allowed and decide(plan, context)
            observations.append({'type': 'use', 'ticket': event['ticket'], 'allowed': bool(allowed)})
    return observations


def trace_oracle(initial, events):
    current = copy.deepcopy(initial)
    approvals, result = {}, []
    for event in events:
        if event['type'] == 'change':
            if event['area']=='request':current[event['field']]=copy.deepcopy(event['value'])
            else:current[event['area']][event['field']] = copy.deepcopy(event['value'])
        elif event['type'] == 'authorize':
            identity = (current['subject']['id'], current['object']['id'], current['action'])
            approved = oracle(current, 'Middle')
            approvals[event['ticket']] = (identity, approved)
            result.append({'type': 'authorize', 'ticket': event['ticket'], 'allowed': approved})
        else:
            previous = approvals.get(event['ticket'])
            identity = (current['subject']['id'], current['object']['id'], current['action'])
            approved = previous is not None and previous[0] == identity and previous[1] and oracle(current, 'Middle')
            result.append({'type': 'use', 'ticket': event['ticket'], 'allowed': bool(approved)})
    return result


def transitions(condition):
    other = condition['tenants'][1]
    return [('token', 'active', False, True, 'read'),
            ('token', 'scopes', [], ['read'], 'read'),
            ('object', 'tenant', other, condition['tenants'][0], 'read'),
            ('object', 'owner', 'u2', 'u1', 'read'),
            ('object', 'state', 'locked', 'active', 'write'),
            ('object', 'state', 'deleted', 'active', 'read'),
            ('subject', 'role', 'guest', 'reader', 'read')]


def evaluate(plan, condition):
    grade = condition['grade']
    validate_plan(plan, grade)
    checks = dict.fromkeys(CHECKS + (MIDDLE_CHECKS if grade == 'Middle' else []), True)
    states = ['active', 'locked', 'deleted', 'unknown'] if grade == 'Middle' else ['active']
    for role, action, same, owned, active, scoped, state in itertools.product(['reader', 'editor', 'guest'], ['read', 'write', 'export'], [False, True], [False, True], [False, True], [False, True], states):
        context = context_for(condition, action, role, owned, same, active, scoped, state)
        actual, expected = decide(plan, context), oracle(context, grade)
        if expected:
            checks['sec_allowed_paths'] &= actual is True
        if not same:
            checks['sec_tenant_isolation'] &= actual is False
        if role == 'guest' or role == 'reader' and (not owned or action != 'read'):
            checks['sec_object_authorization'] &= actual is False
        if not active or not scoped:
            checks['sec_scope_token'] &= actual is False
        if action == 'export':
            checks['sec_default_deny'] &= actual is False
        if grade == 'Middle' and (state not in ('active', 'locked') or state == 'locked' and action == 'write'):
            checks['sec_object_state'] &= actual is False
    if grade == 'Middle':
        for area, field, denied, restored, action in transitions(condition):
            initial = context_for(condition, action=action, role='editor' if action == 'write' else 'reader')
            changed = {'type': 'change', 'area': area, 'field': field, 'value': denied}
            restored_event = dict(changed, value=restored)
            live_events = [{'type': 'authorize', 'ticket': 'a'}, changed, {'type': 'authorize', 'ticket': 'b'}, restored_event, {'type': 'authorize', 'ticket': 'c'}]
            use_events = [{'type': 'authorize', 'ticket': 'a'}, changed, {'type': 'use', 'ticket': 'a'}, restored_event, {'type': 'authorize', 'ticket': 'b'}, {'type': 'use', 'ticket': 'b'}]
            checks['sec_live_decision'] &= execute(plan, initial, live_events) == trace_oracle(initial, live_events)
            checks['sec_use_recheck'] &= execute(plan, initial, use_events) == trace_oracle(initial, use_events)
        # An earlier deny cannot become an allow-ticket merely because rights grew.
        initial = context_for(condition, active=False)
        events = [{'type': 'authorize', 'ticket': 'a'}, {'type': 'change', 'area': 'token', 'field': 'active', 'value': True}, {'type': 'use', 'ticket': 'a'}, {'type': 'authorize', 'ticket': 'b'}, {'type': 'use', 'ticket': 'b'}]
        checks['sec_live_decision'] &= execute(plan, initial, events) == trace_oracle(initial, events)
        checks['sec_use_recheck'] &= execute(plan, initial, events) == trace_oracle(initial, events)
        for area, field, value in [('subject','id','u2'),('object','id','another-document'),('request','action','write')]:
            initial=context_for(condition,role='editor')
            initial['token']['scopes']=['read','write']
            events=[{'type':'use','ticket':'missing'},{'type':'authorize','ticket':'a'},
                    {'type':'change','area':area,'field':field,'value':value}, {'type':'use','ticket':'a'}]
            checks['sec_use_recheck'] &= execute(plan, initial, events)==trace_oracle(initial, events)
    return checks
