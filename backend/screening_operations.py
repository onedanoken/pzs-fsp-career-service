"""Authored distinct core operations. A public finite bank is not leak-proof."""
import json
import secrets
from pathlib import Path
from .screening_distractors import DISTRACTORS

VERSION='core-operations-2026-v2'


def operations(spec, grade, rng):
    root=Path(__file__).resolve().parents[1]
    path=root/'resources/screening_core_operations.json'
    if not path.exists():path=root/'data/screening_core_operations.json'
    content=json.loads(path.read_text(encoding='utf-8'))
    result=[]
    for competency, rows in content['content'].get(spec,{}).items():
        for family,prompt,correct,incorrect in rows:
            if spec=='backend' and family=='join':
                prompt='Нужны все клиенты, включая клиентов без заказов, и только связанные заказы статуса paid. Где фильтровать статус, чтобы LEFT JOIN не отбрасывал клиентов?'
            options=[(True,correct),(False,incorrect)]+[(False,value) for value in DISTRACTORS[(spec,family)]]
            assert len({text for _,text in options})==4
            rng.shuffle(options)
            choices=[]; expected=None
            for valid,text in options:
                identity=secrets.token_hex(5)
                choices.append({'id':identity,'text':text})
                if valid:expected=identity
            result.append({'id':'item_'+secrets.token_hex(8),'kind':'choice','competency':competency,
                           'family':spec+':'+competency+':'+family,'version':VERSION,
                           'prompt':prompt,'choices':choices,'source_ids':[],
                           'private':{'expected':expected,'rationale':correct},'difficulty_scope':'Foundational invariant shared across grades; grade decision and practical artifact carry level-specific constraints.'})
    return result
