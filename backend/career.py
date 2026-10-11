"""Role-specific next steps and accountable invitations; private by default."""
import json
import time
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from .auth import session_user, require_role, require_org_role, require_verified
from .db import connection, dumps, now, audit, bump
from .schemas import StrictModel
from .contact_policy import hiring_activity, contact_state
from .competency_feedback import feedback
from .certification_evidence import evidence as assessment_evidence

router = APIRouter(prefix='/api', tags=['Career progress and contact commitments'])

@router.get('/career/overview')
def overview(user=Depends(session_user)):
    require_role(user, 'candidate', 'employer')
    with connection() as conn:
        if user['role'] == 'candidate':
            from .privacy import consent, eligible
            from .domain import candidate_from_row
            row = conn.execute('SELECT * FROM candidates WHERE id=?', (user['candidate_id'],)).fetchone()
            candidate = candidate_from_row(row, conn)
            certifications = []
            for cert in conn.execute('SELECT * FROM grade_certifications WHERE candidate_id=? AND active=1 ORDER BY created_at DESC', (row['id'],)):
                from .certification_validity import usable_certificate
                if not usable_certificate(cert):continue
                proof = json.loads(cert['payload'])
                certifications.append({k: cert[k] for k in ['id', 'specialization', 'grade', 'score', 'status', 'created_at']} | {'demo': bool(proof.get('demo')), 'calibration': proof.get('calibration', 'pilot_unvalidated'), 'competencies': proof.get('competencies', {}), 'assessment': assessment_evidence(conn,cert,user)})
            steps = [
                {'label': 'Защитить аккаунт', 'done': bool(user.get('auth_kind')=='username' or user['email_verified']), 'route': 'profile'},
                {'label': 'Пройти проверку уровня', 'done': bool(certifications), 'route': 'assessment'},
                {'label': 'Завершить выбор уровня', 'done': bool(candidate.get('assessment_onboarding_done')), 'route': 'assessment'},
                {'label': 'Разрешить обработку и публикацию', 'done': consent(conn, row['id'], 'processing') and consent(conn, row['id'], 'publication'), 'route': 'profile'},
                {'label': 'Включить видимость профиля и поиск работы', 'done': bool(candidate.get('visible') and candidate.get('looking', True)), 'route': 'profile'},
            ]
            latest = conn.execute("SELECT id,specialization,grade,payload,completed_at FROM assessment_sessions WHERE candidate_id=? AND status='completed' ORDER BY completed_at DESC", (row['id'],)).fetchone()
            last = {'id': latest['id'], 'specialization': latest['specialization'], 'grade': latest['grade'], 'completed_at': latest['completed_at'], 'competencies': feedback(json.loads(latest['payload']).get('result'))} if latest else None
            invitations = list(conn.execute('SELECT * FROM invitations WHERE candidate_id=?', (row['id'],)))
            items = [contact_state(r) for r in invitations]
            pending_invitations = sum(r['status'] in {'sent', 'viewed', 'clarification'} for r in invitations)
            contact_actions = sum(x['status'] in {'waiting', 'overdue', 'employer_reported', 'disputed'} for x in items)
            ready_action = {'label': 'Посмотреть приглашения', 'route': 'offers'} if pending_invitations or contact_actions else {'label': 'Потренировать навыки', 'route': 'assessment'}
            action = next(({'label': s['label'], 'route': s['route']} for s in steps if not s['done']), ready_action)
            return {'role': 'candidate', 'steps': steps, 'next_action': action, 'visible_in_matching': eligible(conn, candidate), 'certifications': certifications, 'latest_assessment': last, 'pending_invitations': pending_invitations, 'contacts_awaiting_action': contact_actions, 'scope': 'Оценки относятся к заданиям сессии; пилотная шкала не подтверждает владение всем стеком.'}
        from .trust import company
        record = company(conn, user['org_id'])
        needs = [dict(r, activity=hiring_activity(r)) for r in conn.execute('SELECT * FROM employer_needs WHERE org_id=?', (user['org_id'],))]
        invitations = list(conn.execute('SELECT * FROM invitations WHERE org_id=?', (user['org_id'],)))
        states = [contact_state(r) for r in invitations]
        counts = {'active_needs': sum(r['activity']['status'] == 'current' for r in needs), 'needs_reconfirmation': sum(r['activity']['status'] == 'reconfirmation_required' for r in needs), 'accepted_invitations': sum(r['status'] == 'accepted' for r in invitations), 'overdue_contacts': sum(s['status'] == 'overdue' for s in states), 'contact_disputes': sum(s['status'] == 'disputed' for s in states), 'candidate_confirmed_contacts': sum(s['status'] == 'confirmed' for s in states)}
        action = {'label': 'Проверить компанию', 'route': 'company'} if record['verification'] not in {'verified', 'demo_verified'} else {'label': 'Ответить кандидатам', 'route': 'offers'} if counts['overdue_contacts'] or counts['contact_disputes'] else {'label': 'Подтвердить актуальность найма', 'route': 'candidates'} if counts['needs_reconfirmation'] else {'label': 'Подобрать команду', 'route': 'candidates'}
        return {'role': 'employer', 'company_verification': record['verification'], 'counts': counts, 'next_action': action, 'scope': 'Счётчики показывают события платформы; подтверждение контакта не означает трудоустройства.'}

class HiringConfirmation(StrictModel):
    version: int = Field(ge=1)
    active_hiring: bool
    salary_approved: bool

@router.post('/needs/{identity}/confirm-hiring')
def confirm_hiring(identity: str, body: HiringConfirmation, user=Depends(session_user)):
    require_org_role(user, 'owner', 'recruiter'); require_verified(user)
    if not body.active_hiring or not body.salary_approved:
        raise HTTPException(422, 'Подтвердите активный запрос на подбор и согласованный зарплатный диапазон; иначе закройте запрос.')
    from .recruiting import owned_need, need_view
    from .trust import company
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        need = owned_need(conn, identity, user)
        if need['version'] != body.version:
            raise HTTPException(409, 'Запрос изменился; обновите его.')
        if need['state'] != 'active' or need['expires'] <= time.time() or company(conn, user['org_id'])['verification'] not in {'verified', 'demo_verified'}:
            raise HTTPException(409, 'Сначала проверьте компанию и активные условия запроса.')
        data = json.loads(need['payload'])
        data['hiring_confirmation'] = {'at': now(), 'active_hiring': True, 'salary_approved': True, 'source': 'employer_statement'}
        conn.execute('UPDATE employer_needs SET payload=?,version=version+1 WHERE id=?', (dumps(data), identity))
        audit(conn, user['id'], 'hiring_confirmed', identity); bump(conn)
        return need_view(owned_need(conn, identity, user))

class ContactProgress(StrictModel):
    version: int = Field(ge=1)
    action: str
    message: str = Field(default='', max_length=1000)

@router.post('/invitations/{identity}/contact-progress')
def progress(identity: str, body: ContactProgress, user=Depends(session_user)):
    require_role(user, 'candidate', 'employer'); require_verified(user)
    if user['role'] == 'employer': require_org_role(user, 'owner', 'recruiter')
    actions = {'contact_confirmed', 'contact_missing'} if user['role'] == 'candidate' else {'contacted', 'interview_scheduled', 'decision_sent'}
    if body.action not in actions: raise HTTPException(403, 'Действие недоступно роли.')
    from .recruiting import own_invitation, invitation_view
    from .trust import company
    from .privacy import consent
    from .identity import notify
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE'); row = own_invitation(conn, identity, user)
        if row['version'] != body.version: raise HTTPException(409, 'Взаимодействие изменилось; обновите список.')
        if row['status'] != 'accepted': raise HTTPException(409, 'Сначала кандидат должен принять приглашение.')
        if company(conn, row['org_id'])['verification'] not in {'verified', 'demo_verified'} or not consent(conn, row['candidate_id'], 'processing') or not conn.execute('SELECT 1 FROM contact_grants WHERE candidate_id=? AND org_id=? AND active=1', (row['candidate_id'], row['org_id'])).fetchone():
            raise HTTPException(403, 'Разрешение на контакт больше не действует.')
        history = json.loads(row['history'])
        history.append({'status': body.action, 'at': now(), 'actor': user['role'], 'message': body.message, 'source': 'participant_statement'})
        conn.execute('UPDATE invitations SET history=?,version=version+1 WHERE id=?', (dumps(history), identity))
        recipient = row['sender_id'] if user['role'] == 'candidate' else conn.execute('SELECT id FROM users WHERE candidate_id=?', (row['candidate_id'],)).fetchone()[0]
        notify(conn, recipient, 'contact_progress', identity, 'Обновлён следующий шаг по принятому приглашению.')
        audit(conn, user['id'], 'contact_progress', identity, {'action': body.action})
        return invitation_view(conn.execute('SELECT * FROM invitations WHERE id=?', (identity,)).fetchone())
