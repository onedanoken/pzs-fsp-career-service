"""Declarative reference rules, separate from the bounded replay implementation."""
def api_reference(condition,strategy='atomic_record'):
    rows=condition['requests']
    def eligible(row):
        return row['authorized'] and not row['revoked'] and (not condition['require_expiry'] or row['expires']>condition['now'])
    output=[]
    for position,row in enumerate(rows):
        if not eligible(row):output.append(403);continue
        preceding=[other for other in rows[:position] if eligible(other) and (other['tenant'],other['key'])==(row['tenant'],row['key'])]
        if not preceding:output.append(202 if strategy=='transactional_outbox' else 201)
        else:output.append(200 if row['body']==preceding[0]['body'] else 409)
    return output

def react_reference(condition):
    result={}
    for channel in sorted({r['channel'] for r in condition['events']}):
        events=[r for r in condition['events'] if r['channel']==channel]
        issued=[(i,r) for i,r in enumerate(events) if r['type']=='issue']
        position,latest=issued[-1]
        suffix=events[position+1:]
        responses=[r for r in suffix if r['type']=='response' and r['id']==latest['id']]
        closed=any(r['type']=='unmount' for r in suffix)
        response=responses[-1] if responses else None
        status='closed' if closed else 'loading' if response is None else 'ready' if 200<=response['status']<300 else 'error'
        result[channel]={'request':latest['id'],'status':status,'items':response['items'] if status=='ready' and response['status']==200 else []}
    return result
