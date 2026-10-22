"""Frozen actual risk duplicates, unsupported reaction claims and repair headroom."""
import copy
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

import ttf_news.analysis as analysis
from ttf_news.analysis import (AnalysisError,ContextError,_json,_parse_result,
    _prepare_medium_repair,_validate_output_bounds,prepare_context,response_schema,
    REPAIR_DRAFT_INSTRUCTION)
from ttf_news.prompts import repair_prompt,system_prompt
from ttf_news.semantics import (_check_observed_absence_of_price_reaction,
    collect_analysis_semantic_errors)
from test_analysis import FakeAnalyst,context,result

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def actuals():
    return json.loads((Path(__file__).parent/'fixtures/final_output_scope_actuals_v2_3.json').read_text())


def raw(case,index=0):
    return case['attempts'][index]['response']['message']['content']


async def test_actual_cold_duplicate_risks_do_not_discard_correct_full_analysis(actuals):
    case=actuals['cold_heating_demand'];before=copy.deepcopy(case)
    analyst=FakeAnalyst([raw(case)])
    output=await analyst.analyze(case['input_snapshot'])
    assert len(analyst.calls)==1 and output['news_impact']['direction']=='up'
    assert output['close_forecast']['direction'] is None
    assert output['_meta']['model_risk_flags_normalization']=={
        'raw_count':2,'exact_unique_count':1,'duplicates_removed':1,
        'method':'exact_string_dedup_at_known_limitations','raw_preserved_in_model_calls':True}
    risk=json.loads(raw(case))['risk_flags'][0]
    assert output['risk_flags'].count(risk)==1
    assert output['_meta']['model_calls'][0]['raw_content']==raw(case)
    assert case==before and case['original_contract_passed'] is False


def test_risk_exception_keeps_max_two_and_other_raw_duplicates_hard(actuals):
    _validate_output_bounds({'risk_flags':['same','same']},False)
    for value,detail in [({'risk_flags':['same']*3},False),
                         ({'risk_flags':['same',' same ']},False),
                         ({'detail':{'new_facts':['same','same']}},True),
                         ({'detail':{'references':[{'id':'N0','url':''}]*2}},True)]:
        with pytest.raises(ValueError):_validate_output_bounds(value,detail)
    case=actuals['anchor_gap_supply_restore_40'];ctx=case['input_snapshot'];_,meta=prepare_context(ctx)
    # The first candidate has correct down/-1, while the second says down/+1.
    _parse_result(raw(case),False,meta['source_index'],ctx)
    with pytest.raises(ValueError,match='负的'):
        _parse_result(raw(case,1),False,meta['source_index'],ctx)
    assert json.loads(raw(case,1))['news_impact']['contextual_news_score']==1


@pytest.mark.parametrize('name,path',[
    ('future_planned_notice','news_impact.summary'),
    ('supply_loss_40_with_flat_baseline','close_forecast.basis'),
    ('anchor_real_manual_missing_prevday','news_impact.summary'),
])
def test_actual_accepted_price_zero_claims_are_rejected_without_changing_records(actuals,name,path):
    case=actuals[name];out=json.loads(raw(case,-1));before=copy.deepcopy(out)
    errors=collect_analysis_semantic_errors(out,case['input_snapshot'],case['attempts'][-1]['input'])
    assert any(path in e and '价格反应观测' in e for e in errors)
    assert out==before


@pytest.mark.parametrize('claim',[
    '该未来公告未导致当前供气损失或价格反应。',
    '该未来公告未造成实际流量损失及即时价格波动。',
    '截至目前无价格反应，计价不确定。',
    '价格没有即时反应。',
    '若无其他逆向消息，价格可能上行；但截至目前无价格反应。',
    '该建设消息未产生即时价格冲击。',
])
def test_explicit_price_absence_is_not_proved_by_future_flow_or_baseline(actuals,claim):
    case=actuals['supply_loss_40_with_flat_baseline'];output={'close_forecast':{'basis':claim}}
    with pytest.raises(ValueError,match='价格反应观测'):
        _check_observed_absence_of_price_reaction(output,case['input_snapshot'],case['attempts'][0]['input'])


@pytest.mark.parametrize('claim',[
    '无价格反应的数据。','无价格反应证据。','无价格反应观测资料。',
    '无法确认是否无价格反应。','未见价格反应。',
    '若没有价格反应，则须检查其他因素。',
    '预计即时价格影响有限。','预计无即时价格反应。',
    '当前已观察同合约价格基准持平，公告后的价格反应未知。',
    '该未来维护尚未导致实际流量损失；价格反应未知。',
])
def test_missing_observations_conditions_and_reference_levels_remain_distinct(actuals,claim):
    case=actuals['supply_loss_40_with_flat_baseline']
    _check_observed_absence_of_price_reaction({'analysis':{'summary':claim}},case['input_snapshot'],case['attempts'][0]['input'])


def test_known_reaction_and_cjk_adjacent_alias_are_scoped_to_source_and_time(actuals):
    case=actuals['supply_loss_40_with_flat_baseline'];ctx=copy.deepcopy(case['input_snapshot'])
    at=ctx['as_of'];source={'id':'observed','title':'Source reaction','summary':'该消息截至目前无价格反应。',
                           'source':'test_source','published_at':at,'first_seen_at':at,'timestamp_precision':'exact'}
    ctx['window_news']=[ctx['current_news'],source];prepared,_=prepare_context(ctx)
    # N1 is the trigger duplicate; N2 is the independent reaction observation.
    _check_observed_absence_of_price_reaction({'analysis':{'summary':'N2所述价格无反应。'}},ctx,prepared)
    with pytest.raises(ValueError):
        _check_observed_absence_of_price_reaction({'analysis':{'summary':'N0所述价格无反应。'}},ctx,prepared)
    source['summary']='输入没有价格反应数据。';ctx['window_news']=[ctx['current_news'],source];prepared,_=prepare_context(ctx)
    with pytest.raises(ValueError):
        _check_observed_absence_of_price_reaction({'analysis':{'summary':'N2所述价格无反应。'}},ctx,prepared)
    source['summary']='该消息截至目前无价格反应。';source['first_seen_at']='2026-10-20T12:20:00Z'
    ctx['window_news']=[ctx['current_news'],source];prepared,_=prepare_context(ctx)
    with pytest.raises(ValueError):
        _check_observed_absence_of_price_reaction({'analysis':{'summary':'N2所述价格无反应。'}},ctx,prepared)
    # Independently comparable observations must themselves be known as of.
    for available,expected in [(at,True),('2026-10-20T12:20:00Z',False)]:
        observed=copy.deepcopy(case['input_snapshot'])
        observed['price_reference']['announcement_price_reaction']={'source':'independent comparison','available_at':available,'observed_change':0}
        prepared,_=prepare_context(observed)
        if expected:
            _check_observed_absence_of_price_reaction({'analysis':{'summary':'截至目前无价格反应。'}},observed,prepared)
        else:
            with pytest.raises(ValueError):
                _check_observed_absence_of_price_reaction({'analysis':{'summary':'截至目前无价格反应。'}},observed,prepared)


def archive_analysis():
    archived=ROOT/'.runtime/source-archives/backend-51753/ttf_news/analysis.py'
    if not archived.is_file():
        pytest.skip('private historical runtime archive not in public source distribution')
    spec=importlib.util.spec_from_file_location('ttf_news._analysis_backend51753',archived)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_first_requests_all_twenty_add_only_explicit_supplied_unit_to_backend51753():
    old=archive_analysis()
    cases=json.loads((ROOT/'reports/repair_2026-10-20/semantic_regeneration_cases_v2_2.json').read_text())
    for case in cases:
        data,meta=prepare_context(case['candidate_context'],reserve_tokens=32)
        prior,previous=old.prepare_context(case['candidate_context'],reserve_tokens=32)
        normalized=copy.deepcopy(data)
        quote=normalized.get('price_reference',{}).get('intraday')
        supplied=(case['candidate_context'].get('price_reference') or {}).get('intraday') or {}
        if quote and 'unit' in supplied:
            assert quote.pop('unit')==supplied['unit']
        assert _json(normalized)==_json(prior),case['id']
        assert meta['input_token_estimate']==previous['input_token_estimate']+analysis.count_tokens(_json(data))-analysis.count_tokens(_json(prior))
        assert response_schema(False,data)==old.response_schema(False,prior),case['id']
        assert system_prompt(False)==old.system_prompt(False)
        for key in ('context_window','input_token_ceiling','output_token_limit'):
            assert meta[key]==previous[key]


async def test_fitting_second_request_preserves_original_request_bytes():
    old=archive_analysis();bad=result();bad['analysis']['summary']='This entirely English paragraph needs to be regenerated in Chinese.'
    responses=[json.dumps(bad),json.dumps(result())]
    class ArchivedFake(old.NewsAnalyst):
        def __init__(self):super().__init__();self.calls=[];self.outputs=list(responses)
        async def _request(self,system,data,detail):
            self.calls.append((system,copy.deepcopy(data),detail));return {'message':{'content':self.outputs.pop(0)},'prompt_eval_count':1200,'eval_count':550}
    old_fake=ArchivedFake();new_fake=FakeAnalyst(responses)
    await old_fake.analyze(context());out=await new_fake.analyze(context())
    assert len(new_fake.calls)==len(old_fake.calls)==2
    for old_call,new_call in zip(old_fake.calls,new_fake.calls):
        assert old_call[0]==new_call[0] and _json(old_call[1])==_json(new_call[1])
    assert 'repair_context_expansion' not in out['_meta']


async def test_actual_eni_source_only_second_request_preserves_all_sources_no_third_call(actuals):
    case=actuals['real_eni'];first=raw(case);analyst=FakeAnalyst([first,first])
    with pytest.raises(AnalysisError) as failure:await analyst.analyze(case['input_snapshot'])
    calls=failure.value.audit['model_calls'];assert len(calls)==2
    assert calls[0]['num_ctx']==16384 and calls[0]['num_predict']==3072
    assert calls[1]['num_ctx'] in {16384,24576} and calls[1]['num_predict']==3072
    assert calls[1]['input_token_estimate']<=calls[1]['input_token_ceiling']
    if calls[1]['num_ctx']==24576:
        assert calls[1]['repair_context_expansion']['reserved_tokens']==1024
    for part in ('current_news','window_news','evidence_ledger'):
        assert analyst.calls[1][1][part]==analyst.calls[0][1][part]
    assert 'UNTRUSTED_DRAFT' not in analyst.calls[1][1]
    assert analyst.calls[1][1]['REPAIR_FEEDBACK']['origin']=='local_validator'
    assert calls[0]['raw_content']==first and calls[1]['raw_content']==first


async def test_request_still_too_large_stops_explicitly_without_extra_model_call():
    real_prepare=analysis.prepare_context;bad=result();bad['analysis']['priced_in_comment']='已计价';analyst=FakeAnalyst([json.dumps(bad)])
    seen=[]
    def preparation(*args,**kwargs):
        seen.append(copy.deepcopy(kwargs))
        if len(seen)==1:return real_prepare(*args,**kwargs)
        raise ContextError('complete facts exceed budget')
    with patch.object(analysis,'prepare_context',side_effect=preparation),pytest.raises(ContextError) as failure:
        await analyst.analyze(context())
    assert len(analyst.calls)==1 and len(seen)==3
    assert seen[-1]['_repair_profile']==(24576,20480,3072)
    assert failure.value.audit['repair_context_expansion']['terminal']=='complete_request_exceeds_24k_profile'
    assert len(failure.value.audit['model_calls'])==1
