"""Owned interview cases: different invariants, not just shuffled numbers."""
import json
from .assessment_catalog import GRADES

CASES = {
    'backend': [
        ('object_access', 0, 'api_contracts', 'Пользователь изменил ID объекта в запросе и увидел данные другой организации. Какая проверка устраняет причину?', [(True, 'На сервере проверять доступ текущей организации к каждому запрошенному объекту.'), (False, 'Скрыть ID в интерфейсе, оставив серверный доступ прежним.'), (False, 'Сделать ID длиннее и считать его секретным.')]),
        ('retry_budget', 1, 'api_contracts', 'Внешний API вернул 429 и Retry-After. Как уменьшить нагрузку, сохранив восстановимость операции?', [(True, 'Учесть Retry-After, ограничить число повторов и сохранить идемпотентность операции.'), (False, 'Немедленно повторять запрос без ограничения.'), (False, 'Считать любой 429 окончательным успехом.')]),
        ('pagination', 2, 'api_contracts', 'В каталоге многие записи имеют одинаковое время создания. Как избежать пропусков при последовательной cursor-пагинации?', [(True, 'Использовать стабильный составной порядок и курсор, например время создания и уникальный ID.'), (False, 'Использовать только время и случайный порядок равных записей.'), (False, 'Удалять записи с совпадающим временем.')]),
    ],
    'frontend': [
        ('http_error', 0, 'state', 'fetch успешно вернул объект Response со статусом 500. Как различить ошибку HTTP и сетевой сбой?', [(True, 'Проверить response.ok/status; сетевой сбой обрабатывать отдельно.'), (False, 'Считать, что fetch всегда бросает исключение на 500.'), (False, 'Считать получение Response подтверждением сохранения данных.')]),
        ('empty_body', 1, 'state', 'Сервер подтвердил удаление статусом 204 без тела. Что должна сделать клиентская обработка?', [(True, 'Обработать успех без обязательного разбора отсутствующего JSON.'), (False, 'Обязательно вызвать response.json() и считать ошибку разбора провалом удаления.'), (False, 'Повторять удаление до получения непустого тела.')]),
        ('request_identity', 2, 'state', 'При переключении фильтра отмена старого запроса может прийти слишком поздно. Как дополнительно защитить отображаемый результат?', [(True, 'Проверить, что идентификатор завершившегося запроса соответствует текущему запросу.'), (False, 'Применить любой завершившийся ответ, даже от прежнего фильтра.'), (False, 'Всегда выбирать ответ с большей длительностью.')]),
    ],
    'qa': [
        ('independent_oracle', 0, 'test_design', 'Тест вычисляет ожидаемую сумму той же функцией, которую проверяет. Что позволит заметить общий дефект?', [(True, 'Независимо вычислить эталон и добавить граничные и отрицательные примеры.'), (False, 'Вызвать ту же функцию ещё раз и сравнить два одинаковых результата.'), (False, 'Убрать утверждение о результате, оставив только отсутствие исключения.')]),
        ('permission_negative', 1, 'test_design', 'API изменения профиля корректно работает для владельца. Какая проверка выявит нарушение объектных прав?', [(True, 'Другой пользователь пробует изменить тот же объект; проверить отказ и неизменность данных.'), (False, 'Владелец повторяет успешный запрос с теми же данными.'), (False, 'Проверить только цвет кнопки сохранения.')]),
        ('failure_atomicity', 2, 'test_design', 'Операция списывает остаток и создаёт заказ. Как проверить атомарность при сбое между записями?', [(True, 'Воспроизвести сбой и проверить согласованный откат обеих записей, затем безопасный повтор.'), (False, 'Проверить только статус успешного сценария.'), (False, 'Разрешить потерю остатка, если интерфейс показал ошибку.')]),
    ],
}

def select_case(spec, grade, rng, legacy):
    choices = [('legacy_'+str(i), 'api_contracts', prompt, options) for i, (prompt, options) in enumerate(legacy)]
    choices += [(family, competency, prompt, options) for family, minimum, competency, prompt, options in CASES[spec] if GRADES.index(grade) >= minimum]
    family, competency, prompt, options = rng.choice(choices)
    return family, competency, prompt, list(options)

def diagnostic_case(spec, grade, rng):
    family = rng.choice(['object_authorization', 'optimistic_version', 'retry_keys']) if spec == 'backend' else rng.choice(['request_order', 'request_identity', 'http_status']) if spec == 'frontend' else rng.choice(['boundary_partitions', 'independent_cases', 'mutation_detection'])
    count = 5 + GRADES.index(grade)
    if family == 'object_authorization':
        rows = [[rng.choice(['A', 'B']), rng.choice(['read', 'write'])] for _ in range(count)]
        prompt = f'Пользователь организации A имеет только право чтения. Запросы [организация объекта, действие]: {json.dumps(rows)}. Сервер проверяет оба условия для каждого объекта. Сколько запросов разрешить?'
        expected = sum(org == 'A' and action == 'read' for org, action in rows); competency = 'api_contracts'
    elif family == 'optimistic_version':
        version = rng.randint(2, 8); received = [rng.randint(version-1, version+2) for _ in range(count)]; current = version; expected = 0
        for value in received:
            if value == current: expected += 1; current += 1
        prompt = f'Начальная версия записи {version}. Последовательно приходят изменения с версиями {received}. Изменение применяется только при равенстве текущей версии, затем версия увеличивается на 1. Сколько изменений применится?'; competency = 'api_contracts'
    elif family == 'retry_keys':
        keys = [rng.choice(['A', 'B', 'C', 'D']) for _ in range(count)]
        prompt = f'Создание операции хранит ключ идемпотентности атомарно. Запросы с одинаковым ключом имеют одинаковое тело. Ключи: {keys}. Сколько операций создать после всех повторов?'; expected = len(set(keys)); competency = 'api_contracts'
    elif family in {'request_order', 'request_identity'}:
        durations = [rng.randint(10, 140) for _ in range(count)]; finishes = [i*5+duration for i, duration in enumerate(durations)]
        if family == 'request_identity':
            expected = count; policy = 'Ответ применяется только если его номер равен номеру последнего запущенного запроса.'
        else:
            expected = max(range(count), key=lambda i: (finishes[i], i))+1; policy = 'Любой завершившийся ответ заменяет состояние; при одинаковом времени первым применяется меньший номер.'
        prompt = f'Запросы с номерами 1–{count} запускаются через каждые 5 мс, первый в момент 0. Их длительности: {durations} мс. {policy} Все завершились успешно. Номер какого запроса останется в состоянии?'; competency = 'state'
    elif family == 'http_status':
        values = [rng.choice([200, 204, 400, 404, 429, 500]) for _ in range(count)]
        prompt = f'Сервер вернул статусы {values}. Успешными считаются все статусы от 200 до 299 включительно. Сколько ответов интерфейс должен показать как ошибку HTTP?'; expected = sum(not 200 <= v <= 299 for v in values); competency = 'state'
    elif family == 'boundary_partitions':
        low = rng.randint(10, 40); high = low+rng.randint(5, 15); values = [low-1, low, high, high+1]+[rng.randint(low-3, high+3) for _ in range(count)]
        prompt = f'Поле принимает целое от {low} до {high} включительно. Проверки: {values}. Сколько отказов должен дать независимый эталон?'; expected = sum(not low <= x <= high for x in values); competency = 'test_design'
    else:
        values = [rng.randint(1, 30) for _ in range(count)]; threshold = rng.randint(5, 25)
        if family == 'mutation_detection':
            values[0]=threshold;values[1]=threshold-1;values[2]=threshold+1
            rng.shuffle(values)
            expected = sum(v == threshold for v in values); prompt = f'Спецификация принимает x ≥ {threshold}, дефектная реализация принимает x > {threshold}. Проверяем {values}. Сколько тестов отличит дефект от спецификации?'
        else:
            expected = sum(v >= threshold for v in values); prompt = f'Спецификация принимает x ≥ {threshold}. Набор {values}. Оракул написан независимо от реализации. Сколько тестов ожидает успешный результат?'
        competency = 'test_design'
    return family, competency, prompt, expected
