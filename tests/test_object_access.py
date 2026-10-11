"""Synthetic access contexts and temporal authorization must stay fail-closed."""
import copy
import itertools
import json
import random
from unittest.mock import Mock

from fastapi import HTTPException
import pytest

from backend import object_access as access
from backend.item_generator import generate, grade_answer, public_item
from backend.work_samples import add_journey, generate_case, generate_legacy_case


def item_for(grade='Junior', variant=None, tenants=None, document='doc-17'):
    return access.generate(grade, Mock(choice=Mock(side_effect=[tenants or ['A','B'],document])), variant or access.VARIANTS[grade][0])


@pytest.mark.parametrize('grade,variant',[(g,v) for g,variants in access.VARIANTS.items() for v in variants])
def test_all_authored_conditions_pass_reference_and_detect_defect(grade,variant):
    for tenants,document in itertools.product([['A','B'],['north','south']],['doc-17','doc-29']):
        item=item_for(grade,variant,tenants,document)
        assert grade_answer(item,item['private']['reference_answer'])['all_checks_passed']
        assert not grade_answer(item,item['starter'])['all_checks_passed']


@pytest.mark.parametrize('grade',['Junior','Middle'])
def test_entire_context_matrix_matches_contract(grade):
    item=item_for(grade);condition=item['private']['condition'];plan=json.loads(item['private']['reference_answer'])
    states=['active','locked','deleted','unknown'] if grade=='Middle' else ['active']
    for role,action,same,owned,active,scoped,state in itertools.product(['reader','editor','guest'],['read','write','export'],[False,True],[False,True],[False,True],[False,True],states):
        context=access.context_for(condition,action,role,owned,same,active,scoped,state)
        assert access.decide(plan,context)==access.oracle(context,grade)


def test_golden_object_and_tenant_denials_with_positive_controls():
    item=item_for();condition=item['private']['condition'];good=json.loads(item['private']['reference_answer'])
    assert access.decide(good,access.context_for(condition))
    assert not access.decide(good,access.context_for(condition,owned=False))
    assert access.decide(good,access.context_for(condition,role='editor',owned=False))
    assert not access.decide(good,access.context_for(condition,role='editor',same_tenant=False))
    assert not access.decide(good,access.context_for(condition,action='write',role='reader'))
    assert not access.decide(good,access.context_for(condition,action='export',role='editor'))
    missing=json.loads(item['starter'])
    assert access.decide(missing,access.context_for(condition,owned=False))


def test_cached_allow_has_a_concrete_revocation_counterexample():
    item=item_for('Middle','cached_allow');initial=access.context_for(item['private']['condition'])
    events=[{'type':'authorize','ticket':'a'},{'type':'change','area':'token','field':'active','value':False},{'type':'authorize','ticket':'b'}]
    expected=[{'type':'authorize','ticket':'a','allowed':True},{'type':'authorize','ticket':'b','allowed':False}]
    assert access.trace_oracle(initial,events)==expected
    assert access.execute(json.loads(item['private']['reference_answer']),initial,events)==expected
    assert access.execute(json.loads(item['starter']),initial,events)[-1]['allowed'] is True


def test_live_authorize_does_not_replace_recheck_at_use():
    item=item_for('Middle','stale_use');initial=access.context_for(item['private']['condition'])
    events=[{'type':'authorize','ticket':'a'},{'type':'change','area':'object','field':'tenant','value':'B'},{'type':'use','ticket':'a'}]
    expected=[{'type':'authorize','ticket':'a','allowed':True},{'type':'use','ticket':'a','allowed':False}]
    assert access.execute(json.loads(item['private']['reference_answer']),initial,events)==access.trace_oracle(initial,events)==expected
    assert access.execute(json.loads(item['starter']),initial,events)[-1]['allowed'] is True
    assert grade_answer(item,item['starter'])['score']==87.5


def test_object_lifecycle_allows_locked_reads_but_rejects_writes_and_deleted_objects():
    item=item_for('Middle');condition=item['private']['condition'];plan=json.loads(item['private']['reference_answer'])
    assert access.decide(plan,access.context_for(condition,state='locked'))
    assert not access.decide(plan,access.context_for(condition,state='locked',action='write',role='editor'))
    for action in ['read','write']:
        assert not access.decide(plan,access.context_for(condition,state='deleted',action=action,role='editor'))
        assert not access.decide(plan,access.context_for(condition,state='unknown',action=action,role='editor'))


def test_all_context_changes_between_authorize_and_use_are_checked():
    item=item_for('Middle');condition=item['private']['condition'];plan=json.loads(item['private']['reference_answer'])
    for area,field,denied,restored,action in access.transitions(condition):
        initial=access.context_for(condition,action=action,role='editor' if action=='write' else 'reader')
        change={'type':'change','area':area,'field':field,'value':denied}
        events=[{'type':'authorize','ticket':'a'},change,{'type':'use','ticket':'a'},dict(change,value=restored),{'type':'authorize','ticket':'b'},{'type':'use','ticket':'b'}]
        expected=access.trace_oracle(initial,events)
        assert [x['allowed'] for x in expected]==[True,False,True,True]
        assert access.execute(plan,initial,events)==expected


def test_deny_ticket_requires_new_authorize_after_rights_grow():
    item=item_for('Middle');initial=access.context_for(item['private']['condition'],active=False)
    events=[{'type':'authorize','ticket':'a'},{'type':'change','area':'token','field':'active','value':True},{'type':'use','ticket':'a'},{'type':'authorize','ticket':'b'},{'type':'use','ticket':'b'}]
    expected=access.trace_oracle(initial,events)
    assert [x['allowed'] for x in expected]==[False,False,True,True]
    assert access.execute(json.loads(item['private']['reference_answer']),initial,events)==expected


@pytest.mark.parametrize('area,field,value',[('subject','id','u2'),('object','id','other'),('request','action','write')])
def test_ticket_is_bound_to_principal_object_action_and_unknown_ticket_is_denied(area,field,value):
    item=item_for('Middle');initial=access.context_for(item['private']['condition'],role='editor')
    initial['token']['scopes']=['read','write']
    events=[{'type':'use','ticket':'missing'},{'type':'authorize','ticket':'a'},{'type':'change','area':area,'field':field,'value':value},{'type':'use','ticket':'a'}]
    expected=access.trace_oracle(initial,events)
    assert [x['allowed'] for x in expected]==[False,True,False]
    assert access.execute(json.loads(item['private']['reference_answer']),initial,events)==expected


@pytest.mark.parametrize('grade',['Junior','Middle'])
def test_reference_independence_ordering_mutants_and_strict_schema(grade):
    item=item_for(grade);plan=json.loads(item['private']['reference_answer'])
    item['private']['reference_answer']='{}';plan['require'].reverse()
    assert grade_answer(item,json.dumps(plan))['all_checks_passed']
    mutants=[('logic','any'),('unknown','allow')]
    if grade=='Middle':mutants += [('cache','memoized'),('use','authorize_only')]
    for key,value in mutants:
        wrong=copy.deepcopy(plan);wrong[key]=value
        assert not grade_answer(item,json.dumps(wrong))['all_checks_passed']
    for factor in plan['require']:
        wrong=copy.deepcopy(plan);wrong['require'].remove(factor)
        assert not grade_answer(item,json.dumps(wrong))['all_checks_passed']
    for malformed in ['{}','[]','{"require":[],"require":[]}','eval(1)','x'*5001]:
        assert grade_answer(item,malformed)['score']==0
    for key,value in [('require',['token_active','token_active']),('require',['__proto__']),('checks',access.CHECKS),('logic',True)]:
        wrong=copy.deepcopy(plan);wrong[key]=value
        assert grade_answer(item,json.dumps(wrong))['score']==0


def test_new_family_is_additional_and_original_tenant_tree_keeps_its_key():
    for grade in ['Junior','Middle']:
        items=[generate_case('security',grade,random.Random(seed)) for seed in range(100)]
        assert {x['family'] for x in items}=={access.FAMILY,'security:tenant_policy_repair'}
        assert {x['private']['condition']['variant'] for x in items if x['family']==access.FAMILY}==set(access.VARIANTS[grade])
        old=generate_legacy_case('security',grade,random.Random(1))
        assert old['version']=='work-cases-2026-v1'
        assert grade_answer(old,old['private']['reference_answer'])['all_checks_passed']
    for grade in ['Intern','Senior']:
        assert generate_case('security',grade,random.Random(1))['family']=='security:tenant_policy_repair'


@pytest.mark.parametrize('grade',['Junior','Middle'])
def test_signature_includes_context_versions_rubric_and_hides_keys(monkeypatch,grade):
    from backend import work_samples
    base=generate('security',grade,allow_neural=False);item=item_for(grade)
    def issued(value):
        monkeypatch.setattr(work_samples,'generate_case',lambda *_:copy.deepcopy(value))
        return add_journey(copy.deepcopy(base),'security',grade)
    original=issued(item)
    assert original['assessment_path']['rubric']==access.rubric(grade)
    assert original['items'][-1]['private']['rubric_criteria']==access.rubric(grade)
    public=public_item(original['items'][-2])
    assert 'private' not in public and 'reference_answer' not in json.dumps(public)
    for field in ['tenants','document','variant','model_version']:
        changed=copy.deepcopy(item);changed['private']['condition'][field]='changed-condition'
        assert issued(changed)['condition_signature']!=original['condition_signature']
    for field in ['version','rubric_version']:
        changed=copy.deepcopy(item);changed[field]='next-version'
        assert issued(changed)['condition_signature']!=original['condition_signature']
    changed=copy.deepcopy(item);changed['review_rubric'][0]['anchors'][2]+=' New observation.'
    assert issued(changed)['condition_signature']!=original['condition_signature']
    changed=copy.deepcopy(item);changed['id']='other-id'
    assert issued(changed)['condition_signature']==original['condition_signature']


def test_unknown_version_is_technical_before_answer_parsing():
    for field in ['version','model_version']:
        item=item_for()
        (item if field=='version' else item['private']['condition'])[field]='future-version'
        with pytest.raises(HTTPException) as error:
            grade_answer(item,'{')
        assert error.value.status_code==503
