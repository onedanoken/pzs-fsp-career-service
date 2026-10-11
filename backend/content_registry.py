"""Pinned metadata and owned grade blueprints, with no runtime remote fetch."""
import json
from pathlib import Path

def registry():
    root=Path(__file__).resolve().parents[1]
    # Immutable image assets must not be masked by the persistent /app/data volume.
    path=root/'resources/assessment_content_v2.json'
    if not path.exists():path=root/'data/assessment_content_v2.json'
    if not path.exists() or path.stat().st_size>256*1024:raise RuntimeError('Content registry unavailable')
    value=json.loads(path.read_text(encoding='utf-8'))
    if value.get('schema')!='fsp-assessment-content-v2':raise RuntimeError('Content version mismatch')
    for source in value['sources']:
        if not source['url'].startswith('https://') or len(source['id'])>100:raise RuntimeError('Invalid source')
    return value

def sources():return registry()['sources']
