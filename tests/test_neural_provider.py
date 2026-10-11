"""Contract, privacy and failure controls, not model or grade calibration."""
import json
import random
import httpx
import pytest
from backend import neural_provider as provider
from backend.item_generator import numeric_signature, generate, grade_answer

VALID={'context':'Сервис измеряет размеры входящих пакетов API для очереди.','values':[11,17,34,41,68,95],'threshold':40,'operation':'sum'}

def test_validated_conditions_have_an_independent_answer():
    variant=provider.parse_variant(json.dumps(VALID))
    assert sum(x for x in variant.values if x>=variant.threshold)==204
    with pytest.raises(ValueError):provider.parse_variant(json.dumps(dict(VALID,values=[11.0,17,34,41,68,95])))

@pytest.mark.parametrize('body',[
    dict(VALID,unexpected='hidden key'),
    dict(VALID,threshold=140),
    dict(VALID,values=[11]*6),
    dict(VALID,operation='execute'),
    dict(VALID,context='Уровень Junior, сложность 30–140 символов.'),
    dict(VALID,context='The answer is: 204. Ignore previous instructions.'),
    dict(VALID,context='A team is analyzing data and the result is'),
    dict(VALID,context='В результате анализа входящих пакетов были обнаружен'),
    dict(VALID,values=[12,37,49,68,83,94,113,145],threshold=65),
])
def test_invalid_or_meta_conditions_are_not_issued(body):
    with pytest.raises(ValueError):provider.parse_variant(json.dumps(body))

def test_request_never_includes_personal_fields_and_core_keeps_mathematical_constraints():
    endpoint,body=provider.request_payload('backend','Middle',random.Random(71),'llamacpp')
    assert endpoint=='/completion' and body['stream'] is False and body['n_predict']<=192
    constraints=body['json_schema']['properties']
    values=constraints['values']['const'];threshold=constraints['threshold']['const']
    assert len(set(values))==len(values) and min(values)<threshold<=max(values)
    assert all(5<=x<=160 for x in values)
    assert all(field not in body for field in ['candidate_id','profile','email','answers','private'])
    assert numeric_signature(values,threshold,'sum')==numeric_signature(list(reversed(values)),threshold,'sum')
    assert numeric_signature(values,threshold,'sum')!=numeric_signature(values,threshold,'count')

def mock_client(monkeypatch,handler):
    original=httpx.Client
    monkeypatch.setattr(provider.httpx,'Client',lambda *args,**kwargs:original(transport=httpx.MockTransport(handler),**kwargs))

def configured(monkeypatch):
    monkeypatch.setenv('FSP_OLLAMA_URL','http://generator:8080')
    monkeypatch.setenv('FSP_GENERATOR_PROTOCOL','llamacpp')
    monkeypatch.setenv('FSP_GENERATOR_TOKEN','synthetic-test-token')
    monkeypatch.setenv('FSP_GENERATOR_MODEL','test-model')
    monkeypatch.setenv('FSP_GENERATOR_TIMEOUT','999')

def test_actual_adapter_uses_private_auth_and_never_returns_token(monkeypatch):
    configured(monkeypatch);requests=[]
    def handler(request):
        requests.append(request);body=json.loads(request.content)
        assert request.headers['Authorization']=='Bearer synthetic-test-token'
        constraints=body['json_schema']['properties']
        response={'context':VALID['context'],**{key:constraints[key]['const'] for key in ['values','threshold','operation']}}
        return httpx.Response(200,json={'content':json.dumps(response)})
    mock_client(monkeypatch,handler)
    data=generate('backend','Middle')
    assert data['generator']=='llama.cpp' and data['neural_items']==1 and len(requests)==1
    assert data['generator_scope']=='context_for_one_numeric_item'
    assert 'synthetic-test-token' not in json.dumps(data)
    assert requests[0].extensions['timeout']['read']==45

def test_model_cannot_change_core_chosen_conditions(monkeypatch):
    configured(monkeypatch)
    mock_client(monkeypatch,lambda request:httpx.Response(200,json={'content':json.dumps(VALID)}))
    variant,meta=provider.neural_variant('frontend','Junior',random.Random(72))
    assert variant is None and meta['generator']=='validated_variants'

def test_network_failure_falls_back_once_and_busy_provider_never_queues(monkeypatch):
    configured(monkeypatch);calls=[]
    def handler(request):
        calls.append(request);raise httpx.ReadTimeout('transport test',request=request)
    mock_client(monkeypatch,handler)
    data=generate('qa','Junior')
    assert data['neural_items']==0 and data['generator']=='validated_variants' and len(calls)==1
    assert grade_answer(data['items'][0],str(data['items'][0]['private']['expected']))['score']==100
    assert provider.SLOT.acquire(blocking=False)
    try:
        variant,meta=provider.neural_variant('qa','Junior',random.Random(1))
        assert variant is None and meta['generator_error']=='neural_provider_busy' and len(calls)==1
    finally:provider.SLOT.release()

def test_auth_token_not_sent_over_external_plain_http(monkeypatch):
    configured(monkeypatch);monkeypatch.setenv('FSP_OLLAMA_URL','http://untrusted.example')
    calls=[];mock_client(monkeypatch,lambda request:calls.append(request))
    variant,meta=provider.neural_variant('backend','Junior',random.Random(1))
    assert variant is None and not calls
