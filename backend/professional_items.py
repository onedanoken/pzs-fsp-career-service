"""Owned professional case families with deterministic, independent keys.

The numeric and contract oracles validate answers, not professional grade
thresholds. Grade difficulty and open-answer rubrics still require human pilots.
"""

LEVEL_CRITERIA={
 'Intern':['Объясняет базовые термины','Решает ограниченную задачу по явным условиям'],
 'Junior':['Самостоятельно обрабатывает типичный сбой','Проверяет граничный случай'],
 'Middle':['Сохраняет инварианты при конкуренции и сбоях','Обосновывает измеримый критерий качества'],
 'Senior':['Описывает системные компромиссы и стоимость отказа','Проектирует наблюдаемость, эволюцию и проверку рисков'],
}

def tasks(spec,grade,rng):
    from .professional_variants import build
    return build(spec,grade,rng,LEVEL_CRITERIA[grade])
