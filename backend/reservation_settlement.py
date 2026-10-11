"""Senior reservation/payment transitions and durable refunds in a finite model."""
import copy
import itertools
import json
import secrets

FAMILY = 'backend:reservation_settlement'
VERSION = 'reservation-settlement-v1'
MODEL_VERSION = 'reservation-settlement-model-v1'
RUBRIC_VERSION = 'reservation-settlement-rubric-v1'
VARIANTS = ['late_payment', 'lost_refund']
CHECKS = ['settlement_deadline', 'settlement_terminal', 'settlement_replay',
          'settlement_serialization', 'settlement_atomicity', 'settlement_ack',
          'settlement_dispatch', 'settlement_independent_refunds']
STATES = ['held', 'paid', 'expired']


def rubric():
    return [
        {'id': 'correctness', 'label': 'Корректность решения', 'anchors': [
            'Оплаченный резерв освобождается, поздняя оплата принимается или возврат теряется.',
            'Основной успех исправлен, но срок, повтор или восстановление нарушает контракт.',
            'Обоснованы терминальные состояния, граница срока, атомарность состояния/намерения возврата, подтверждение входа после фиксации и безопасная повторная доставка возврата.',
            'Сохранение товара и возврата обосновано для обеих очередностей гонки, всех точек сбоя и изменённого контракта провайдера.'
        ]},
        {'id': 'verification', 'label': 'Проверки и воспроизводимость', 'anchors': [
            'Нет событий, точек сбоя и независимо ожидаемого состояния.',
            'Проверена только своевременная оплата или названия тестов без результатов.',
            'Приведены события до/на/после срока, обе очередности оплаты/истечения, повторы, два разных платежа и сбои до commit и после внешнего эффекта; проверяются товар, намерения, возвраты и подтверждения.',
            'Независимые ожидания проверены после каждого события и сбоя; для нового условия заданы сценарии сверки и измерения зависших возвратов.'
        ]},
        {'id': 'reasoning', 'label': 'Обоснование решения', 'anchors': [
            'Локальная транзакция объявлена атомарной с внешним провайдером.',
            'Перечислены saga/outbox/idempotency без связи с потерянным событием или повтором эффекта.',
            'Разделены локальная фиксация, подтверждение брокеру и внешний эффект; объяснены актуальное чтение под сериализацией, стабильный ключ возврата и повтор после потерянного ответа.',
            'Альтернативы сопоставлены по точке принятия решения, блокировкам, неопределённому внешнему результату и операционной стоимости сверки.'
        ]},
        {'id': 'limits', 'label': 'Границы и компромиссы', 'anchors': [
            'Ограниченная модель представлена подтверждением полного Senior или реальной эксплуатации.',
            'Назван внешний риск без способа обнаружения и восстановления.',
            'Отмечены допущения: один подтверждённый платёж на заказ, доверенные события, единые часы, доступность провайдера и сохранение ключей; предложены владелец сверки и наблюдение за незавершёнными возвратами.',
            'При ограниченном сроке хранения ключей определены безопасный контракт повторов, обработка неизвестного результата, сверка/ручная эскалация и условия пересмотра решения.'
        ]},
    ]


def generate(grade, rng, variant=None):
    if grade != 'Senior':
        raise ValueError('Семейство предназначено для Backend Senior')
    variant = variant or rng.choice(VARIANTS)
    if variant not in VARIANTS:
        raise ValueError('Неизвестный дефект')
    quantity, available, deadline = rng.randint(1, 3), rng.randint(0, 3), rng.randint(2, 5)
    condition = {'specialization': 'backend', 'grade': grade, 'variant': variant,
                 'quantity': quantity, 'available': available, 'deadline': deadline,
                 'model_version': MODEL_VERSION}
    reference = {
        'payment': [
            {'states': ['held'], 'time': 'before_deadline', 'steps': ['pay']},
            {'states': ['held'], 'time': 'due', 'steps': ['expire', 'refund']},
            {'states': ['expired'], 'time': 'any', 'steps': ['refund']}],
        'expiry': [{'states': ['held'], 'time': 'due', 'steps': ['expire']}],
        'read': 'current', 'transaction': 'atomic', 'ack': 'after_commit',
        'refund_key': 'payment', 'dispatch_ack': 'after_effect'}
    starter = copy.deepcopy(reference)
    if variant == 'late_payment':
        starter['payment'][0]['time'] = 'any'
        defect = 'Первое правило принимает оплату удерживаемого резерва независимо от срока.'
    else:
        starter['dispatch_ack'] = 'before_effect'
        defect = 'Работник помечает возврат доставленным до вызова провайдера; сбой в промежутке теряет возврат.'
    requirements = [
        f'Два заказа A/B уже удерживают по {quantity} единиц одного товара, свободный остаток {available}. Срок A = {deadline}, B = {deadline + 1}; now и сроки — целые отметки общих часов. У каждого заказа один проверенный внешний платёж p-A/p-B.',
        'Состояния: held — товар удерживается; paid — товар окончательно продан; expired — товар возвращён в свободный остаток. Своевременный callback при now < deadline переводит held в paid. При now >= deadline held истекает; callback дополнительно создаёт намерение полного возврата. Callback для expired также требует возврат, для paid ничего не меняет.',
        'Истечение до срока ничего не меняет; после срока меняет только held. paid и expired терминальны в зафиксированных данных; черновики atomic до commit не видны другим событиям. Свободный остаток + товар held + товар paid равны исходному общему количеству после каждой зафиксированной операции.',
        'payment и expiry: 1–4 правила, выбирается первое совпавшее; отсутствие совпадения — без изменений. Правило содержит ровно states (непустой уникальный список held/paid/expired), time (before_deadline, due или any) и steps (1–3 различных операций pay, expire, refund). due означает now >= deadline.',
        'pay устанавливает paid; expire устанавливает expired и прибавляет количество заказа к свободному остатку; refund создаёт сохраняемую outbox-запись внешнего возврата. Операции сами не проверяют прежнее состояние: безопасность определяется условиями правила. refund допустим только в payment. Порядок expire/refund может различаться, если атомарно фиксируются оба изменения.',
        'read: current — решения событий сериализованы по товару/заказу и читают актуальное состояние; snapshot — события конкурентной пачки проверяют условия по общему начальному снимку, хотя действия затем фиксируются последовательно. Это режимы модели, не названия уровней изоляции конкретной БД.',
        'transaction: atomic — все действия одного callback и намерение возврата фиксируются вместе; split — каждое действие сразу сохраняется. Сбой до общей фиксации не должен оставлять частичного изменения. ack: after_commit или before_commit — когда событие подтверждается брокеру. Подтверждённое событие брокер не доставляет повторно; неподтверждённое доставляется после рестарта.',
        'refund_key: payment — ключ конкретного p-A/p-B; delivery — ключ ID доставки callback (новая доставка имеет другой ID); constant — один ключ для всех возвратов. Запись с существующим ключом не перезаписывается. Этот же ключ передаётся провайдеру; одинаковый ключ даёт один внешний эффект. В пределах модели срок хранения ключей не истекает.',
        'dispatch_ack: after_effect или before_effect — когда outbox помечается доставленным. Вызов провайдера и запись отметки не атомарны; возможен сбой между ними. После восстановления ожидаемые возвраты должны завершиться ровно по одному на платёж, включая два разных заказа.',
        'Исправьте только JSON. Дубли ключей, неизвестные поля, код и перечисление названий проверок не принимаются. Проверяется полная трасса, обе очередности событий, границы сроков и точки сбоя; параметры не ограничены одним успешным примером.',
    ]
    return {'id': 'item_' + secrets.token_hex(8), 'kind': 'work_sample', 'competency': 'work_sample',
            'competencies': ['api_contracts', 'debugging', 'tradeoffs'], 'stage': 'work_sample',
            'family': FAMILY, 'version': VERSION, 'rubric_version': RUBRIC_VERSION,
            'prompt': 'Резервирование связано с внешней оплатой и очередью возвратов. ' + defect + ' Исправьте правила переходов и доставки; одной локальной транзакции недостаточно для внешнего эффекта. Это ограниченный системный кейс, не подтверждение полного профессионального Senior.',
            'starter': json.dumps(starter, ensure_ascii=False, indent=2), 'requirements': requirements,
            'followup': 'Провайдер теперь хранит ключ возврата только сутки, а потерянный ответ обнаружен спустя двое суток. Какие гарантии модели перестали действовать? Опишите сверку неизвестного результата, безопасный контракт повторов, владельца восстановления, эскалацию и измерения.',
            'review_rubric': rubric(), 'max_length': 5000,
            'private': {'condition': condition, 'family': 'reservation_settlement',
                        'reference_answer': json.dumps(reference, ensure_ascii=False, indent=2), 'check_labels': CHECKS}}


def validate_plan(plan):
    fields = {'payment', 'expiry', 'read', 'transaction', 'ack', 'refund_key', 'dispatch_ack'}
    if not isinstance(plan, dict) or set(plan) != fields:
        raise ValueError('Поля плана расчёта')
    choices = {'read': ['current', 'snapshot'], 'transaction': ['atomic', 'split'],
               'ack': ['after_commit', 'before_commit'], 'refund_key': ['payment', 'delivery', 'constant'],
               'dispatch_ack': ['after_effect', 'before_effect']}
    if any(plan[key] not in allowed for key, allowed in choices.items()):
        raise ValueError('Неизвестный режим')
    for kind in ('payment', 'expiry'):
        if not isinstance(plan[kind], list) or not 1 <= len(plan[kind]) <= 4:
            raise ValueError('Правила переходов')
        for rule in plan[kind]:
            if not isinstance(rule, dict) or set(rule) != {'states', 'time', 'steps'}:
                raise ValueError('Поля правила')
            for key, allowed in [('states', STATES), ('steps', ['pay', 'expire', 'refund'])]:
                values = rule[key]
                if not isinstance(values, list) or not 1 <= len(values) <= 3 or any(not isinstance(x, str) or x not in allowed for x in values) or len(set(values)) != len(values):
                    raise ValueError('Операции или состояния')
            if rule['time'] not in ('before_deadline', 'due', 'any') or kind == 'expiry' and 'refund' in rule['steps']:
                raise ValueError('Условие времени или возврат без платежа')


def initial(condition):
    return {'available': condition['available'], 'orders': {
        name: {'state': 'held', 'quantity': condition['quantity'], 'deadline': condition['deadline'] + offset,
               'payment': 'p-' + name} for offset, name in enumerate(['A', 'B'])},
        'outbox': {}, 'acks': set(), 'provider_keys': set(), 'refunds': {}}


def observe(state):
    return {'available': state['available'], 'orders': {key: value['state'] for key, value in state['orders'].items()},
            'refund_intents': sorted((row['payment'], row['done']) for row in state['outbox'].values()),
            'acks': sorted(state['acks']), 'refunds': dict(state['refunds'])}


def select_steps(plan, event, current):
    before = event['now'] < current['deadline']
    return next((rule['steps'] for rule in plan[event['kind']] if current['state'] in rule['states'] and
                 (rule['time'] == 'any' or before == (rule['time'] == 'before_deadline'))), [])


def receive(state, plan, event, crash=None, guard_orders=None):
    if event['id'] in state['acks']:
        return
    working = copy.deepcopy(state) if plan['transaction'] == 'atomic' else state
    current = (guard_orders if guard_orders is not None else state['orders'])[event['order']]
    steps = select_steps(plan, event, current)
    if plan['ack'] == 'before_commit':
        state['acks'].add(event['id'])
    for index, step in enumerate(steps, 1):
        order = working['orders'][event['order']]
        if step == 'pay':
            order['state'] = 'paid'
        elif step == 'expire':
            order['state'] = 'expired'
            working['available'] += order['quantity']
        else:
            key = order['payment'] if plan['refund_key'] == 'payment' else event['id'] if plan['refund_key'] == 'delivery' else 'one-refund'
            working['outbox'].setdefault(key, {'payment': order['payment'], 'done': False})
        if crash == 'step_' + str(index):
            return
    if crash == 'before_commit':
        return
    if plan['transaction'] == 'atomic':
        state.update({key: working[key] for key in ['available', 'orders', 'outbox']})
    if crash == 'after_commit':
        return
    state['acks'].add(event['id'])


def dispatch(state, plan, crash=None):
    for key, row in state['outbox'].items():
        if row['done']:
            continue
        if plan['dispatch_ack'] == 'before_effect':
            row['done'] = True
            if crash == 'between':
                return
        if key not in state['provider_keys']:
            state['provider_keys'].add(key)
            state['refunds'][row['payment']] = state['refunds'].get(row['payment'], 0) + 1
        if plan['dispatch_ack'] == 'after_effect' and crash == 'between':
            return
        row['done'] = True


def execute(plan, condition, events):
    state, trace = initial(condition), []
    for event in events:
        if event['kind'] == 'dispatch':
            dispatch(state, plan)
            trace.append(observe(state))
        else:
            batch = event['events'] if event['kind'] == 'batch' else [event]
            guards = copy.deepcopy(state['orders']) if event['kind'] == 'batch' and plan['read'] == 'snapshot' else None
            for delivery in batch:
                receive(state, plan, delivery, guard_orders=guards)
                trace.append(observe(state))
    return trace


def oracle(condition, events):
    """Contract transitions without candidate rules, delivery modes or reference JSON."""
    available = condition['available']
    statuses = {'A': 'held', 'B': 'held'}
    deadlines = {'A': condition['deadline'], 'B': condition['deadline'] + 1}
    intents, refunded, acknowledged, result = {}, {}, set(), []
    for event in events:
        deliveries = event['events'] if event['kind'] == 'batch' else [event]
        for delivery in deliveries:
            kind = delivery['kind']
            if kind == 'dispatch':
                for payment in intents:
                    intents[payment] = True
                    refunded[payment] = 1
            elif delivery['id'] not in acknowledged:
                name = delivery['order']
                if statuses[name] == 'held':
                    if delivery['now'] >= deadlines[name]:
                        statuses[name] = 'expired'
                        available += condition['quantity']
                    elif kind == 'payment':
                        statuses[name] = 'paid'
                if kind == 'payment' and statuses[name] == 'expired':
                    intents.setdefault('p-' + name, False)
                acknowledged.add(delivery['id'])
            result.append({'available': available, 'orders': dict(statuses),
                           'refund_intents': sorted(intents.items()), 'acks': sorted(acknowledged), 'refunds': dict(refunded)})
    return result


def event(kind, now, identity, order='A'):
    return {'kind': kind, 'now': now, 'id': identity, 'order': order}


def evaluate(plan, condition):
    validate_plan(plan)
    checks = dict.fromkeys(CHECKS, True)
    deadline = condition['deadline']
    delivery = {'kind': 'dispatch'}
    def compare(label, events):
        checks[label] &= execute(plan, condition, events) == oracle(condition, events)
    for time in [0, deadline - 1, deadline, deadline + 1, deadline + 2]:
        compare('settlement_deadline', [event('payment', time, 'p'), delivery])
        compare('settlement_terminal', [event('expiry', time, 'e'), event('expiry', time, 'e2')])
    compare('settlement_terminal', [event('payment', deadline - 1, 'p'), event('expiry', deadline, 'e'), event('payment', deadline + 1, 'p2')])
    for first in ['payment', 'expiry']:
        events = [event(first, deadline, 'first'), event('payment', deadline + 1, 'second'), delivery,
                  event('payment', deadline + 2, 'third'), event('expiry', deadline + 2, 'fourth'), delivery]
        compare('settlement_replay', events)
    for kinds in itertools.product(['payment', 'expiry'], repeat=2):
        for times in [(deadline - 1, deadline - 1), (deadline - 1, deadline), (deadline, deadline)]:
            batch = [event(kind, time, str(index)) for index, (kind, time) in enumerate(zip(kinds, times))]
            compare('settlement_serialization', [{'kind': 'batch', 'events': batch}, delivery])
    # Before commit both order state and refund intent remain at the prior value.
    # After commit a missing broker ack is recovered by redelivery of the same ID.
    for kind, time in [('payment', deadline - 1), ('payment', deadline), ('expiry', deadline)]:
        incoming = event(kind, time, 'incoming')
        steps = select_steps(plan, incoming, initial(condition)['orders']['A'])
        for crash in ['before_commit', 'after_commit'] + ['step_' + str(index) for index in range(1, len(steps) + 1)]:
            state = initial(condition)
            receive(state, plan, incoming, crash)
            actual = observe(state)
            expected = oracle(condition, [incoming])[-1] if crash == 'after_commit' else observe(initial(condition))
            checks['settlement_atomicity'] &= {k: v for k, v in actual.items() if k != 'acks'} == {k: v for k, v in expected.items() if k != 'acks'}
            if crash != 'after_commit':
                checks['settlement_ack'] &= not state['acks']
            receive(state, plan, incoming)
            dispatch(state, plan)
            checks['settlement_ack'] &= observe(state) == oracle(condition, [incoming, delivery])[-1]
    # Two independent payments must each get a refund; duplicates cannot merge them.
    events = [event('payment', deadline + 1, 'a'), event('payment', deadline + 1, 'b', 'B'), delivery,
              event('payment', deadline + 2, 'a2'), event('payment', deadline + 2, 'b2', 'B'), delivery]
    compare('settlement_independent_refunds', events)
    for names in [('A',), ('A', 'B'), ('B', 'A')]:
        incoming = [event('payment', deadline + 1, 'p-' + name, name) for name in names]
        state = initial(condition)
        for value in incoming:
            receive(state, plan, value)
        dispatch(state, plan, 'between')
        dispatch(state, plan)
        dispatch(state, plan)
        checks['settlement_dispatch'] &= observe(state) == oracle(condition, incoming + [delivery])[-1]
    return checks
