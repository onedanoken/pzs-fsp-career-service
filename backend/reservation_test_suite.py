"""Evaluate authored QA test data against a contract and bounded mutant models."""
import copy
import json
import math
import secrets

FAMILY = 'qa:reservation_test_suite'
VERSION = 'reservation-tests-v1'
MODEL_VERSION = 'qa-reservation-model-v1'
RUBRIC_VERSION = 'reservation-tests-rubric-v1'
CHECKS = ['suite_oracle', 'suite_positive_negative', 'suite_boundary_types', 'suite_mutations']
MIDDLE_CHECKS = ['idempotency_detection', 'race_detection', 'recovery_detection']
MUTANTS = ['accept_zero', 'accept_over_limit', 'allow_shortage', 'drop_reservation']
VARIANTS = {'Junior': ['success_only', 'copied_expectations'],
            'Middle': ['isolated_requests', 'copied_crash_state']}


def rubric(grade):
    middle = grade == 'Middle'
    return [
        {'id': 'correctness', 'label': 'Корректность решения', 'anchors': [
            'Ожидания повторяют дефект приложения или противоречат контракту.',
            'Верны только отдельные успешные ожидания; состояния отказа не обоснованы.',
            'Ожидания HTTP и состояния выведены из контракта, включая границы и отказ без изменений.' + (' Для повтора, конфликта, гонки и сбоя явно заданы остаток и записи.' if middle else ''),
            'Ожидания и инварианты обоснованы также при изменённом условии.'
        ]},
        {'id': 'verification', 'label': 'Проверки и воспроизводимость', 'anchors': [
            'Вместо входов/ожиданий перечислены названия тестов.',
            'Проверен лишь позитивный пример или только статус ответа.',
            'Покрыты 1, лимит, 0, лимит + 1, типы и нехватка с независимым состоянием.' + (' Есть связанная последовательность, заданная гонка и обе точки сбоя.' if middle else ''),
            'Для каждого дефекта приведён различающий контрпример; добавлены независимые проверки нового условия.'
        ]},
        {'id': 'reasoning', 'label': 'Обоснование решения', 'anchors': [
            'Не объяснено, откуда получены ожидаемые значения.',
            'Сказано «проверить границы/гонку» без связи с дефектом.',
            'Показано, какой неправильный алгоритм различает каждый существенный тест.' + (' Разделены новое состояние, повтор и незавершённая транзакция.' if middle else ''),
            'Сопоставлены слабые и сильные утверждения; объяснены наблюдаемость, повторяемость и новый риск.'
        ]},
        {'id': 'limits', 'label': 'Границы и компромиссы', 'anchors': [
            'Прохождение моделей объявлено доказательством отсутствия дефектов реального API.',
            'Назван риск без отдельной проверки.',
            'Указаны непроверенные транспорт, БД, права и нагрузка.' + (' Отмечена заданная детерминированная модель гонки, а не все расписания настоящей системы.' if middle else ''),
            'Для нового условия предложены риск, наблюдаемый результат и основания изменения тестового плана.'
        ]},
    ]


def oracle(case, limit, grade):
    """Expected state comes from the contract, not the app model or saved key."""
    stock = case['stock']
    if grade == 'Junior':
        quantity = case['quantity']
        if type(quantity) is not int or not 1 <= quantity <= limit:
            return {'status': 422, 'stock': stock, 'reserved': 0}
        if quantity > stock:
            return {'status': 409, 'stock': stock, 'reserved': 0}
        return {'status': 201, 'stock': stock - quantity, 'reserved': quantity}
    statuses, reservations = [], {}
    for index, request in enumerate(case['requests']):
        key, quantity = request['key'], request['quantity']
        if type(quantity) is not int or not 1 <= quantity <= limit:
            statuses.append(422)
        elif key in reservations:
            statuses.append(200 if reservations[key] == quantity else 409)
        elif quantity > stock:
            statuses.append(409)
        elif index == 0 and case['fault_at'] != 'none':
            # Restart discards the uncommitted request before the next one.
            statuses.append('interrupted')
        else:
            stock -= quantity
            reservations[key] = quantity
            statuses.append(201)
    return {'statuses': statuses, 'stock': stock, 'reserved': reservations}


def model(case, limit, grade, mutant=None):
    """Execute only a fixed reservation model, optionally with one known defect."""
    balance, records, statuses = case['stock'], {}, []
    requests = [{'key': 'a', 'quantity': case['quantity']}] if grade == 'Junior' else case['requests']
    snapshot = balance
    for index, request in enumerate(requests):
        key, quantity = request['key'], request['quantity']
        minimum = 0 if mutant == 'accept_zero' else 1
        maximum = limit + 1 if mutant == 'accept_over_limit' else limit
        if type(quantity) is not int or not minimum <= quantity <= maximum:
            statuses.append(422)
            continue
        if grade == 'Middle' and key in records:
            if records[key] != quantity:
                statuses.append(409)
                continue
            if mutant != 'duplicate_debit':
                statuses.append(200)
                continue
        read = snapshot if mutant == 'lost_update' and case.get('schedule') == 'parallel' else balance
        if read < quantity and mutant != 'allow_shortage':
            statuses.append(409)
            continue
        staged = read - quantity
        fault = case.get('fault_at', 'none') if index == 0 else 'none'
        if mutant == 'non_atomic':
            balance = staged
        if fault == 'after_debit':
            statuses.append('interrupted')
            continue
        if mutant == 'non_atomic':
            records[key] = quantity
        if fault == 'after_record':
            statuses.append('interrupted')
            continue
        balance = staged
        if mutant != 'drop_reservation':
            records[key] = quantity
        statuses.append(201)
    if grade == 'Junior':
        return {'status': statuses[0], 'stock': balance, 'reserved': sum(records.values())}
    return {'statuses': statuses, 'stock': balance, 'reserved': records}


def reference_suite(grade, limit):
    if grade == 'Junior':
        cases = [{'stock': stock, 'quantity': quantity} for stock, quantity in
                 [(limit, 1), (limit, limit), (limit, 0), (limit, limit + 1),
                  (0, 1), (limit, True), (limit, 1.5), (limit, '1'), (limit, None)]]
    else:
        def case(quantities, stock=None, schedule='serial', fault='none'):
            return {'stock': limit if stock is None else stock,
                    'requests': [{'key': key, 'quantity': quantity} for key, quantity in quantities],
                    'schedule': schedule, 'fault_at': fault}
        cases = [case([('a', 1), ('a', 1)]), case([('a', limit)]),
                 case([('a', 0), ('a', limit + 1), ('a', None)]),
                 case([('a', True), ('a', 1.5), ('a', '1')]), case([('a', 1)], stock=0),
                 case([('a', 1), ('a', 2)]),
                 case([('a', limit), ('b', limit)], schedule='parallel'),
                 case([('a', 1), ('a', 1)], fault='after_debit'),
                 case([('a', 1), ('a', 1)], fault='after_record')]
    for case in cases:
        case['expected'] = oracle(case, limit, grade)
    return {'tests': cases}


def generate(grade, rng, variant=None):
    if grade not in VARIANTS:
        raise ValueError('Семейство предназначено для Junior/Middle')
    variant = variant or rng.choice(VARIANTS[grade])
    if variant not in VARIANTS[grade]:
        raise ValueError('Неизвестный дефект уровня')
    limit = rng.choice([2, 4, 6])
    reference = reference_suite(grade, limit)
    if grade == 'Junior':
        starter = {'tests': [copy.deepcopy(reference['tests'][0])]}
        if variant == 'copied_expectations':
            copied = copy.deepcopy(reference['tests'][2])
            copied['expected'] = model(copied, limit, grade, 'accept_zero')
            starter['tests'].append(copied)
            defect = 'Ожидание для нулевого количества скопировано из ошибочного приложения: оно разрешает резерв вместо отказа.'
        else:
            defect = 'Один успешный тест не обнаруживает границы, неверный тип и изменение состояния при отказе.'
        requirements = ['Каждый тест: ровно stock, quantity, expected. expected: ровно status, stock, reserved (общее количество созданного резерва).',
                        'Каждый тест начинает новое состояние без резервов. Успех — 201, отказ по количеству — 422, нехватка — 409.']
        followup = 'Контракт разрешил null как «резерв не запрошен»: ответ 204 без изменений. Какие прежние ожидания изменятся, какие сохранятся и какой новый дефект нужно различить?'
    else:
        if variant == 'isolated_requests':
            starter = {'tests': [copy.deepcopy(reference['tests'][1]), copy.deepcopy(reference['tests'][1])]}
            defect = 'Два одинаковых теста каждый раз сбрасывают состояние: они не проверяют повтор, конфликт ключа, гонку и восстановление.'
        else:
            copied = copy.deepcopy(reference['tests'][7])
            copied['expected'] = model(copied, limit, grade, 'non_atomic')
            starter = {'tests': [copied]}
            defect = 'Состояние после сбоя скопировано из приложения с частичной фиксацией; тест принимает потерянное списание как правильное.'
        requirements = ['Каждый тест: ровно stock, requests, schedule, fault_at, expected. requests: 1…3 объекта с key (a/b/c) и quantity.',
                        'expected: ровно statuses (ответ каждого запроса), stock и reserved (объект key → количество). При отказе новое состояние не записывается.',
                        'Один тест — одно связанное состояние; разные тесты начинают с пустых резервов. Тот же key с тем же количеством даёт 200 без списания; с другим допустимым количеством — 409 без изменений.',
                        'schedule: serial или parallel. parallel требует два запроса и fault_at=none; это заданная модель гонки: оба читают начальный остаток, корректная фиксация проверяет актуальный остаток. При нехватке первым фиксируется первый запрос массива, статусы идут в порядке массива.',
                        'fault_at: none, after_debit или after_record. Сбой применяется только к первому запросу массива, если он проходит проверки и создаёт новый резерв, до общей фиксации; его ответ interrupted и обе записи откатываются. Если первый запрос отклонён, сбой не применяется. Остальные запросы исполняются без сбоя после рестарта с сохранённым состоянием.',
                        'Проверяйте связанную идемпотентность/конфликт, гонку разных ключей и оба места сбоя. Повтор HTTP-статуса без остатка и записей недостаточен.']
        followup = 'Срок хранения ключа стал 24 часа, а повтор возможен через два дня. Какие гарантии контракта нужно уточнить и какие отдельные тесты выявят второй резерв после истечения срока?'
    requirements += [
        f'Контракт: quantity — только целое от 1 до {limit}; boolean, дробь, строка и null дают 422 раньше проверки остатка/ключа. Нехватка даёт 409 без изменений; успех создаёт ровно один резерв и уменьшает остаток на его количество.',
        f'stock: целое 0…{2 * limit}; количества для проверки: числа −1…{limit + 1}, boolean, строки до 16 символов или null. Начальное состояние и входы выбираете вы, ожидания выводите из контракта.',
        'Нужны успешные 1 и лимит, отказы 0 и лимит + 1, boolean, дробь, строка, null и нехватка. Неправильное ожидание нельзя использовать как доказательство обнаруженного дефекта.',
        'В объекте tests — 1…20 тестов; все перечисленные поля обязательны. Сервер выполняет тесты на правильной модели и моделях дефектов валидации, нехватки и пропавшего резерва. Для Middle также проверяются повторное списание, потерянное обновление и частичная фиксация.',
        'Используйте компактный JSON при большом наборе: предел ответа 5 000 символов. Не добавляйте checks, названия мутаций или программный код.',
    ]
    condition = {'specialization': 'qa', 'grade': grade, 'variant': variant,
                 'max_quantity': limit, 'model_version': MODEL_VERSION}
    return {'id': 'item_' + secrets.token_hex(8), 'kind': 'work_sample', 'competency': 'work_sample',
            'competencies': ['test_design', 'api_contracts', 'risk_analysis'], 'stage': 'work_sample',
            'family': FAMILY, 'version': VERSION, 'rubric_version': RUBRIC_VERSION,
            'prompt': f'API резервирует ограниченный ресурс; лимит одного запроса {limit}. {defect} Исправьте набор входов и независимых ожиданий в JSON.',
            'starter': json.dumps(starter, ensure_ascii=False, indent=2), 'requirements': requirements,
            'followup': followup, 'review_rubric': rubric(grade), 'max_length': 5000,
            'private': {'family': 'reservation_test_suite', 'condition': condition,
                        'reference_answer': json.dumps(reference, ensure_ascii=False, separators=(',', ':')),
                        'check_labels': CHECKS + (MIDDLE_CHECKS if grade == 'Middle' else [])}}


def validate_suite(suite, limit, grade):
    if not isinstance(suite, dict) or set(suite) != {'tests'} or not isinstance(suite['tests'], list) or not 1 <= len(suite['tests']) <= 20:
        raise ValueError('Набор тестов')
    def integer(value, low, high):
        return type(value) is int and low <= value <= high
    for case in suite['tests']:
        fields = {'stock', 'quantity', 'expected'} if grade == 'Junior' else {'stock', 'requests', 'schedule', 'fault_at', 'expected'}
        if not isinstance(case, dict) or set(case) != fields or not integer(case['stock'], 0, 2 * limit):
            raise ValueError('Поля теста')
        requests = [{'quantity': case['quantity']}] if grade == 'Junior' else case['requests']
        if not isinstance(requests, list) or not 1 <= len(requests) <= 3:
            raise ValueError('Запросы')
        if grade == 'Middle':
            if case['schedule'] not in ('serial', 'parallel') or case['fault_at'] not in ('none', 'after_debit', 'after_record'):
                raise ValueError('Расписание или сбой')
            if case['schedule'] == 'parallel' and (len(requests) != 2 or case['fault_at'] != 'none'):
                raise ValueError('Ограничение модели гонки')
        for request in requests:
            if grade == 'Middle' and (not isinstance(request, dict) or set(request) != {'key', 'quantity'} or request['key'] not in ('a', 'b', 'c')):
                raise ValueError('Поля запроса')
            quantity = request['quantity']
            if quantity is None or type(quantity) is bool:
                continue
            if type(quantity) is str and len(quantity) <= 16:
                continue
            if type(quantity) not in (int, float) or not math.isfinite(quantity) or not -1 <= quantity <= limit + 1:
                raise ValueError('Количество тестового запроса')
        expected = case['expected']
        expected_fields = {'status', 'stock', 'reserved'} if grade == 'Junior' else {'statuses', 'stock', 'reserved'}
        if not isinstance(expected, dict) or set(expected) != expected_fields or not integer(expected['stock'], 0, 2 * limit):
            raise ValueError('Поля ожидания')
        if grade == 'Junior':
            if type(expected['status']) is not int or expected['status'] not in (201, 409, 422) or not integer(expected['reserved'], 0, limit):
                raise ValueError('Ожидание одиночного запроса')
        else:
            statuses, records = expected['statuses'], expected['reserved']
            if not isinstance(statuses, list) or len(statuses) != len(requests) or any(not (type(x) is int and x in (200, 201, 409, 422) or type(x) is str and x == 'interrupted') for x in statuses):
                raise ValueError('Статусы последовательности')
            if not isinstance(records, dict) or any(key not in ('a', 'b', 'c') or not integer(value, 1, limit) for key, value in records.items()):
                raise ValueError('Записи резервов')


def evaluate(suite, condition):
    grade, limit = condition['grade'], condition['max_quantity']
    validate_suite(suite, limit, grade)
    correct = [case for case in suite['tests'] if case['expected'] == oracle(case, limit, grade)]
    statuses, boundary, types = set(), set(), set()
    for case in correct:
        requests = [{'quantity': case['quantity']}] if grade == 'Junior' else case['requests']
        results = [case['expected']['status']] if grade == 'Junior' else case['expected']['statuses']
        for request, status in zip(requests, results):
            quantity = request['quantity']
            statuses.add(status)
            if type(quantity) is int and ((quantity in (1, limit) and status == 201) or (quantity in (0, limit + 1) and status == 422)):
                boundary.add(quantity)
            if status == 422 and type(quantity) in (bool, float, str, type(None)):
                types.add(type(quantity))
    def kills(mutant):
        return any(model(case, limit, grade, mutant) != case['expected'] for case in correct)
    checks = {'suite_oracle': len(correct) == len(suite['tests']),
              'suite_positive_negative': {201, 409, 422} <= statuses,
              'suite_boundary_types': {1, limit, 0, limit + 1} <= boundary and {bool, float, str, type(None)} <= types,
              'suite_mutations': all(kills(name) for name in MUTANTS)}
    if grade == 'Middle':
        checks.update(idempotency_detection=kills('duplicate_debit'),
                      race_detection=kills('lost_update'),
                      recovery_detection=all(any(case['fault_at'] == point and model(case, limit, grade, 'non_atomic') != case['expected'] for case in correct) for point in ('after_debit', 'after_record')))
    return checks
