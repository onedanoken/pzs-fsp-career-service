"""Explain observed task scores without revealing answers or claiming calibration."""
from collections import defaultdict
from .assessment_catalog import POLICY

LABELS = {
    'work_sample':'Рабочий кейс',
    'data_processing': 'Обработка данных', 'sql': 'SQL и работа с выборками',
    'state': 'Асинхронное состояние', 'debugging': 'Поиск и воспроизведение ошибки',
    'api_contracts': 'Контракты API', 'test_design': 'Проектирование проверок',
    'tradeoffs': 'Аргументация и компромиссы', 'accessibility': 'Доступность интерфейса',
    'risk_analysis': 'Анализ рисков', 'capacity': 'Ресурсный бюджет',
    'deployment': 'Развёртывание', 'pipeline': 'Процесс доставки', 'observability': 'Наблюдаемость',
    'metrics': 'Метрики модели', 'leakage': 'Утечка данных', 'splitting': 'Разделение выборок',
    'validation': 'Валидация', 'authorization': 'Права на объекты', 'network': 'Сетевые ограничения',
    'least_privilege': 'Минимальные привилегии', 'incident_response': 'Реагирование на инцидент',
}
GUIDANCE = {
    'work_sample':'Сопоставьте артефакт с каждым инвариантом и отрицательным сценарием. Объясните границы модельной проверки в отдельном разборе.',
    'sql': 'Потренируйте фильтрацию, группировку и проверки на новых наборах данных.',
    'state': 'Разберите порядок завершения запросов, отмену и защиту актуального состояния.',
    'api_contracts': 'Проверьте повторы, ошибки, права на объект и неизменяемые условия запроса.',
    'test_design': 'Добавьте независимый эталон, граничные случаи и отрицательные примеры.',
    'debugging': 'Сформулируйте воспроизводимый пример и проверяемый инвариант.',
    'deployment': 'Различайте бюджет rollout и измеренную доступность. Проверьте совместимость версий и сценарий сохранения данных.',
    'capacity': 'Сложите requests всех обычных контейнеров; проверьте каждый ограничивающий ресурс и единицы измерения.',
    'pipeline': 'Нарисуйте зависимости задач. Параллельные ветви не складываются; следующий этап ждёт свои зависимости.',
    'observability': 'Разделите работоспособность процесса, готовность к трафику и корректность бизнес-ответа.',
    'metrics': 'Назовите положительный класс, выпишите FP/FN и знаменатель метрики. Проверьте указанное правило округления.',
    'splitting': 'Проверьте, что группы не пересекаются, а временной порядок и исключённый gap соблюдены.',
    'leakage': 'Для каждого признака укажите момент его доступности; preprocessing обучайте без итогового контроля.',
    'validation': 'Сверьте ошибки с независимым эталоном, стоимостью FP/FN и назначением контрольной выборки.',
    'authorization': 'Проверяйте объект, организацию, действие и актуальность разрешения одновременно; вход не даёт доступ ко всем объектам.',
    'network': 'Явно разделите адресный диапазон и ограничения порта; неизвестный случай не должен получать разрешение.',
    'least_privilege': 'Проверьте пересечение роли, scope и состояния аккаунта; достаточность одного условия не заменяет остальные.',
    'incident_response': 'Свяжите ограничение ущерба с проверяемым прекращением доступа и сохранением защищённых журналов.',
}

def feedback(result):
    groups = defaultdict(list)
    expert = (result or {}).get('expert_review') or {}
    for item in (result or {}).get('items', []):
        groups[item['competency']].append(item)
    rows = []
    for name, values in groups.items():
        scores = [v['score'] for v in values if v.get('score') is not None]
        reviewed = any(v.get('item_id') == expert.get('item_id') and v.get('score') is None for v in values) and expert.get('score') is not None
        score = round(sum(scores)/len(scores), 2) if scores else None
        pending = any(v.get('score') is None and not (reviewed and v.get('item_id') == expert.get('item_id')) for v in values)
        rows.append({
            'competency': name, 'label': LABELS.get(name, name), 'score': score,
            'observations': len(scores), 'pending_expert': pending,
            'expert_score': expert['score'] if reviewed else None,
            'status': 'expert_reviewed' if score is None and reviewed else 'awaiting_expert' if score is None else 'practice_recommended' if score < POLICY['pass_score'] else 'observed_in_session',
            'next_step': 'Изучите обоснование отдельного экспертного разбора.' if score is None and reviewed else 'Дождитесь отдельного разбора эксперта.' if score is None else GUIDANCE.get(name, 'Повторите практический случай с другими условиями и проверьте результат независимо.') if score < POLICY['pass_score'] else 'Проверьте перенос навыка на новый практический случай.',
        })
    return rows
