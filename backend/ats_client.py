"""Reference adapter boundary for proprietary ATS vendors.

Never grants consent or bypasses server permissions. A vendor maps its own IDs
and field names to this contract and persists its own synchronization cursor.
"""
import time,secrets
import httpx

class UniversalATSClient:
    def __init__(self,base_url,token):
        self.http=httpx.Client(base_url=base_url.rstrip('/'),headers={'Authorization':'Bearer '+token},timeout=20,trust_env=False,follow_redirects=False)
    def close(self):self.http.close()
    def request(self,method,path,body=None,key=None):
        # A logical mutation has one key even when delivery is repeated.
        headers={'Idempotency-Key':key or secrets.token_hex(16)} if method!='GET' else {}
        for attempt in range(3):
            try:
                response=self.http.request(method,'/api/ats/v1'+path,json=body,headers=headers)
                if response.status_code in {429,502,503,504} and attempt<2:
                    try:delay=min(3,max(.1,float(response.headers.get('Retry-After','1'))))
                    except ValueError:delay=1
                    time.sleep(delay);continue
                response.raise_for_status();return response.json()
            except (httpx.ConnectError,httpx.ReadTimeout):
                if attempt==2:raise
                time.sleep(.25*(attempt+1))
    def import_need(self,external_id,need,key=None):return self.request('POST','/needs',{'external_id':external_id,'need':need},key)
    def profiles(self):
        items=[];cursor=None
        while True:
            from urllib.parse import urlencode
            page=self.request('GET','/profiles'+('?'+urlencode({'cursor':cursor}) if cursor else ''));items.extend(page['items'])
            if not page.get('has_more'):return items
            cursor=page['next_cursor']
    def stage(self,invitation_id,external_candidate_id,stage,key=None):return self.request('POST','/stages',{'invitation_id':invitation_id,'external_candidate_id':external_candidate_id,'stage':stage},key)
    def profile(self,invitation_id):
        from urllib.parse import quote
        try:return self.request('GET','/profiles/'+quote(invitation_id,safe=''))
        except httpx.HTTPStatusError as error:
            if error.response.status_code==404:return None
            raise
    def close_need(self,external_id,key=None):
        from urllib.parse import quote
        return self.request('DELETE','/needs/'+quote(external_id,safe=''),key=key)
    def events(self,cursor=None):
        from urllib.parse import urlencode
        return self.request('GET','/events'+('?'+urlencode({'cursor':cursor}) if cursor else ''))
    def acknowledge_deletion(self,event_id,note=''):return self.request('POST','/events/'+event_id+'/ack',{'deleted':True,'note':note})
