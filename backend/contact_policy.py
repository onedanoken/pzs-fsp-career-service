"""Time-bound employer statements and mutually recorded contact checkpoints."""
import json
import time
from datetime import datetime, timezone

RECONFIRM_DAYS = 14

def timestamp(value):
    try:
        result = datetime.fromisoformat(value)
        return (result if result.tzinfo else result.replace(tzinfo=timezone.utc)).timestamp()
    except (ValueError, TypeError):
        return None

def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None

def hiring_activity(need, clock=None):
    clock = time.time() if clock is None else clock
    data = json.loads(need['payload'])
    confirmation = data.get('hiring_confirmation') or {}
    observed = confirmation.get('at') or data.get('last_verified_at') or need['created_at']
    observed_at = timestamp(observed)
    valid = observed_at is not None and observed_at <= clock + 300
    deadline = min(need['expires'], observed_at + RECONFIRM_DAYS*86400) if valid else None
    status = need['state']
    if status == 'active':
        status = 'expired' if need['expires'] <= clock else 'current' if deadline and deadline > clock else 'reconfirmation_required'
    return {
        'status': status, 'observed_at': observed if valid else None,
        'reconfirm_by': iso(deadline), 'reconfirm_days': RECONFIRM_DAYS,
        'source': 'employer_confirmation' if confirmation else 'employer_created_or_updated',
        'salary_approved_by_employer': confirmation.get('salary_approved') is True,
        'notice': 'Заявление работодателя об актуальности; не независимая гарантия вакансии или найма.',
    }

def contact_state(invitation, clock=None):
    clock = time.time() if clock is None else clock
    payload = json.loads(invitation['payload'])
    plan = payload.get('contact_plan')
    if not plan:
        return {'status': 'not_agreed', 'deadline': None, 'next_step': None, 'response_days': None}
    result = dict(plan, status='not_started', deadline=None, evidence_source='participant_statements')
    if invitation['status'] != 'accepted':
        return result
    history = json.loads(invitation['history'])
    accepted = next((timestamp(x['at']) for x in history if x['status'] == 'accepted'), None)
    if accepted is None:
        return result
    deadline = accepted + plan['response_days']*86400
    candidate = next((x for x in reversed(history) if x['status'] in {'contact_confirmed', 'contact_missing'}), None)
    employer = next((x for x in reversed(history) if x['status'] in {'contacted', 'interview_scheduled', 'decision_sent'}), None)
    status = 'confirmed' if candidate and candidate['status'] == 'contact_confirmed' else 'disputed' if candidate else 'employer_reported' if employer else 'overdue' if clock > deadline else 'waiting'
    return dict(result, status=status, deadline=iso(deadline), employer_reported_at=employer['at'] if employer else None, candidate_reported_at=candidate['at'] if candidate else None)
