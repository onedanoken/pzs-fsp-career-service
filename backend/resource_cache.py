"""Bounded UI/cache event traces and an independent contract oracle."""
import copy
import json
import secrets

FAMILY = 'frontend:resource_cache'
VERSION = 'resource-cache-v1'
MODEL_VERSION = 'resource-cache-model-v1'
RUBRIC_VERSION = 'resource-cache-rubric-v1'
CHECKS = ['cache_identity', 'cache_http_errors', 'cache_expiry']
MIDDLE_CHECKS = ['independent_views', 'invalidation_epoch', 'cancelled_response']
VARIANTS = {'Junior': ['page_collision', 'cached_failure'],
            'Middle': ['global_stream', 'obsolete_generation']}


def rubric(grade):
    middle = grade == 'Middle'
    return [
        {'id': 'correctness', 'label': 'Корректность решения', 'anchors': [
            'Страница/фильтр смешиваются в кеше или ошибка выдаётся как готовый список.' + (' Поздний ответ восстанавливает отменённые/инвалидированные данные.' if middle else ''),
            'Объяснён только успешный запрос; один из инвариантов кеша не обоснован.',
            'Обоснованы полный ключ ресурса, кеширование только успеха и строгая граница TTL.' + (' Разделены идентификатор запроса экрана, поколение ресурса и отмена.' if middle else ''),
            'Инварианты состояния и кеша сохранены и обоснованы при изменённом условии.'
        ]},
        {'id': 'verification', 'label': 'Проверки и воспроизводимость', 'anchors': [
            'Нет последовательности событий и ожидаемого состояния.',
            'Есть только успешная загрузка без отрицательного примера.',
            'Приведены разные страницы/фильтры, 500, пустой 200 и граница TTL с экраном/кешем/сетевыми запросами.' + (' Есть два экрана, ответ после invalidate и после cancel.' if middle else ''),
            'Ожидаемое состояние вычислено независимо после каждого события; разобраны перестановки ответов и новое условие.'
        ]},
        {'id': 'reasoning', 'label': 'Обоснование решения', 'anchors': [
            'Не объяснена связь ключа/приёма ответа с актуальностью данных.',
            'Упомянут кеш или отмена без механизма и контрпримера.',
            'Объяснены идентичность ресурса и возраст успешного ответа.' + (' Показано, почему AbortController и номер запроса не заменяют проверку поколения.' if middle else ''),
            'Альтернативы сопоставлены по свежести, числу запросов и последствиям устаревшего ответа.'
        ]},
        {'id': 'limits', 'label': 'Границы и компромиссы', 'anchors': [
            'JSON-план объявлен доказательством качества настоящего React-приложения.',
            'Назван риск без способа проверки.',
            'Указаны непроверенные DOM, клавиатура, браузерная сеть и смена пользователя.' + (' Названы ограничения модели отмены и инвалидирования.' if middle else ''),
            'Для нового условия заданы проверяемые риски и измерения: чужие данные, устаревшие ответы и сетевые запросы.'
        ]},
    ]


def generate(grade, rng, variant=None):
    if grade not in VARIANTS:
        raise ValueError('Семейство предназначено для Junior/Middle')
    variant = variant or rng.choice(VARIANTS[grade])
    if variant not in VARIANTS[grade]:
        raise ValueError('Неизвестный дефект уровня')
    ttl = rng.choice([10, 30, 60])
    filters = sorted(rng.sample(['open', 'closed', 'archived'], 2))
    condition = {'specialization': 'frontend', 'grade': grade, 'variant': variant,
                 'ttl_seconds': ttl, 'filters': filters, 'pages': [1, 2], 'model_version': MODEL_VERSION}
    reference = {'key': ['filter', 'page'], 'cache_statuses': [200],
                 'reuse': 'fresh_only', 'ttl_seconds': ttl}
    if grade == 'Middle':
        reference.update(streams='per_view', invalidated='discard', cancelled='discard')
    starter = copy.deepcopy(reference)
    if variant == 'page_collision':
        starter['key'] = ['filter']
        defect = 'После перехода на другую страницу показывается закешированная первая: ключ не различает страницы.'
    elif variant == 'cached_failure':
        starter['cache_statuses'] = [200, 500]
        defect = 'Ошибка 500 попадает в кеш; повторная загрузка показывает готовый пустой список вместо нового запроса.'
    elif variant == 'global_stream':
        starter['streams'] = 'global'
        defect = 'Два независимых экрана используют один идентификатор последнего запроса; ответ одного экрана теряется после запроса другого.'
    else:
        starter['invalidated'] = 'accept'
        defect = 'После изменения данных кеш очищен, но старый ответ возвращает прежнее содержимое на экран и в кеш.'
    requirements = [
        'key: один или два разных поля из filter и page; порядок полей несущественен. Разные фильтры и страницы — разные ресурсы.',
        'cache_statuses: непустой список разных целых статусов из 200 и 500. Успешный ответ 200 кешируется, включая пустой список; 500 показывает error и не кешируется.',
        f'reuse: fresh_only или any. ttl_seconds: целое 1…300. Контракт TTL — {ttl} секунд с момента ответа: кеш свежий только при возрасте строго меньше TTL.',
        'load(view, filter, page, time, id) устанавливает новый текущий запрос экрана. Свежий кеш даёт ready без сетевого запроса; иначе loading и один запрос. Старые данные при load очищаются.',
        'response(id, status, items, time) для последнего отправленного запроса показывает ready/items при 200 или error/пустой список при 500. Неизвестный ID и ответ на загрузку из кеша игнорируются.',
        'Ответ уже заменённого запроса не меняет ни экран, ни кеш. Сравниваются состояния после каждого события, включая сетевые запросы.',
        f'Примеры фильтров: {filters}; страницы 1 и 2. Сервер меняет фильтры, страницы, порядок ответов, содержимое и времена, включая точную границу TTL.',
        'Не добавляйте checks, код или другие поля. Редактируется ограниченная модель, а не настоящий обработчик fetch.',
    ]
    if grade == 'Middle':
        requirements += [
            'streams: per_view — текущий ID отдельно у каждого экрана; global — один общий ID. Два экрана должны работать независимо.',
            'invalidate(filter) увеличивает поколение этого фильтра, удаляет только его кеш и переводит соответствующие экраны в stale с пустыми данными. Остальные фильтры остаются доступными.',
            'invalidated: discard или accept. Ответ запроса, начатого до invalidate его фильтра, не должен менять экран или кеш, даже если ID ещё текущий.',
            'cancel(view) переводит этот экран в idle/пустой список. cancelled: discard или accept; поздний ответ отменённого запроса не меняет экран и кеш. Другой экран продолжает работать.',
            'Отмена в этой модели не гарантирует остановку транспорта: ответ всё равно может прийти. Новый load после отмены/инвалидирования должен работать.',
        ]
        followup = 'Пользователь сменил организацию без перезагрузки вкладки; прежний ответ ещё в пути. Как изменятся ключ, поколения, отмена и проверки, чтобы чужие данные не появились даже на один кадр?'
        scope = 'Проверьте также два экрана, отмену и ответ из старого поколения после изменения данных.'
    else:
        followup = 'Добавлена сортировка, а текущую страницу при её изменении нужно сбросить на первую. Как изменятся ключ ресурса, состояние экрана и отрицательные проверки?'
        scope = 'Рассматривается один экран; отмена и изменение серверных данных пока не проверяются.'
    return {'id': 'item_' + secrets.token_hex(8), 'kind': 'work_sample', 'competency': 'work_sample',
            'competencies': ['state', 'api_contracts', 'debugging'], 'stage': 'work_sample',
            'family': FAMILY, 'version': VERSION, 'rubric_version': RUBRIC_VERSION,
            'prompt': f'Экран показывает список с фильтром и страницей; TTL успешного ответа {ttl} секунд. {defect} Исправьте JSON-план. {scope}',
            'starter': json.dumps(starter, ensure_ascii=False, indent=2), 'requirements': requirements,
            'followup': followup, 'review_rubric': rubric(grade), 'max_length': 5000,
            'private': {'family': 'resource_cache', 'condition': condition,
                        'reference_answer': json.dumps(reference, ensure_ascii=False, indent=2),
                        'check_labels': CHECKS + (MIDDLE_CHECKS if grade == 'Middle' else [])}}


def validate_plan(plan, grade):
    fields = {'key', 'cache_statuses', 'reuse', 'ttl_seconds'}
    if grade == 'Middle':
        fields |= {'streams', 'invalidated', 'cancelled'}
    if not isinstance(plan, dict) or set(plan) != fields:
        raise ValueError('Поля плана кеша')
    key, statuses = plan['key'], plan['cache_statuses']
    if not isinstance(key, list) or not 1 <= len(key) <= 2 or any(x not in ('filter', 'page') for x in key) or len(set(key)) != len(key):
        raise ValueError('Ключ ресурса')
    if not isinstance(statuses, list) or not 1 <= len(statuses) <= 2 or any(type(x) is not int or x not in (200, 500) for x in statuses) or len(set(statuses)) != len(statuses):
        raise ValueError('Статусы кеша')
    if plan['reuse'] not in ('fresh_only', 'any') or type(plan['ttl_seconds']) is not int or not 1 <= plan['ttl_seconds'] <= 300:
        raise ValueError('TTL')
    if grade == 'Middle' and (plan['streams'] not in ('per_view', 'global') or any(plan[x] not in ('discard', 'accept') for x in ('invalidated', 'cancelled'))):
        raise ValueError('Приём ответа')


def snapshot(views, cache, network):
    return copy.deepcopy({'views': views, 'cache': sorted(cache.values(), key=lambda x: (x['filter'], x['page'])), 'network': network})


def execute(plan, events):
    """Apply the submitted policy to trusted, bounded event traces."""
    views, cache, requests, current, epochs = {}, {}, {}, {}, {}
    network, snapshots = [], []
    for event in events:
        kind = event['type']
        if kind == 'load':
            view, identity = event['view'], event['id']
            scope = '*' if plan.get('streams') == 'global' else view
            current[scope] = identity
            resource = tuple(event[name] for name in plan['key'])
            entry = cache.get(resource)
            hit = entry is not None and (plan['reuse'] == 'any' or event['time'] - entry['time'] < plan['ttl_seconds'])
            views[view] = {'filter': event['filter'], 'page': event['page'], 'status': 'ready' if hit else 'loading', 'items': entry['items'][:] if hit else []}
            requests[identity] = dict(event, generation=epochs.get(event['filter'], 0), sent=not hit, cancelled=False, scope=scope, resource=resource)
            if not hit:
                network.append(identity)
        elif kind == 'response':
            request = requests.get(event['id'])
            eligible = request is not None and request['sent'] and current.get(request['scope']) == event['id']
            if eligible and request['cancelled'] and plan.get('cancelled', 'discard') == 'discard':
                eligible = False
            if eligible and request['generation'] != epochs.get(request['filter'], 0) and plan.get('invalidated', 'discard') == 'discard':
                eligible = False
            if eligible:
                success = event['status'] == 200
                views[request['view']].update(status='ready' if success else 'error', items=event['items'][:] if success else [])
                if event['status'] in plan['cache_statuses']:
                    cache[request['resource']] = {'filter': request['filter'], 'page': request['page'], 'time': event['time'], 'items': event['items'][:] if success else [], 'generation': request['generation']}
                request['sent'] = False
        elif kind == 'invalidate':
            value = event['filter']
            epochs[value] = epochs.get(value, 0) + 1
            cache = {k: v for k, v in cache.items() if v['filter'] != value}
            for state in views.values():
                if state['filter'] == value:
                    state.update(status='stale', items=[])
        elif kind == 'cancel':
            view = event['view']
            for request in requests.values():
                if request['view'] == view:
                    request['cancelled'] = True
            if view in views:
                views[view].update(status='idle', items=[])
        snapshots.append(snapshot(views, cache, network))
    return snapshots


def oracle(events, ttl):
    """Canonical resource contract; accepts no submitted or reference plan."""
    views, cache, pending, latest, revisions, retired = {}, {}, {}, {}, {}, set()
    network, snapshots = [], []
    for event in events:
        kind = event['type']
        if kind == 'load':
            view, identity = event['view'], event['id']
            latest[view] = identity
            key = (event['filter'], event['page'])
            cached = cache.get(key)
            state = {'filter': key[0], 'page': key[1], 'status': 'loading', 'items': []}
            if cached is not None and event['time'] < cached['time'] + ttl:
                state.update(status='ready', items=cached['items'][:])
            else:
                pending[identity] = (view, key, revisions.get(key[0], 0))
                network.append(identity)
            views[view] = state
        elif kind == 'response':
            details = pending.pop(event['id'], None)
            if details is not None:
                view, key, revision = details
                valid = event['id'] not in retired and latest.get(view) == event['id'] and revision == revisions.get(key[0], 0)
                if valid and event['status'] == 200:
                    views[view].update(status='ready', items=event['items'][:])
                    cache[key] = {'filter': key[0], 'page': key[1], 'time': event['time'], 'items': event['items'][:], 'generation': revision}
                elif valid:
                    views[view].update(status='error', items=[])
        elif kind == 'invalidate':
            value = event['filter']
            revisions[value] = revisions.get(value, 0) + 1
            for key in list(cache):
                if key[0] == value:
                    del cache[key]
            for state in views.values():
                if state['filter'] == value:
                    state.update(status='stale', items=[])
        elif kind == 'cancel':
            view = event['view']
            retired.update(identity for identity, (owner, _, _) in pending.items() if owner == view)
            if view in views:
                views[view].update(status='idle', items=[])
        snapshots.append(snapshot(views, cache, network))
    return snapshots


def load(identity, view, value, page=1, time=0):
    return {'type': 'load', 'id': identity, 'view': view, 'filter': value, 'page': page, 'time': time}


def response(identity, items=None, status=200, time=1):
    return {'type': 'response', 'id': identity, 'status': status, 'items': ['new'] if items is None else items, 'time': time}


def scenarios(condition):
    a, b = condition['filters']
    ttl = condition['ttl_seconds']
    groups = {name: [] for name in CHECKS + (MIDDLE_CHECKS if condition['grade'] == 'Middle' else [])}
    for first_filter in (a, b):
        for other_filter, other_page in [(first_filter, 2), (b if first_filter == a else a, 1)]:
            groups['cache_identity'].append([load('a', 'main', first_filter), response('a', ['first']), load('b', 'main', other_filter, other_page, 2), response('b', ['second'], time=3), load('c', 'main', first_filter, time=4)])
    for value in (a, b):
        for items in ([], ['one', 'two']):
            groups['cache_http_errors'].append([load('a', 'main', value), response('a', status=500), load('b', 'main', value, time=2), response('b', items, time=3), load('c', 'main', value, time=4)])
        for age in (0, ttl - 1, ttl, ttl + 1):
            groups['cache_expiry'].append([load('a', 'main', value), response('a'), load('b', 'main', value, time=1 + age), response('b', ['fresh'], time=2 + age)])
        for order in [('a', 'b'), ('b', 'a')]:
            # Even Junior must not cache a superseded request or process unknown IDs.
            groups['cache_identity'].append([load('a', 'main', value), load('b', 'main', value, 2), response(order[0], [order[0]]), response(order[1], [order[1]], time=2), response('unknown', ['wrong'], time=3)])
    if condition['grade'] == 'Middle':
        for order in [('a', 'b'), ('b', 'a')]:
            for other_filter in (a, b):
                groups['independent_views'].append([load('a', 'left', a), load('b', 'right', other_filter, 2), response(order[0], [order[0]]), response(order[1], [order[1]], time=2)])
        for value in (a, b):
            other = b if value == a else a
            groups['invalidation_epoch'].append([load('other', 'right', other), response('other', ['keep']), load('old', 'left', value, time=2), {'type': 'invalidate', 'filter': value}, response('old', ['obsolete'], time=3), load('new', 'left', value, time=4), response('new', ['current'], time=5), load('hit', 'right', other, time=6)])
            groups['invalidation_epoch'].append([load('old', 'left', value), response('old'), {'type': 'invalidate', 'filter': value}, load('new', 'left', value, time=2), response('new', ['current'], time=3)])
            groups['cancelled_response'].append([load('a', 'left', value), load('b', 'right', other, 2), {'type': 'cancel', 'view': 'left'}, response('a', ['obsolete']), response('b', ['keep'], time=2), load('c', 'left', value, time=3), response('c', ['current'], time=4)])
    return groups


def evaluate(plan, condition):
    validate_plan(plan, condition['grade'])
    return {name: all(execute(plan, events) == oracle(events, condition['ttl_seconds']) for events in traces)
            for name, traces in scenarios(condition).items()}
