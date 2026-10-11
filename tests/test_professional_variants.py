"""Independent operational checks of generated cases; not expert calibration."""
import copy,json,random
from decimal import Decimal,ROUND_HALF_UP
import pytest
from backend import db
from backend.assessment_catalog import GRADES,SPECIALIZATIONS,BLUEPRINT_VERSION
from backend.professional_items import tasks
from backend.professional_variants import condition_signature
from backend.item_generator import generate,grade_answer,public_item
from backend.jobs import Worker

def independent_numeric(item):
    c=item['private']['condition'];f=item['family'].split(':',1)[1]
    if f.startswith('rollout_'):
        admissible=[down for down in range(c['replicas']+1) if down*100<=c['replicas']*c['unavailable']] if c['percent'] else list(range(c['unavailable']+1))
        return min(c['replicas']-down for down in admissible)
    if f in ['memory_requests','cpu_memory_requests']:
        count=0;memory=c['memory']-c['reserve'];cpu=c.get('cpu',10**9)-c.get('cpu_reserve',0)
        while memory>=c['pod']+c['sidecar'] and cpu>=c.get('pod_cpu',0)+c.get('sidecar_cpu',0):
            memory-=c['pod']+c['sidecar'];cpu-=c.get('pod_cpu',0)+c.get('sidecar_cpu',0);count+=1
        return count
    if f in ['parallel_join','branched_pipeline']:
        durations=c['durations'];dependencies=[[],[],[0,1]] if len(durations)==3 else [[],[],[0],[1],[2,3]]
        end=[]
        for duration,parents in zip(durations,dependencies):end.append(duration+max([end[p] for p in parents] or [0]))
        return end[-1]
    if f.startswith('metric_'):
        tp,fp,fn,tn=[Decimal(c[k]) for k in ['tp','fp','fn','tn']]
        precision=tp/(tp+fp);recall=tp/(tp+fn)
        value={'precision':precision,'recall':recall,'F1':2*precision*recall/(precision+recall),'balanced accuracy':(recall+tn/(tn+fp))/2}[c['metric']]
        return int((value*100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    if f=='equal_groups':
        records=[group for group in range(c['groups']) for _ in range(c['size'])]
        test_groups=set(range(len(range(c['groups']))*c['rate']//100))
        return len([group for group in records if group in test_groups])
    if f=='unequal_groups':return len([key for key,size in c['sizes'].items() for _ in range(size) if key in c['selected']])
    if f=='time_gap':return len(list(range(c['total']))[c['train']+c['gap']:])
    if f in ['false_positives','error_cost']:
        costs=[]
        for actual,predicted in zip(c['actual'],c['predicted']):
            costs.append(c.get('fp_cost',1) if (actual,predicted)==(0,1) else c.get('fn_cost',0) if (actual,predicted)==(1,0) else 0)
        return sum(costs)
    if f=='owner_access':return len([owner for owner in c['owners'] if owner==c['actor']])
    if f=='tenant_action':return len([1 for org,action,active in c['rows'] if org==c['tenant'] and active and action in c['actions']])
    if f=='subnet_hosts':return len(range(1,2**(32-c['prefix'])-1))
    if f=='network_allowlist':
        prefix=c['subnet'].split('.');return len([1 for ip,port in c['connections'] if ip.split('.')[:3]==prefix[:3] and port==c['port']])
    if f in ['active_role_write','scoped_write']:
        permissions={'reader':set(),'writer':{'write'},'admin':{'write'}}
        scopes=c.get('scope',[True]*len(c['roles']))
        return len([1 for role,blocked,scope in zip(c['roles'],c['disabled'],scopes) if 'write' in permissions[role] and not blocked and scope])
    raise AssertionError('Unknown numeric family '+f)

@pytest.mark.parametrize('spec',['devops','ml','security'])
@pytest.mark.parametrize('grade',GRADES)
def test_professional_numeric_conditions_against_independent_simulators(spec,grade):
    families=set()
    for seed in range(60):
        items=tasks(spec,grade,random.Random(seed));families.update(i['family'] for i in items)
        assert set(SPECIALIZATIONS[spec]['competencies'])=={i['competency'] for i in items}
        for item in items:
            assert 'private' not in public_item(item) and 'condition' not in public_item(item)
            if item['kind'] in ['number','debug']:
                expected=independent_numeric(item)
                assert item['private']['expected']==expected,item['family']
                assert grade_answer(item,str(expected))['score']==100
                assert grade_answer(item,str(expected+1))['score']==0
    assert len(families)>=8

def test_shuffle_and_new_ids_cannot_disguise_identical_professional_conditions():
    items=tasks('ml','Middle',random.Random(7));same=copy.deepcopy(items)
    random.Random(18).shuffle(same)
    for item in same:
        item['id']='new-'+item['id'];item['prompt']='Другая формулировка того же условия'
        if item.get('choices'):item['choices'].reverse()
    assert condition_signature(items)==condition_signature(same)
    changed=copy.deepcopy(items);changed[0]['private']['condition']['tp']+=1
    assert condition_signature(items)!=condition_signature(changed)

def test_higher_level_adds_combined_constraints_without_claiming_calibration():
    for spec,required in [('devops','cpu_memory_requests'),('ml','error_cost'),('security','scoped_write')]:
        for seed in range(10):
            items=tasks(spec,'Senior',random.Random(seed))
            assert any(x['family']==spec+':'+required for x in items)
    data=generate('ml','Senior',allow_neural=False)
    assert data['calibration']=='pilot_unvalidated' and data['blueprint_version']==BLUEPRINT_VERSION
    assert data['neural_items']==0 and data['total_items']==6

@pytest.mark.parametrize('spec',['ml','security'])
def test_permuting_counted_rows_does_not_create_a_new_case(spec):
    items=tasks(spec,'Senior',random.Random(13));permuted=copy.deepcopy(items)
    for item in permuted:
        c=item['private']['condition']
        for key in ['owners','rows','connections']:
            if key in c:c[key].reverse()
        if 'actual' in c:c['actual'].reverse();c['predicted'].reverse()
        if 'roles' in c:
            c['roles'].reverse();c['disabled'].reverse()
            if 'scope' in c:c['scope'].reverse()
    assert condition_signature(items)==condition_signature(permuted)

def drain():
    worker=Worker()
    for _ in range(100):
        if not worker.run_once():return
    raise AssertionError('Queue did not drain')

def start(candidate):
    response=candidate.post('/api/assessments',json={'specialization':'ml','grade':'Junior','mode':'practice'})
    assert response.status_code==202,response.text
    drain();return candidate.get('/api/assessments/'+response.json()['id']).json()

def test_every_retry_is_checked_before_issuing_and_prior_session_stays_frozen(clients,monkeypatch):
    employer,candidate,expert,sign=clients
    from backend import assessments
    first=generate('ml','Junior',allow_neural=False);fresh=generate('ml','Junior',allow_neural=False)
    monkeypatch.setattr(assessments,'generate',lambda *a,**k:copy.deepcopy(first))
    prior=start(candidate);assert prior['status']=='active'
    assert candidate.post('/api/assessments/'+prior['id']+'/cancel',json={}).status_code==200
    calls=[];variants=iter([first,first,fresh])
    def replacement(*args,**kw):calls.append(kw['allow_neural']);return copy.deepcopy(next(variants))
    monkeypatch.setattr(assessments,'generate',replacement)
    new=start(candidate);assert new['status']=='active' and calls==[True,False,False]
    assert candidate.get('/api/assessments/'+prior['id']).json()['items']==prior['items']
    assert 'condition' not in json.dumps(new,ensure_ascii=False)
    assert employer.get('/api/assessments/'+new['id']).status_code==403

def test_exhausted_variant_pool_is_technical_failure_and_does_not_change_grade(clients,monkeypatch):
    employer,candidate,expert,sign=clients
    from backend import assessments
    fixed=generate('ml','Junior',allow_neural=False)
    monkeypatch.setattr(assessments,'generate',lambda *a,**k:copy.deepcopy(fixed))
    prior=start(candidate);candidate.post('/api/assessments/'+prior['id']+'/cancel',json={})
    before=candidate.get('/api/assessments').json()['certifications'];calls=[]
    def replacement(*args,**kw):calls.append(kw['allow_neural']);return copy.deepcopy(fixed)
    monkeypatch.setattr(assessments,'generate',replacement)
    failed=start(candidate)
    assert failed['status']=='cancelled' and failed['result'] is None and failed['technical_error']
    assert calls==[True,False,False,False,False]
    assert candidate.get('/api/assessments').json()['certifications']==before
    with db.connection() as conn:
        job=conn.execute("SELECT error_code,status FROM jobs WHERE kind='assessment.generate' AND json_extract(payload,'$.session_id')=?",(failed['id'],)).fetchone()
        assert job['status']=='failed' and job['error_code']=='variant_pool_collision'

def test_stored_v4_private_format_and_criteria_survive_the_catalog_update(clients,monkeypatch):
    employer,candidate,expert,sign=clients
    from backend import assessments
    from backend.professional_items import LEVEL_CRITERIA
    legacy=generate('ml','Junior',allow_neural=False);legacy['blueprint_version']='competencies-2026-10-v4'
    for item in legacy['items']:
        item['version']='competencies-2026-10-v4';item['private'].pop('condition')
    monkeypatch.setattr(assessments,'generate',lambda *a,**k:copy.deepcopy(legacy))
    current=start(candidate);original_coverage=current['coverage']
    monkeypatch.setitem(SPECIALIZATIONS,'ml',{**SPECIALIZATIONS['ml'],'competencies':['future_unissued_area']})
    monkeypatch.setitem(LEVEL_CRITERIA,'Junior',['New criterion for future sessions'])
    for item in legacy['items']:
        key=item['private'].get('expected')
        answer=json.dumps(key) if item['kind']=='multi' else str(key) if key is not None else 'Синтетический открытый ответ без независимой экспертной оценки.'
        response=candidate.put('/api/assessments/'+current['id']+'/answer',json={'item_id':item['id'],'answer':answer,'version':current['version']})
        assert response.status_code==200,response.text;current=response.json()
    candidate.post('/api/assessments/'+current['id']+'/finish',json={'version':current['version']});drain()
    result=candidate.get('/api/assessments/'+current['id']).json()
    assert result['result']['score']==100 and result['result']['calibration']=='pilot_unvalidated'
    assert all(x['version']=='competencies-2026-10-v4' for x in result['items'])
    assert result['coverage']['blueprint_competencies']==original_coverage['blueprint_competencies']
    assert result['coverage']['level_criteria']==original_coverage['level_criteria']
