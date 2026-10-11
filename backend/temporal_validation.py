"""Frozen row-level preparation and temporal/cost validation without training ML."""
import copy
import json
import math
import secrets

FAMILY = 'ml:temporal_validation'
VERSION = 'temporal-validation-v1'
MODEL_VERSION = 'temporal-validation-model-v1'
RUBRIC_VERSION = 'temporal-validation-rubric-v1'
CHECKS = ['ml_partitions', 'ml_fit_scope', 'ml_fill_statistic', 'ml_feature_availability']
MIDDLE_CHECKS = ['ml_label_cutoff', 'ml_threshold_scope', 'ml_validation_cost']
VARIANTS = {'Junior': ['fit_holdout', 'future_feature'],
            'Middle': ['late_label', 'test_threshold']}
FEATURES = {'amount': 'prediction', 'channel': 'prediction', 'resolved_amount': 'after_outcome'}


def rubric(grade):
    middle = grade == 'Middle'
    return [
        {'id': 'correctness', 'label': 'Корректность решения', 'anchors': [
            'Подготовка использует holdout или признак из будущего.' + (' Метка ещё не доступна или порог настраивается по test.' if middle else ''),
            'Исправлен один вид утечки, остальные условия не обоснованы.',
            'Разбиение и статистика заполнения независимы от test; признаки доступны при прогнозе.' + (' Обоснованы cutoff, доступность метки, validation и стоимость ошибок.' if middle else ''),
            'Инварианты подготовки и валидации обоснованы также при изменённом условии.'
        ]},
        {'id': 'verification', 'label': 'Проверки и воспроизводимость', 'anchors': [
            'Нет строк, вычислений и ожидаемого результата.',
            'Проверена лишь итоговая метрика без источников её вычисления.',
            'Приведены train/test, строки fit и независимое среднее; изменение test не меняет подготовку.' + (' Проверены поздняя метка, FP/FN и все допустимые пороги.' if middle else ''),
            'Есть независимые отрицательные сценарии и расчёты нового условия, а не повтор эталона.'
        ]},
        {'id': 'reasoning', 'label': 'Обоснование решения', 'anchors': [
            'Высокая метрика оправдывает утечку.',
            'Сказано «разделить данные» без связи с временем получения информации.',
            'Объяснено, почему fit и признаки не используют будущее.' + (' Разделены время прогноза/метки и цена FP/FN; test не выбирает порог.' if middle else ''),
            'Альтернативы сопоставлены по допустимой информации, цели прогноза и последствиям ошибок.'
        ]},
        {'id': 'limits', 'label': 'Границы и компромиссы', 'anchors': [
            'Таблица объявлена доказательством качества обученной промышленной модели.',
            'Назван риск без отдельной проверки.',
            'Указаны непроверенные реальное обучение, drift, причинность и качество меток.' + (' Отмечена малая фиксированная validation, а не статистическая калибровка.' if middle else ''),
            'Для нового условия предложены измерения, риск и основания пересмотра протокола.'
        ]},
    ]


def split_oracle(condition):
    rows = condition['rows']
    if condition['grade'] == 'Junior':
        test = set(condition['holdout'])
        return {'train': [x['id'] for x in rows if x['id'] not in test], 'test': sorted(test)}
    cutoff, end = condition['cutoff'], condition['validation_end']
    split = {name: [] for name in ['train', 'validation', 'test', 'purge']}
    for row in rows:
        if row['prediction_at'] < cutoff and row['label_at'] <= cutoff:
            name = 'train'
        elif cutoff <= row['prediction_at'] < end and row['label_at'] <= end:
            name = 'validation'
        elif row['prediction_at'] >= end:
            name = 'test'
        else:
            name = 'purge'
        split[name].append(row['id'])
    return split


def preparation_oracle(condition, train):
    values = [x['amount'] for x in condition['rows'] if x['id'] in train and x['amount'] is not None]
    mean = sum(values) / len(values)
    return {'mean': mean, 'filled': {x['id']: mean if x['amount'] is None else x['amount'] for x in condition['rows']}}


def execute_preparation(plan, rows):
    """Fit the submitted row selection, then apply its learned statistic."""
    sample = []
    for row in rows:
        if row['id'] in plan['fit_rows'] and row['amount'] is not None:
            sample.append(row['amount'])
    if not sample:
        return {'mean': None, 'filled': {}}
    learned = sum(sample) / len(sample)
    filled = {}
    for row in rows:
        filled[row['id']] = learned if row['amount'] is None else row['amount']
    return {'mean': learned, 'filled': filled}


def classification_cost(rows, identities, threshold, fp_cost, fn_cost):
    fp = fn = 0
    for row in rows:
        if row['id'] not in identities:
            continue
        predicted = row['probability'] >= threshold
        fp += predicted and row['target'] == 0
        fn += not predicted and row['target'] == 1
    return {'fp': fp, 'fn': fn, 'cost': fp * fp_cost + fn * fn_cost}


def threshold_oracle(condition, validation):
    # Count mistakes directly from the frozen contract; do not use submitted
    # threshold rows, submitted cost, the model helper or reference_answer.
    candidates = []
    for threshold in condition['thresholds']:
        penalty = sum(condition['fp_cost'] if row['target'] == 0 else condition['fn_cost']
                      for row in condition['rows'] if row['id'] in validation and (row['probability'] >= threshold) != bool(row['target']))
        candidates.append((penalty, -threshold))
    cost, negative = min(candidates)
    return {'threshold': -negative, 'cost': cost}


def generate(grade, rng, variant=None):
    if grade not in VARIANTS:
        raise ValueError('Семейство предназначено для Junior/Middle')
    variant = variant or rng.choice(VARIANTS[grade])
    if variant not in VARIANTS[grade]:
        raise ValueError('Неизвестный дефект уровня')
    scale = rng.choice([2, 4, 6])
    amounts = [scale, scale + 4, None, scale + 8, scale + 12, None, scale + 20, 1000 + scale]
    rows = [{'id': f'r{i + 1}', 'amount': amount, 'channel': 'web' if i % 2 == 0 else 'app'} for i, amount in enumerate(amounts)]
    condition = {'specialization': 'ml', 'grade': grade, 'variant': variant,
                 'model_version': MODEL_VERSION, 'rows': rows, 'feature_timing': dict(FEATURES)}
    if grade == 'Junior':
        condition['holdout'] = ['r7', 'r8']
        requirements = ['train и test: массивы уникальных известных ID. Test заранее зафиксирован: r7/r8; остальные строки — train. Разбиение сделано до fit.']
        followup = 'Появились несколько записей одного клиента. Как изменить разбиение и проверки, если нужно оценить качество для новых клиентов?'
    else:
        cutoff = rng.choice([40, 70])
        fp_cost, fn_cost = rng.choice([(1, 9), (9, 1), (1, 1)])
        condition.update(cutoff=cutoff, validation_end=cutoff + 20, fp_cost=fp_cost, fn_cost=fn_cost, thresholds=[0.3, 0.5, 0.8])
        times = [-30, -20, -10, -1, 5, 15, 25, 35]
        for row, offset, target, probability in zip(rows, times, [0, 1, 0, 1, 1, 0, 1, 0], [0.1, 0.4, 0.6, 0.8, 0.4, 0.7, 0.9, 0.6]):
            row.update(prediction_at=cutoff + offset, label_at=cutoff + offset + 2, target=target, probability=probability)
        rows[3]['label_at'] = cutoff + 10
        requirements = [
            f'train/validation/test/purge: уникальные ID без пересечений, вместе все строки. Модель обучается по снимку {cutoff}: train имеет prediction_at < cutoff и label_at <= cutoff.',
            f'validation: cutoff <= prediction_at < {cutoff + 20}, label_at <= validation_end. test: prediction_at >= validation_end. Остальное — purge; поздняя метка не переносит старый прогноз в validation.',
            f'threshold_rows: только все validation, не test. threshold: один из 0.3/0.5/0.8. Класс 1 при probability >= threshold. Цена FP={fp_cost}, FN={fn_cost}; минимизируйте FP×цена_FP + FN×цена_FN на validation. При равной цене выберите больший порог.',
            'expected_cost: целое значение этой стоимости на validation. probability — замороженные оценки уже заданной модели; новый классификатор здесь не обучается.',
            f'Финальный test оценивается после дня {cutoff + 40}, когда его метки уже доступны; он не используется для fit или выбора порога.',
        ]
        followup = 'Изменились задержка получения метки и цена пропущенного события. Какие снимки, повторную валидацию, мониторинг и условия пересмотра порога вы зададите?'
    partitions = split_oracle(condition)
    prepared = preparation_oracle(condition, partitions['train'])
    reference = dict(partitions, fit_rows=partitions['train'][:], features=['amount', 'channel'], fill_value=prepared['mean'])
    if grade == 'Middle':
        best = threshold_oracle(condition, partitions['validation'])
        reference.update(threshold_rows=partitions['validation'][:], threshold=best['threshold'], expected_cost=best['cost'])
    starter = copy.deepcopy(reference)
    if variant == 'fit_holdout':
        starter['fit_rows'] = [x['id'] for x in rows]
        starter['fill_value'] = execute_preparation(starter, rows)['mean']
        defect = 'Заполнение пропусков обучено на всех строках, включая финальный holdout; изменение test меняет подготовку.'
    elif variant == 'future_feature':
        starter['features'].append('resolved_amount')
        defect = 'В признаки попал resolved_amount, который появляется только после исхода; при прогнозе его нет.'
    elif variant == 'late_label':
        starter['train'].append('r4'); starter['purge'].remove('r4')
        starter['fit_rows'] = starter['train'][:]
        starter['fill_value'] = execute_preparation(starter, rows)['mean']
        defect = 'В train включён старый прогноз r4, но его метка появилась только после снимка обучения.'
    else:
        starter['threshold_rows'] = partitions['test'][:]
        test_best = threshold_oracle(condition, partitions['test'])
        starter.update(threshold=test_best['threshold'], expected_cost=test_best['cost'])
        defect = 'Порог выбран по финальному test, и его нулевая стоимость объявлена независимой оценкой качества.'
    requirements += [
        'fit_rows: уникальные ID только из правильного train, включая все его наблюдаемые amount; строки train с пропуском можно включать или опускать. Fit использует только наблюдаемые amount, не метки/holdout.',
        'fill_value: конечное среднее наблюдаемых amount правильного train. Сервер отдельно вычисляет фактически обученную статистику по fit_rows и заполненные строки; подмена заявленного числа не скрывает утечку.',
        'features: непустой набор из amount/channel/resolved_amount, с обязательным amount. amount и channel известны при прогнозе; resolved_amount известен после исхода и недопустим.',
        'Формат — JSON без checks, операторов и лишних полей; до 5 000 символов. Порядок ID/признаков несущественен. Источники статистики и оценок сохраняются с попыткой.',
    ]
    columns = ['id', 'amount', 'channel'] + (['prediction_at', 'label_at', 'target', 'probability'] if grade == 'Middle' else [])
    return {'id': 'item_' + secrets.token_hex(8), 'kind': 'work_sample', 'competency': 'work_sample',
            'competencies': ['leakage', 'splitting', 'validation'], 'stage': 'work_sample',
            'family': FAMILY, 'version': VERSION, 'rubric_version': RUBRIC_VERSION,
            'prompt': 'Исправьте подготовку данных и протокол оценки. ' + defect + ' Строки ниже синтетические; единицы времени — дни.',
            'example_table': {'columns': columns, 'rows': [[row[column] for column in columns] for row in rows]},
            'starter': json.dumps(starter, ensure_ascii=False, indent=2), 'requirements': requirements,
            'followup': followup, 'review_rubric': rubric(grade), 'max_length': 5000,
            'private': {'family': 'temporal_validation', 'condition': condition,
                        'reference_answer': json.dumps(reference, ensure_ascii=False, indent=2),
                        'check_labels': CHECKS + (MIDDLE_CHECKS if grade == 'Middle' else [])}}


def validate_plan(plan, condition):
    fields = {'train', 'test', 'fit_rows', 'features', 'fill_value'}
    if condition['grade'] == 'Middle':
        fields |= {'validation', 'purge', 'threshold_rows', 'threshold', 'expected_cost'}
    if not isinstance(plan, dict) or set(plan) != fields:
        raise ValueError('Поля подготовки')
    ids = {row['id'] for row in condition['rows']}
    arrays = ['train', 'test', 'fit_rows'] + (['validation', 'purge', 'threshold_rows'] if condition['grade'] == 'Middle' else [])
    for field in arrays + ['features']:
        values = plan[field]
        allowed = FEATURES if field == 'features' else ids
        if not isinstance(values, list) or len(values) > len(allowed) or any(not isinstance(x, str) or x not in allowed for x in values) or len(set(values)) != len(values):
            raise ValueError('ID или признаки')
    if type(plan['fill_value']) not in (int, float) or not math.isfinite(plan['fill_value']) or not 0 <= plan['fill_value'] <= 5000:
        raise ValueError('Статистика заполнения')
    if condition['grade'] == 'Middle':
        if type(plan['threshold']) not in (int, float) or not math.isfinite(plan['threshold']) or plan['threshold'] not in condition['thresholds']:
            raise ValueError('Допустимый порог')
        if type(plan['expected_cost']) is not int or not 0 <= plan['expected_cost'] <= 100:
            raise ValueError('Цена ошибок')


def evaluate(plan, condition):
    validate_plan(plan, condition)
    partitions = split_oracle(condition)
    train = set(partitions['train'])
    observed = {row['id'] for row in condition['rows'] if row['id'] in train and row['amount'] is not None}
    learned, expected = execute_preparation(plan, condition['rows']), preparation_oracle(condition, train)
    partition_ok = all(set(plan[name]) == set(ids) for name, ids in partitions.items())
    checks = {'ml_partitions': partition_ok,
              'ml_fit_scope': observed <= set(plan['fit_rows']) <= train,
              'ml_fill_statistic': learned == expected and plan['fill_value'] == expected['mean'],
              'ml_feature_availability': bool(plan['features']) and 'amount' in plan['features'] and all(condition['feature_timing'][x] == 'prediction' for x in plan['features'])}
    if condition['grade'] == 'Middle':
        best = threshold_oracle(condition, partitions['validation'])
        cost = classification_cost(condition['rows'], partitions['validation'], plan['threshold'], condition['fp_cost'], condition['fn_cost'])
        checks.update(ml_label_cutoff=bool(plan['train']) and all(row['prediction_at'] < condition['cutoff'] and row['label_at'] <= condition['cutoff'] for row in condition['rows'] if row['id'] in plan['train']),
                      ml_threshold_scope=set(plan['threshold_rows']) == set(partitions['validation']),
                      ml_validation_cost=plan['threshold'] == best['threshold'] and cost['cost'] == best['cost'] and plan['expected_cost'] == cost['cost'])
    return checks
