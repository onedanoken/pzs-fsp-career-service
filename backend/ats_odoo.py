"""Odoo 19 Recruitment JSON-2 adapter; requires the shipped fsp_bridge addon."""
import time
import httpx

class AdapterError(Exception):
    def __init__(self,code):super().__init__(code);self.code=code

class OdooRecruitment:
    def __init__(self,url,key,database,company_id):
        self.company_id=company_id
        self.http=httpx.Client(base_url=url.rstrip('/'),headers={'Authorization':'bearer '+key,'X-Odoo-Database':database,'User-Agent':'FSP-ATS-Bridge/1'},timeout=20,trust_env=False,follow_redirects=False)
    def close(self):self.http.close()
    def call(self,model,method,**body):
        body['context']={'allowed_company_ids':[self.company_id],'active_test':False,'lang':'en_US'}
        for attempt in range(3):
            try:response=self.http.post('/json/2/'+model+'/'+method,json=body)
            except (httpx.ConnectError,httpx.TimeoutException):
                if attempt==2:raise AdapterError('network_error') from None
                time.sleep(.2*(attempt+1));continue
            if response.status_code in {429,502,503,504} and attempt<2:
                try:delay=min(5,max(.1,float(response.headers.get('Retry-After','1'))))
                except ValueError:delay=1
                time.sleep(delay);continue
            if response.status_code in {401,403}:raise AdapterError('access_denied')
            if response.status_code>=500:raise AdapterError('remote_error')
            if response.status_code!=200:raise AdapterError('invalid_data')
            try:return response.json()
            except ValueError:raise AdapterError('invalid_data') from None
        raise AdapterError('remote_error')
    def positions(self,max_records=5000):
        rows=[]
        for offset in range(0,max_records,100):
            page=self.call('hr.job','search_read',domain=[['company_id','=',self.company_id]],fields=['id','name','description','active','no_of_recruitment'],limit=100,offset=offset,order='id')
            if not isinstance(page,list):raise AdapterError('invalid_data')
            rows.extend(page)
            if len(page)<100:return rows
        raise AdapterError('invalid_data')
    def upsert(self,reference,candidate_id,job_id,profile):return self.call('hr.applicant','fsp_upsert',invitation_ref=reference,candidate_ref=candidate_id,job_id=job_id,profile=profile)
    def status(self,reference):return self.call('hr.applicant','fsp_status',invitation_ref=reference)
    def forget(self,reference):return self.call('hr.applicant','fsp_forget',invitation_ref=reference)
