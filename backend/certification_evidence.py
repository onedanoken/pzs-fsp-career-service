"""Label historical scores; disclose stage results only through passport permissions."""
import json
from fastapi import HTTPException


def summary(cert):
    cert=dict(cert)
    proof = json.loads(cert['payload']) if isinstance(cert['payload'], str) else cert['payload']
    path_data = proof.get('assessment_path')
    path = path_data.get('version') if isinstance(path_data, dict) else None
    if not cert.get('session_id') and (proof.get('demo') or cert['status'] == 'demo'):
        semantics, label = 'synthetic_example', 'Синтетический пример'
    elif not cert.get('session_id'):
        semantics, label = 'unknown', 'Баллы без проверяемой попытки'
    elif path in {'work_sample_v1','work_sample_v2_auto','work_sample_v3_balanced'}:
        semantics, label = 'screening_score', 'Короткий тест'
    elif not path and 'assessment_path' not in proof:
        semantics, label = 'legacy_objective_score', 'Объективные задания прежней процедуры'
    else:
        semantics, label = 'unknown', 'Баллы процедуры с неизвестной семантикой'
    return {'score_semantics': semantics, 'score_label': label,
            'session_id': cert.get('session_id'), 'path_version': path or ('legacy' if semantics == 'legacy_objective_score' else None),
            'blueprint_version': proof.get('blueprint_version'), 'policy_version': proof.get('policy_version'),
            'calibration': proof.get('calibration', 'pilot_unvalidated'), 'demo': bool(proof.get('demo'))}


def evidence(conn, cert, user=None):
    cert=dict(cert)
    result = summary(cert)
    result.update(stages=None, passport=None, detail_access='not_applicable')
    if result['score_semantics'] != 'screening_score':
        return result
    result['detail_access'] = 'unavailable'
    # Need/invitation snapshots use no viewer and never store private stage scores.
    if user is None:
        return result
    row = conn.execute('SELECT * FROM assessment_passports WHERE session_id=? AND candidate_id=? ORDER BY revision DESC LIMIT 1',
                       (cert.get('session_id'), cert['candidate_id'])).fetchone()
    if not row:
        return result
    from .skill_passports import view
    try:
        passport = view(conn, row, user)
    except HTTPException as error:
        if error.status_code != 404:
            raise
        return result
    snapshot = passport['snapshot']
    result.update(detail_access='authorized', stages={
        'screening': snapshot['screening'], 'work_sample': snapshot['work_sample'], 'review': snapshot['review'], 'transfer': snapshot.get('transfer')},
        passport={'id': passport['id'], 'revision': passport['revision'], 'status': snapshot['status'],
                  'issued_at': snapshot['issued_at'], 'category_confirmed_by_this_revision': passport['current']['category_confirmed_by_this_revision']})
    # Versions belong to this immutable passport, including a re-opened appeal.
    result.update(blueprint_version=snapshot['blueprint_version'], path_version=snapshot['path_version'],
                  policy_version=snapshot['policy'].get('version'))
    return result
