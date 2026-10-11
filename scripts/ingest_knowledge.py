"""Pin provenance of public sources. Store hashes and owned abstracts, not texts."""
import hashlib,json,sys
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.assessment_catalog import SOURCES
from backend.db import now
records=[]
with httpx.Client(timeout=30,follow_redirects=True,headers={'User-Agent':'FSPCareer-source-provenance/0.3'},trust_env=False) as client:
    pin=client.get('https://api.github.com/repos/yangshun/tech-interview-handbook/commits/main');pin.raise_for_status();sha=pin.json()['sha']
    for source in SOURCES:
        url=source['url']
        if source['id']=='interview-handbook':url='https://raw.githubusercontent.com/yangshun/tech-interview-handbook/'+sha+'/README.md'
        response=client.get(url);response.raise_for_status()
        records.append({**source,'resolved_url':str(response.url),'fetched_at':now(),'http_status':response.status_code,'sha256':hashlib.sha256(response.content).hexdigest(),'bytes':len(response.content),'upstream_revision':sha if source['id']=='interview-handbook' else response.headers.get('last-modified'),'retained_content':'metadata and owned summary only'})
    license_response=client.get('https://raw.githubusercontent.com/yangshun/tech-interview-handbook/'+sha+'/LICENSE');license_response.raise_for_status()
    (ROOT/'docs'/'INTERVIEW_SOURCE_LICENSE.txt').write_text(license_response.text,encoding='utf-8')
target=ROOT/'data'/'knowledge_manifest.json';target.write_text(json.dumps({'version':'knowledge-provenance-v1','sources':records},ensure_ascii=False,indent=2),encoding='utf-8')
print('Source provenance recorded:',len(records))
