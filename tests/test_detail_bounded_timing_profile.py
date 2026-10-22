"""Actual output-loop regression and separate event/as-of/window clocks."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

from ttf_news.analysis import (prepare_context, response_schema, _parse_result,
                               _validate_output_bounds, _json, count_tokens)
from ttf_news.semantics import (notice_settlement_timing,
    _check_notice_settlement_timing, _check_gassco_physical_state,
    _check_unknown_as_fact, source_fact_projection)
from ttf_news.prompts import system_prompt


ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def actual():
    fixture=json.loads((Path(__file__).parent/'fixtures/production_detail_bounded_timing_v2_3.json').read_text())
    context=fixture['input_snapshot'];prepared,meta=prepare_context(context,True)
    return fixture,context,prepared,meta


@pytest.mark.parametrize('detail',[False,True])
def test_native_risks_are_bounded_and_unique_without_limiting_directions(actual,detail):
    prepared,_=prepare_context(actual[1],detail)
    schema=response_schema(detail,prepared)
    assert schema['properties']['risk_flags']['maxItems']==2
    assert schema['properties']['risk_flags']['uniqueItems'] is True
    assert set(schema['$defs']['NewsImpact']['properties']['direction']['enum'])=={
        'extreme_up','up','flat','down','extreme_down'}
    for direction in ('extreme_up','up','flat','down','extreme_down'):
        assert direction in schema['$defs']['CloseForecast']['properties']['direction']['anyOf'][0]['enum']
    for risks in ([],['source gap'],['source gap','price anchor'],['same','same']):
        output={'risk_flags':risks};before=copy.deepcopy(output)
        _validate_output_bounds(output,detail)
        assert output==before
    for risks in (['same',' same '],['one','two','three']):
        with pytest.raises(ValueError,match='risk_flags'):
            _validate_output_bounds({'risk_flags':risks},detail)


def test_detail_arrays_have_finite_native_and_raw_limits_with_all_fact_rows(actual):
    schema=response_schema(True,actual[2]);props=schema['$defs']['DetailAnalysis']['properties']
    for field in ('new_facts','transmission','daily_balance','historical_comparisons',
                  'invalidation_conditions','limitations'):
        assert props[field]['maxItems']==2
        assert props[field]['uniqueItems'] is True
        if 'enum' not in props[field]['items']:
            assert props[field]['items']['maxLength']==280
        with pytest.raises(ValueError,match=field):
            _validate_output_bounds({'detail':{field:['[N0] first','[N1] second','[N2] third']}},True)
    assert props['references']['maxItems']==16
    assert len(source_fact_projection(actual[2])['operator_notices'])==2
    for field in ('new_facts','transmission','daily_balance','invalidation_conditions'):
        with pytest.raises(ValueError,match='280'):
            _validate_output_bounds({'detail':{field:['[N0]'+'a'*280]}},True)
    # Body arrays remain unaltered in the brief contract.
    _validate_output_bounds({'analysis':{'matched_terms':[str(i) for i in range(9)]}},False)


def test_actual_incomplete_risk_loop_is_preserved_and_rejected(actual):
    call=actual[0]['model_calls'][0];original=copy.deepcopy(call)
    assert call['done_reason']=='length' and call['eval_count']==3072
    assert call['raw_content'].count('输入未提供可用的前一同合约价格锚点')>20
    with pytest.raises(json.JSONDecodeError):
        _parse_result(call['raw_content'],True,[])
    assert call==original


def test_actual_brief_has_the_same_repeated_risk_loop_and_is_not_recovered():
    fixture=json.loads((Path(__file__).parent/'fixtures/production_brief_risk_loop_v2_3.json').read_text())
    call=fixture['model_call'];before=copy.deepcopy(call)
    assert call['done_reason']=='length' and call['eval_count']==3072
    assert call['raw_content'].count('来源gdelt')>20
    with pytest.raises(json.JSONDecodeError):
        _parse_result(call['raw_content'],False,[])
    assert call==before


def test_profile_reserves_detail_generation_budget_without_dropping_sources(actual):
    _,_,prepared,meta=actual
    assert (meta['context_window'],meta['input_token_ceiling'],meta['output_token_limit'])==(16384,11776,4096)
    assert meta['input_token_estimate']<=meta['input_token_limit']
    assert meta['input_token_ceiling']+meta['output_token_limit']+512<=meta['context_window']
    assert prepared['compression']['every_window_fact_card_retained']
    assert [row['id'] for row in prepared['window_news']]==['N1','N2']


def test_as_of_future_and_target_window_inside_are_independent(actual):
    cards={row['id']:row for row in actual[2]['window_news']}
    assert cards['N1']['settlement_timing']['as_of_relation']=='not_started'
    assert cards['N1']['settlement_timing']['start_vs_target_window']=='at_or_after_target_window_end'
    assert cards['N1']['settlement_timing']['notice_interval_overlaps_target_window'] is False
    assert cards['N2']['settlement_timing']['as_of_relation']=='not_started'
    assert cards['N2']['settlement_timing']['start_vs_target_window']=='starts_inside_target_window'
    assert cards['N2']['settlement_timing']['notice_interval_overlaps_target_window'] is True


@pytest.mark.parametrize('start,end,now,relation,future,overlap',[
    ('2026-10-20T15:15:00Z','2026-10-21T00:00:00Z','2026-10-20T15:15:00Z','starts_inside_target_window',False,True),
    ('2026-10-21T15:15:00Z','2026-10-22T00:00:00Z','2026-10-20T23:00:00Z','at_or_after_target_window_end',True,False),
    ('2026-10-20T12:00:00Z','2026-10-20T15:15:00Z','2026-10-20T23:00:00Z','starts_before_target_window',False,False),
    ('2026-10-20T12:00:00Z','2026-10-21T00:00:00Z','2026-10-20T23:00:00Z','starts_before_target_window',False,True),
])
def test_exact_time_boundaries_are_half_open(actual,start,end,now,relation,future,overlap):
    source=copy.deepcopy(actual[1]['window_news'][0]);source.update(event_start=start,event_end=end)
    timing=notice_settlement_timing(source,actual[1]['session'],now)
    assert timing['start_vs_target_window']==relation
    assert (timing['as_of_relation']=='not_started') is future
    assert timing['notice_interval_overlaps_target_window'] is overlap
    source['event_start']=start.replace('Z','+00:00')
    assert notice_settlement_timing(source,actual[1]['session'],now)==timing


@pytest.mark.parametrize('text',[
    '[N1]在本窗口之后生效；[N2]在本结算窗口内开始，但截至分析时点两项均尚未开始。',
    '[N1]若维护提前在本结算窗口内开始，则须重新分析。',
    '[N2]不能认定该维护在本窗口之后生效。',
    '[N2]维护在本窗口之后结束。',
    '[N2]维护结束时间在本窗口之后。',
])
def test_source_bound_correct_timing_and_hypotheses_are_allowed(actual,text):
    _check_notice_settlement_timing({'analysis':{'summary':text}},actual[1],actual[2])


@pytest.mark.parametrize('text,alias',[
    ('Troll两项计划维护均在本窗口之后生效。','N2'),
    ('[N1]与[N2]均在本窗口之后生效。','N2'),
    ('N1、N2均在本窗口之后生效。','N2'),
    ('N2所述维护和N1所述维护均在本窗口之后生效。','N2'),
    ('[N1]在本结算窗口内开始。','N1'),
    ('[N2]已经开始维护。','N2'),
])
def test_source_bound_wrong_window_or_already_started_are_rejected(actual,text,alias):
    with pytest.raises(ValueError,match=alias):
        _check_notice_settlement_timing({'analysis':{'summary':text}},actual[1],actual[2])


def test_future_event_denial_does_not_become_measured_global_no_loss(actual):
    ctx,prepared=actual[1:3]
    raw=json.loads(actual[0]['model_calls'][1]['raw_content']);before=copy.deepcopy(raw)
    _check_gassco_physical_state(raw,ctx,prepared)
    _check_unknown_as_fact(raw,ctx,prepared)
    with pytest.raises(ValueError,match='N2'):
        _check_notice_settlement_timing(raw,ctx,prepared)
    assert raw==before  # The old failed attempt stays unchanged.
    for text in ('Troll两项维护均尚未开始，尚未导致实际流量损失。',
                 '[N1]和[N2]维护计划截至当前尚未造成实际供气损失。'):
        out={'analysis':{'summary':text}}
        _check_gassco_physical_state(out,ctx,prepared)
        _check_unknown_as_fact(out,ctx,prepared)
    for text in ('Troll整体实际流量没有损失。','[N2]当前实际流量保持不变。',
                 '[N2]维护公告未对当前价格产生影响。',
                 '[N2]Troll未来维护公告已公布，另一处气田故障未导致实际流量损失。'):
        out={'analysis':{'summary':text}}
        with pytest.raises(ValueError):
            _check_gassco_physical_state(out,ctx,prepared)
            _check_unknown_as_fact(out,ctx,prepared)
    active=copy.deepcopy(ctx);active['as_of']='2026-10-21T04:00:00Z'
    active_prepared,_=prepare_context(active,True)
    with pytest.raises(ValueError):
        _check_gassco_physical_state({'analysis':{'summary':'[N2]这项维护尚未导致实际流量损失。'}},active,active_prepared)


def test_invalidation_conditions_do_not_assert_current_notice_timing(actual):
    _check_notice_settlement_timing({'detail':{'invalidation_conditions':['[N1]维护在本结算窗口内开始。']}},actual[1],actual[2])
    with pytest.raises(ValueError,match='N1'):
        _check_notice_settlement_timing({'detail':{'invalidation_conditions':['[N1]维护已经开始。']}},actual[1],actual[2])


def test_explicit_collective_facilities_bind_both_sources_and_ids_do_not_cross(actual):
    ctx=copy.deepcopy(actual[1]);ctx['window_news'][0]['title']=ctx['window_news'][0]['title'].replace('Troll','Nyhamna')
    prepared,_=prepare_context(ctx,True)
    for claim in ('Troll和Nyhamna两项维护均在本窗口之后生效。',
                  'Nyhamna与Troll两项维护均在本窗口之后生效。'):
        with pytest.raises(ValueError,match='N2'):
            _check_notice_settlement_timing({'analysis':{'summary':claim}},ctx,prepared)
    _check_notice_settlement_timing({'analysis':{'summary':'Nyhamna在本窗口之后生效，Troll在本窗口内开始。'}},ctx,prepared)
    _check_notice_settlement_timing({'analysis':{'summary':'若Troll改期至本窗口之后才开始，则须更新判断。'}},ctx,prepared)


def test_brief_archive_changes_only_supplied_unit_and_native_risk_bounds():
    archived=ROOT/'.runtime/source-archives/backend-43554/ttf_news/analysis.py'
    if not archived.is_file():
        pytest.skip('private historical runtime archive not in public source distribution')
    spec=importlib.util.spec_from_file_location('ttf_news._analysis_backend43554',archived)
    old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
    cases=json.loads((ROOT/'reports/repair_2026-10-20/semantic_regeneration_cases_v2_2.json').read_text())
    assert len(cases)==20
    for case in cases:
        context=case.get('candidate_context') or case['context']
        new_data,new_meta=prepare_context(context,reserve_tokens=32)
        old_data,old_meta=old.prepare_context(context,reserve_tokens=32)
        normalized=copy.deepcopy(new_data)
        quote=normalized.get('price_reference',{}).get('intraday')
        supplied=(context.get('price_reference') or {}).get('intraday') or {}
        if quote and 'unit' in supplied:
            assert quote.pop('unit')==supplied['unit']
        assert _json(normalized)==_json(old_data),case['id']
        assert new_meta['input_token_estimate']==old_meta['input_token_estimate']+count_tokens(_json(new_data))-count_tokens(_json(old_data))
        for field in ('input_token_limit','input_token_ceiling','context_window','output_token_limit'):
            assert new_meta[field]==old_meta[field],(case['id'],field)
        assert system_prompt(False)==old.system_prompt(False)
        new_schema=response_schema(False,new_data);old_schema=old.response_schema(False,old_data)
        assert new_schema['properties']['risk_flags']['maxItems']==2
        assert new_schema['properties']['risk_flags']['uniqueItems'] is True
        del new_schema['properties']['risk_flags']['maxItems']
        del new_schema['properties']['risk_flags']['uniqueItems']
        assert new_schema==old_schema,case['id']
