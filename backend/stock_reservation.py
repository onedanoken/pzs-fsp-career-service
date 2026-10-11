"""Versioned reservation plans executed by a bounded model, never user code."""
import json
import secrets

FAMILY = 'backend:stock_reservation'
VERSION = 'stock-reservation-v2'
RUBRIC_VERSION = 'stock-reservation-rubric-v2'
V1_VERSION = 'stock-reservation-v1'
V1_RUBRIC_VERSION = 'stock-reservation-rubric-v1'
STEPS = ['validate', 'check_stock', 'decrement', 'record']
CHECKS = ['quantity_contract', 'reservation_balance', 'rejection_preserves_state']
MIDDLE_CHECKS = ['concurrent_reservations', 'atomic_recovery', 'restart_continuation']
VARIANTS = {
    'Junior': ['quantity_boundary', 'write_before_check'],
    'Middle': ['lost_update', 'partial_commit'],
}


def rubric_v1(grade):
    """Concrete observations for this family; the existing four gates stay intact."""
    middle = grade == 'Middle'
    return [
        {'id': 'correctness', 'label': 'Корректность решения', 'anchors': [
            'Резерв создаётся для недопустимого количества или остаток меняется при отказе.',
            'Один дефект исправлен, но другой сценарий нарушает контракт резерва.',
            'Количество и остаток проверяются до записи; резерв и списание согласованы.' +
            (' Конкурирующие запросы не продают один остаток дважды; сбой откатывает обе записи.' if middle else ''),
            'Инвариант «остаток + сумма резервов = начальный остаток» обоснован и для нового условия.'
        ]},
        {'id': 'verification', 'label': 'Проверки и воспроизводимость', 'anchors': [
            'Не приведены вход, HTTP-статус и ожидаемые остаток/резервы.',
            'Есть только успешный резерв без отказов и проверки состояния.',
            'Проверены 1, лимит, 0, лимит + 1, дробь и нехватка; при отказе состояние неизменно.' +
            (' Есть два конкурирующих запроса и сбой после списания.' if middle else ''),
            'Эталон состояния независим от плана; проверены все точки сбоя и продолжение после рестарта.'
            if middle else 'Приведены независимые ожидаемые результаты, контрпример исходному дефекту и проверки нового условия.'
        ]},
        {'id': 'reasoning', 'label': 'Обоснование решения', 'anchors': [
            'Нет связи исправления с контрактом и сохранением остатка.',
            'Упомянута валидация или транзакция без конкретного механизма.',
            'Объяснено, почему проверка предшествует изменению состояния.' +
            (' Разделены изоляция конкурирующих запросов и атомарность двух записей.' if middle else ''),
            'Альтернативы сопоставлены по блокировкам, длительности транзакции и последствиям отказа.'
            if middle else 'Сопоставлены проверка до записи и компенсация; показано, почему компенсация здесь не нужна.'
        ]},
        {'id': 'limits', 'label': 'Границы и компромиссы', 'anchors': [
            'Модель объявлена доказательством надёжности реального API.',
            'Назван риск без сценария или способа проверки.',
            'Указаны непроверенные повторы ключа, внешняя оплата и свойства реальной БД.'
            if middle else 'Указано, что конкуренция, рестарт и повторы запроса этим уровнем не проверены.',
            'Для нового условия предложены измерения и условия пересмотра: конфликты, зависшие резервы, сверка остатков.'
            if middle else 'Для нового условия определены контракт, наблюдаемое нарушение и независимая проверка.'
        ]},
    ]


def generate_v1(grade, rng, variant=None):
    if grade not in VARIANTS:
        raise ValueError('Семейство предназначено для Junior/Middle')
    variant = variant or rng.choice(VARIANTS[grade])
    if variant not in VARIANTS[grade]:
        raise ValueError('Неизвестный дефект уровня')
    limit, stock = rng.randint(2, 8), rng.randint(3, 12)
    condition = {'specialization': 'backend', 'grade': grade, 'variant': variant,
                 'limit': limit, 'stock': stock, 'oracle_version': V1_VERSION}
    reference = {'quantity': {'type': 'integer', 'min': 1, 'max': limit}, 'steps': STEPS.copy()}
    if grade == 'Middle':
        reference.update(concurrency='locked', transaction='atomic')
    starter = json.loads(json.dumps(reference))
    if variant == 'quantity_boundary':
        starter['quantity'].update(type='number', min=0, max=limit + 1)
        defect = 'Валидация пропускает ноль, дробь и количество сверх лимита.'
    elif variant == 'write_before_check':
        starter['steps'] = ['validate', 'decrement', 'check_stock', 'record']
        defect = 'При нехватке товара запрос меняет остаток до проверки и возвращает отказ.'
    elif variant == 'lost_update':
        starter['concurrency'] = 'snapshot'
        defect = 'Два запроса читают один остаток, затем каждый записывает вычисленное значение: обновление теряется.'
    else:
        starter['transaction'] = 'split'
        defect = 'После списания, но до записи резерва процесс падает; при рестарте списание остаётся без резерва.'
    requirements = [
        'quantity: ровно type (integer или number), min и max (целые числа). integer отвергает также boolean; number допускает дроби, но не boolean.',
        'steps: до четырёх различных операций из validate, check_stock, decrement, record; выполните необходимые операции в правильном порядке.',
        'validate проверяет тип и диапазон quantity; при отказе HTTP 422. check_stock сравнивает текущий остаток с количеством; при нехватке HTTP 409.',
        'decrement вычитает количество из текущего остатка; record добавляет запись резерва с этим количеством. Завершённый план возвращает HTTP 201.',
        f'Контракт: только целое количество от 1 до {limit} включительно; при любом отказе остаток и резервы неизменны.',
        'При успехе ровно один резерв; остаток + сумма резервов равны начальному остатку. Не добавляйте другие поля или программный код.',
    ]
    if grade == 'Middle':
        requirements += [
            'concurrency: locked — запрос удерживает блокировку одного товара на весь план; snapshot — конкурирующие запросы берут начальный снимок и позднее перезаписывают остаток.',
            'transaction: atomic — изменения остатка и резерва фиксируются вместе только после успешного плана; split — каждая операция сохраняется сразу.',
            'Сбой возможен после любой операции до общей фиксации; незавершённый запрос не оставляет изменений. После рестарта сохраняются только зафиксированные данные.',
        ]
        followup = 'Добавлена внешняя оплата: ответ провайдера может потеряться, а резерв живёт 15 минут. Что нельзя гарантировать одной транзакцией БД? Опишите повтор, компенсацию, сроки и сверку.'
        scope = 'Проверьте также конкуренцию двух запросов, каждую точку сбоя и продолжение после рестарта. Повторы ключа и внешние сервисы не входят в автоматическую модель.'
    else:
        followup = 'Один запрос теперь резервирует две разные позиции; второй позиции не хватает. Как изменятся контракт, порядок действий и проверки неизменности обеих позиций при отказе?'
        scope = 'Запросы выполняются последовательно, без сбоев процесса. Конкуренция, повтор запроса и восстановление здесь не проверяются.'
    return {
        'id': 'item_' + secrets.token_hex(8), 'kind': 'work_sample', 'competency': 'work_sample',
        'competencies': ['api_contracts', 'debugging'], 'stage': 'work_sample', 'family': FAMILY,
        'version': V1_VERSION, 'rubric_version': V1_RUBRIC_VERSION,
        'prompt': f'API резервирует один товар. Начальный остаток {stock}, лимит одного запроса {limit}. {defect} Исправьте JSON-план. {scope}',
        'starter': json.dumps(starter, ensure_ascii=False, indent=2), 'requirements': requirements,
        'followup': followup, 'review_rubric': rubric_v1(grade), 'max_length': 5000,
        'private': {'condition': condition, 'family': 'stock_reservation',
                    'reference_answer': json.dumps(reference, ensure_ascii=False, indent=2),
                    'check_labels': CHECKS + (MIDDLE_CHECKS if grade == 'Middle' else [])},
    }


def rubric(grade):
    criteria = rubric_v1(grade)
    if grade == 'Middle':
        criteria[0]['anchors'][:3] = [
            'План допускает продажу сверх остатка или сохраняет списание без резерва при сбое; объяснение это не исправляет.',
            'Объяснён только последовательный успех; защита от конкуренции или сбоя не обоснована.',
            'Обоснованы контракт количества, изоляция конкурирующих запросов и совместная фиксация остатка/резерва. Черновая запись внутри atomic до валидации допустима, если при отказе и сбое она не фиксируется.',
        ]
        criteria[2]['anchors'][2] = 'Объяснено, почему отказ проверяется до общей фиксации. Разделены изоляция конкурирующих запросов и атомарность двух записей; черновое состояние не смешивается с сохранённым.'
    else:
        criteria[0]['anchors'][2] = 'Обоснованы контракт количества и отказ без изменения данных; проверки предшествуют немедленно сохраняемым изменениям.'
    return criteria


def generate(grade, rng, variant=None):
    """Clarified v2 content; the unchanged v1 interpreter checks both versions."""
    item = generate_v1(grade, rng, variant)
    item.update(version=VERSION, rubric_version=RUBRIC_VERSION, review_rubric=rubric(grade))
    item['prompt'] += ' Указанный остаток — пример для ручного разбора. План должен соблюдать контракт и при других неотрицательных остатках, включая ноль и нехватку; сервер проверяет такие сценарии.'
    item['requirements'] += [
        'Невалидное количество всегда даёт 422, даже если товара также не хватает. Целое передаётся без десятичной точки и экспоненты; 1.0 и 1e0 не соответствуют integer.',
        'Конечный остаток неотрицателен. Проверка меняет начальные остатки; нельзя подгонять решение только под число из примера.',
    ]
    if grade == 'Middle':
        item['requirements'].append('В atomic записи до общей фиксации являются черновыми: отказ или сбой удаляет их. Оценивается сохранённое состояние, а не буквальное совпадение порядка операций с одним эталоном. snapshot — только режим этой модели, а не уровень изоляции настоящей БД.')
    return item


def validate_plan(plan, grade):
    fields = {'quantity', 'steps'} | ({'concurrency', 'transaction'} if grade == 'Middle' else set())
    if not isinstance(plan, dict) or set(plan) != fields:
        raise ValueError('Поля плана резервирования')
    quantity, steps = plan['quantity'], plan['steps']
    if not isinstance(quantity, dict) or set(quantity) != {'type', 'min', 'max'}:
        raise ValueError('Поля количества')
    if quantity['type'] not in ('integer', 'number') or any(type(quantity[k]) is not int or not -10000 <= quantity[k] <= 10000 for k in ('min', 'max')) or quantity['min'] > quantity['max']:
        raise ValueError('Диапазон количества')
    if not isinstance(steps, list) or not 1 <= len(steps) <= 4 or any(not isinstance(x, str) or x not in STEPS for x in steps) or len(set(steps)) != len(steps):
        raise ValueError('Операции плана')
    if grade == 'Middle' and (plan['concurrency'] not in ('locked', 'snapshot') or plan['transaction'] not in ('atomic', 'split')):
        raise ValueError('Режим исполнения')


def execute(plan, stock, quantities, concurrent=False, crash_after=None):
    """Interpret only four fixed operations on local state and bounded inputs.

    A snapshot batch prepares all reads before the writes. A locked batch executes
    one whole request at a time. Crashes occur before the final atomic commit.
    """
    initial = stock
    reservations, statuses = [], []
    atomic = plan.get('transaction') == 'atomic'
    for quantity in quantities:
        local = initial if concurrent and plan.get('concurrency') == 'snapshot' else stock
        staged = []
        status = 201
        for step in plan['steps']:
            try:
                if step == 'validate':
                    rule = plan['quantity']
                    types = (int,) if rule['type'] == 'integer' else (int, float)
                    if type(quantity) not in types or not rule['min'] <= quantity <= rule['max']:
                        status = 422
                elif step == 'check_stock':
                    if local < quantity:
                        status = 409
                elif step == 'decrement':
                    local -= quantity
                    if not atomic:
                        stock = local
                else:
                    staged.append(quantity)
                    if not atomic:
                        reservations.append(quantity)
            except TypeError:
                status = 500
            if status != 201:
                break
            if step == crash_after:
                status = 'interrupted'
                break
        if atomic and status == 201:
            stock = local
            reservations.extend(staged)
        statuses.append(status)
    return {'stock': stock, 'reservations': reservations, 'statuses': statuses}


def oracle(stock, quantities, limit):
    """Contract oracle has no plan, operation ordering or reference answer input."""
    reservations, statuses = [], []
    for quantity in quantities:
        if type(quantity) is not int or not 1 <= quantity <= limit:
            statuses.append(422)
        elif quantity > stock:
            statuses.append(409)
        else:
            stock -= quantity
            reservations.append(quantity)
            statuses.append(201)
    return {'stock': stock, 'reservations': reservations, 'statuses': statuses}


def evaluate(plan, condition):
    grade, limit = condition['grade'], condition['limit']
    validate_plan(plan, grade)
    invalid = [-1, 0, limit + 1, 1.5, True, False, '1', None]
    checks = dict.fromkeys(CHECKS + (MIDDLE_CHECKS if grade == 'Middle' else []), True)
    stocks = sorted(set(range(2 * limit + 1)) | {condition['stock']})
    # Exhaust the finite valid input domain at every relevant balance, including
    # balances other than the public example. Do not compare to the authored plan.
    for stock in stocks:
        for quantity in invalid + list(range(1, limit + 1)):
            actual, expected = execute(plan, stock, [quantity]), oracle(stock, [quantity], limit)
            label = 'quantity_contract' if type(quantity) is not int or not 1 <= quantity <= limit else 'reservation_balance'
            checks[label] &= actual == expected
            if expected['statuses'][0] != 201:
                checks['rejection_preserves_state'] &= actual == expected
    if grade == 'Middle':
        for stock in stocks:
            for first in range(1, limit + 1):
                for second in range(1, limit + 1):
                    checks['concurrent_reservations'] &= execute(plan, stock, [first, second], concurrent=True) == oracle(stock, [first, second], limit)
        for step in STEPS:
            crashed = execute(plan, condition['stock'], [1], crash_after=step)
            checks['atomic_recovery'] &= crashed == {'stock': condition['stock'], 'reservations': [], 'statuses': ['interrupted']}
            # Durable state survives; an independent fresh request follows restart.
            resumed = execute(plan, crashed['stock'], [1])
            resumed['reservations'] = crashed['reservations'] + resumed['reservations']
            checks['restart_continuation'] &= resumed == oracle(condition['stock'], [1], limit)
    return checks
