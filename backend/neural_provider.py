"""One bounded, private request for anonymous task conditions; never grading."""
import json
import os
import re
import threading
from urllib.parse import urlparse
import httpx
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from .assessment_catalog import SOURCES, GRADES

SLOT = threading.BoundedSemaphore(1)

class Variant(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    context: str = Field(min_length=30, max_length=500)
    values: list[int] = Field(min_length=6, max_length=10)
    threshold: int = Field(ge=10, le=140)
    operation: Literal['sum', 'count', 'max']

def request_payload(spec, grade, rng, protocol):
    source = next(s for s in SOURCES if s['id'] == 'python-docs')
    domain = {'backend':'размеры пакетов API','frontend':'время загрузки карточек интерфейса','qa':'длительность ответов тестового прогона'}[spec]
    # The model writes context. The application owns mathematical constraints
    # and the independent oracle; a small model is not a trustworthy solver.
    threshold=rng.randint(15,130)
    values=[rng.randint(5,threshold-1),rng.randint(threshold,160)]
    values+=rng.sample([x for x in range(5,161) if x not in values],4+min(2,GRADES.index(grade)))
    rng.shuffle(values)
    parameters={'values':values,'threshold':threshold,'operation':rng.choice(['sum','count','max'])}
    prompt=('Write one realistic Russian work scenario, 30-100 characters, about '+domain+'. '+
            'Describe data arriving from this process. Do not mention interviews, grades, JSON, instructions or solutions. '+
            'Return context and these EXACT parameters: '+json.dumps(parameters)+'. '+
            'Knowledge: '+source['summary']+' New scenario '+str(rng.randrange(100000,999999)))
    if protocol == 'llamacpp':
        schema = Variant.model_json_schema()
        schema['properties']['context']['maxLength'] = 100
        for key,value in parameters.items():schema['properties'][key]={'const':value}
        chat = '<|im_start|>system\nReturn one JSON object. No solutions. Follow the schema.<|im_end|>\n<|im_start|>user\n'+prompt+'<|im_end|>\n<|im_start|>assistant\n'
        return '/completion', {'prompt':chat,'json_schema':schema,'stream':False,'n_predict':160,'temperature':.7,'top_p':.9,'seed':rng.randrange(1,2147483647),'cache_prompt':False}
    if protocol=='fsp-json-v1':return '/generate',{'schema':'fsp-generator-request-v1','specialization':spec,'grade':grade,'parameters':parameters,'output_schema':Variant.model_json_schema(),'context_language':'ru','max_context_characters':100,'model':os.environ.get('FSP_GENERATOR_MODEL','configured-provider')}
    if protocol != 'ollama': raise ValueError('unsupported protocol')
    return '/api/generate', {'model':os.environ.get('FSP_GENERATOR_MODEL','qwen2.5:0.5b'),'stream':False,'format':'json','prompt':prompt,'options':{'temperature':.7,'num_predict':300}}

def parse_variant(raw):
    if not isinstance(raw,str) or len(raw)>12000:raise ValueError('invalid response size')
    if raw.startswith('```'):raw=raw.split('\n',1)[1].rsplit('```',1)[0].strip()
    variant = None
    for offset, char in enumerate(raw):
        if char != '{':continue
        try:
            obj,_=json.JSONDecoder().raw_decode(raw[offset:]);variant=Variant.model_validate(obj);break
        except ValueError:continue
    if variant is None:raise ValueError('invalid schema')
    if any(not 5<=x<=160 for x in variant.values) or len(set(variant.values))<4 or not any(x>=variant.threshold for x in variant.values) or not any(x<variant.threshold for x in variant.values):raise ValueError('invalid conditions')
    if (variant.values,variant.threshold,variant.operation)==([12,37,49,68,83,94,113,145],65,'sum'):raise ValueError('copied legacy prompt example')
    if re.search(r'ignore previous|system prompt|<script|javascript:|\b(answer|solution)\s*(is|:|=)|\bответ\s*[:=]',variant.context,re.I):raise ValueError('solution or instruction in context')
    if re.search(r'\bjson\b|\bcontext\b|\bvalues\b|символов|\b(junior|middle|senior|intern)\b|идентификатор.*варианта',variant.context,re.I):raise ValueError('meta instructions instead of work context')
    letters=re.findall(r'[A-Za-zА-Яа-яЁё]',variant.context)
    russian=re.findall(r'[А-Яа-яЁё]',variant.context)
    if len(russian)<10 or len(russian)<len(letters)*.4 or variant.context.rstrip()[-1] not in '.!?:':raise ValueError('Russian complete work context required')
    return variant

def neural_variant(spec,grade,rng):
    fallback={'generator':'validated_variants','generator_error':None,'neural_items':0}
    base=os.environ.get('FSP_OLLAMA_URL')
    if not base:return None,fallback
    if not SLOT.acquire(blocking=False):return None,dict(fallback,generator_error='neural_provider_busy')
    try:
        protocol=os.environ.get('FSP_GENERATOR_PROTOCOL','ollama')
        parsed=urlparse(base);token=os.environ.get('FSP_GENERATOR_TOKEN','')
        if parsed.scheme not in {'http','https'} or parsed.username or parsed.password or parsed.query or parsed.fragment:raise ValueError('invalid provider URL')
        if protocol in {'llamacpp','fsp-json-v1'} and not token:raise ValueError('private provider requires a token')
        if token and parsed.scheme=='http' and parsed.hostname not in {'generator','localhost','127.0.0.1','::1'}:raise ValueError('token requires TLS outside private local network')
        timeout=min(45,max(1,float(os.environ.get('FSP_GENERATOR_TIMEOUT','30'))))
        endpoint,body=request_payload(spec,grade,rng,protocol)
        headers={'Authorization':'Bearer '+token} if token else {}
        with httpx.Client(timeout=httpx.Timeout(timeout,connect=3),trust_env=False) as client:
            response=client.post(base.rstrip('/')+endpoint,json=body,headers=headers);response.raise_for_status()
            if len(response.content)>20000:raise ValueError('oversized envelope')
            envelope=response.json()
        raw=envelope['content'] if protocol=='llamacpp' else envelope['response']
        variant=parse_variant(raw.strip())
        if protocol=='fsp-json-v1' and (envelope.get('schema')!='fsp-generator-response-v1' or any(getattr(variant,k)!=body['parameters'][k] for k in ['values','threshold','operation'])):raise ValueError('model changed frozen parameters or schema')
        if protocol=='llamacpp' and any(getattr(variant,key)!=body['json_schema']['properties'][key]['const'] for key in ['values','threshold','operation']):raise ValueError('model changed controlled parameters')
        metadata={'generator':'llama.cpp' if protocol=='llamacpp' else envelope.get('provider','ollama'),'model':os.environ.get('FSP_GENERATOR_MODEL') if protocol=='llamacpp' else envelope.get('model',body['model']),'model_revision':os.environ.get('FSP_GENERATOR_REVISION') if protocol=='llamacpp' else envelope.get('revision'),'quantization':os.environ.get('FSP_GENERATOR_QUANTIZATION') if protocol=='llamacpp' else envelope.get('quantization'),'generator_error':None,'neural_items':1,'generator_scope':'context_for_one_numeric_item' if protocol=='llamacpp' else 'validated_conditions_for_one_numeric_item'}
        if protocol=='fsp-json-v1' and envelope.get('mock') is True:metadata.update(neural_items=0,generator='reference_protocol_simulator',generator_scope='protocol_test_not_real_neural_generation')
        return variant,metadata
    except (httpx.HTTPError,KeyError,TypeError,ValueError):
        return None,dict(fallback,generator_error='neural_provider_unavailable_or_invalid')
    finally:SLOT.release()
