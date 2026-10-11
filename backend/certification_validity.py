"""Current category eligibility shared by directory, ranking and passport views.

Age affects ranking; it does not silently revoke a historically issued category.
Explicit expiry/status/active flags do. The original attempt is never rewritten.
"""
import json
import math
from datetime import datetime, timezone
from .assessment_catalog import GRADES, SPECIALIZATIONS

def usable_certificate(record, reference_time=None):
    row = dict(record)
    if not row.get('active', True) or row.get('status') not in {'provisional', 'confirmed', 'certified', 'demo'}:
        return False
    if row.get('specialization') not in SPECIALIZATIONS or row.get('grade') not in GRADES:
        return False
    score = row.get('score')
    if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 100:
        return False
    try:
        proof = json.loads(row['payload']) if isinstance(row.get('payload'), str) else row.get('payload')
        if not isinstance(proof, dict):
            return False
        skills = proof.get('tested_skills', [])
        if not isinstance(skills, list) or len(skills) > 100 or any(not isinstance(s,str) or len(s)>100 for s in skills):
            return False
        expiry = proof.get('expires_at')
        if expiry is not None:
            date = datetime.fromisoformat(expiry.replace('Z', '+00:00'))
            if date.tzinfo is None:
                return False
            current = reference_time if reference_time is not None else datetime.now(timezone.utc).timestamp()
            if date.timestamp() <= current:
                return False
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return False
    return True
