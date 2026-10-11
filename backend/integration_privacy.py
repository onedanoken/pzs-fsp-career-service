"""Durable withdrawal messages. No contact data is stored in these events."""
import json,secrets
from .db import dumps,now

def withdrawal_event(conn,invitation,reason):
    if not json.loads(invitation['payload']).get('ats_consent',{}).get('granted'):return
    timestamp=now()
    for client in conn.execute('SELECT id FROM ats_connections WHERE org_id=?',(invitation['org_id'],)).fetchall():
        identity='event_'+secrets.token_hex(10)
        payload={'event_type':'consent_revoked','invitation_id':invitation['id'],'reason':reason,'occurred_at':timestamp}
        conn.execute("INSERT INTO integration_events(id,connection_id,direction,external_key,payload,status,created_at) VALUES(?,?,'outbound',?,?,'awaiting_ack',?)",(identity,client[0],'withdrawal:'+identity,dumps(payload),timestamp))

def revoke_transfers(conn,candidate_id,org_id=None,reason='contact_withdrawn'):
    query='SELECT * FROM invitations WHERE candidate_id=?';parameters=[candidate_id]
    if org_id:query+=' AND org_id=?';parameters.append(org_id)
    for row in conn.execute(query,parameters).fetchall():
        payload=json.loads(row['payload'])
        if payload.get('ats_consent',{}).get('granted'):
            withdrawal_event(conn,row,reason);payload['ats_consent'].update({'granted':False,'revoked_at':now(),'reason':reason})
            conn.execute('UPDATE invitations SET payload=?,version=version+1 WHERE id=?',(dumps(payload),row['id']))
