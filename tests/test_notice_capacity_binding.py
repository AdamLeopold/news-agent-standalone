"""Regression of actual 1508 live failures: field/interval scope, never labels."""
import copy
import json
from pathlib import Path

import pytest

from ttf_news.analysis import _validate_notice_source_facts, prepare_context
from ttf_news.semantics import collect_analysis_semantic_errors


@pytest.fixture
def actual():
    fixture=json.loads((Path(__file__).parent/'fixtures/live_same_facility_paired_capacity_1508.json').read_text())
    context=fixture['input_snapshot']
    prepared,_=prepare_context(context)
    return fixture,context,prepared


def prose_output(actual,claim):
    result=copy.deepcopy(actual[0]['attempts'][0]['result'])
    result['news_impact']['summary']=claim
    result['analysis']['summary']=claim
    return result


def test_actual_respectively_intervals_and_shared_unit_are_valid(actual):
    fixture,context,prepared=actual
    before=copy.deepcopy(fixture)
    for attempt in fixture['attempts']:
        _validate_notice_source_facts(attempt['result'],context,prepared)
    assert fixture==before
    assert collect_analysis_semantic_errors(fixture['attempts'][0]['result'],context,prepared)==[]
    errors=collect_analysis_semantic_errors(fixture['attempts'][1]['result'],context,prepared)
    assert any('价格反应观测' in error for error in errors)
    assert not any('实际流量状态' in error for error in errors)


@pytest.mark.parametrize('claim',[
    'Troll未来10月21日至22日和10月22日至23日分别导致13.0和7.6mcm/d容量不可用。',
    'Troll未来可用容量分别下降7.6mcm/d和13.0mcm/d。',
    'Troll未来可用容量分别为129.8和124.3mcm/d。',
    '[N0]可用容量下降7.6mcm/d，实际流量待核实。',
    '[N0]可用容量降至129.8mcm/d，实际流量待核实。',
    '[N2]不可用容量13.0mcm/d，实际流量待核实。',
])
def test_delta_level_and_same_facility_pairs_preserve_known_values(actual,claim):
    _validate_notice_source_facts(prose_output(actual,claim),actual[1],actual[2])


@pytest.mark.parametrize('claim',[
    'Troll未来10月22日至23日和10月21日至22日分别导致13.0和7.6mcm/d容量不可用。',
    'Troll未来可用容量分别下降8.6和13.0mcm/d。',
    'Troll未来10月24日至25日和10月21日至22日分别导致7.6和13.0mcm/d容量不可用。',
    '[N0]不可用容量13.0mcm/d。',
    '[N0]可用容量下降13.0mcm/d。',
    '[N0]可用容量降至13.0mcm/d。',
    'Troll两项计划维护导致未来可用容量下降20.6mcm/d。',
    'Troll两项未来维护合计容量下降20.6mcm/d。',
])
def test_wrong_binding_delta_or_nonoverlapping_sum_still_rejected(actual,claim):
    with pytest.raises(ValueError,match='容量|时段'):
        _validate_notice_source_facts(prose_output(actual,claim),actual[1],actual[2])


@pytest.mark.parametrize('claim',[
    '[N0]该未来维护公告尚未生效，未造成当前供气损失。',
    'Troll未来维护计划未造成当前供气损失，实际流量仍未知。',
])
def test_future_event_not_started_does_not_claim_total_flow_stable(actual,claim):
    errors=collect_analysis_semantic_errors(prose_output(actual,claim),actual[1],actual[2])
    assert not any('实际流量状态' in error or '不能证明未形成供需冲击' in error for error in errors)


@pytest.mark.parametrize('claim',[
    '[N0]未来维护未生效，但当前实际流量没有损失。',
    'Troll未来维护未生效，欧洲整体未造成当前供气损失。',
    '[N0]实际流量保持不变。',
])
def test_unknown_overall_flow_not_licensed_by_future_event(actual,claim):
    errors=collect_analysis_semantic_errors(prose_output(actual,claim),actual[1],actual[2])
    assert any('实际流量状态' in error or '不能证明' in error for error in errors)


def test_future_exception_requires_future_source_asof(actual):
    ctx=copy.deepcopy(actual[1]);ctx['as_of']='2026-10-22T08:00:00Z'
    prepared,_=prepare_context(ctx)
    out=prose_output(actual,'[N0]该未来维护公告未造成当前供气损失。')
    assert any('实际流量状态' in error for error in collect_analysis_semantic_errors(out,ctx,prepared))


@pytest.mark.parametrize('claim',[
    '[N0]新闻未产生价格反应。',
    '[N0]TTF机制：无价格反应。',
    '[N0]没有实际价格反应。',
])
def test_definite_zero_price_reaction_requires_observation(actual,claim):
    assert any('价格反应观测' in error for error in collect_analysis_semantic_errors(prose_output(actual,claim),actual[1],actual[2]))


@pytest.mark.parametrize('claim',[
    '[N0]未见价格反应，仍须核对公告前后可比反应。',
    '[N0]无价格反应证据，无法确认消息是否已计价。',
    '[N0]无法确认新闻未产生价格反应。',
    '[N0]若新闻没有产生价格反应，则需核对未来供需和预期差。',
])
def test_price_reaction_uncertainty_is_not_zero_fact(actual,claim):
    assert not any('价格反应观测' in error for error in collect_analysis_semantic_errors(prose_output(actual,claim),actual[1],actual[2]))


def test_true_known_price_reaction_supports_no_reaction_but_future_evidence_does_not(actual):
    ctx=copy.deepcopy(actual[1]);reaction={'source':'independent announcement comparison','observed_at':'2026-10-20T20:35:00Z','available_at':'2026-10-20T20:40:00Z','before':{'value':86.47,'observed_at':'2026-10-20T20:00:00Z'},'after':{'value':86.47,'observed_at':'2026-10-20T20:35:00Z'}}
    ctx['price_reference']['announcement_price_reaction']=reaction
    prepared,_=prepare_context(ctx);out=prose_output(actual,'[N0]新闻未产生价格反应。')
    assert not any('价格反应观测' in e for e in collect_analysis_semantic_errors(out,ctx,prepared))
    ctx['price_reference']['announcement_price_reaction']['available_at']='2026-10-21T20:40:00Z'
    prepared,_=prepare_context(ctx)
    assert any('价格反应观测' in e for e in collect_analysis_semantic_errors(out,ctx,prepared))


@pytest.mark.parametrize('claim',[
    '[N0]与[N2]两条未来维护合计容量下降20.6mcm/d。',
    '[N0]与[N2]未来合计不可用容量20.6mcm/d。',
    '[N0]与[N2]未来可用容量分别下降13.0和7.6mcm/d。',
    'Troll未来2027年10月22日至23日和2027年10月21日至22日分别导致7.6和13.0mcm/d容量不可用。',
])
def test_explicit_citations_nonoverlapping_sum_and_explicit_year_are_checked(actual,claim):
    with pytest.raises(ValueError,match='容量|时段'):
        _validate_notice_source_facts(prose_output(actual,claim),actual[1],actual[2])


def test_shared_unit_order_is_bound_to_distinct_entity_names(actual):
    context=copy.deepcopy(actual[1]);context['window_news'][1]['title']=context['window_news'][1]['title'].replace('Troll','Kollsnes')
    prepared,_=prepare_context(context)
    good=prose_output(actual,'Troll与Kollsnes未来可用容量分别下降7.6和13.0mcm/d。')
    _validate_notice_source_facts(good,context,prepared)
    bad=prose_output(actual,'Troll与Kollsnes未来可用容量分别下降13.0和7.6mcm/d。')
    with pytest.raises(ValueError,match='显式N引用顺序'):
        _validate_notice_source_facts(bad,context,prepared)


def test_simultaneous_independent_facilities_numeric_capacity_sum_remains_possible(actual):
    context=copy.deepcopy(actual[1]);context['window_news'][1]['title']=context['window_news'][1]['title'].replace('Troll','Independent plant')
    context['window_news'][1]['event_start']='2026-10-22T06:00:00+0200'
    context['window_news'][1]['event_end']='2026-10-23T06:00:00+0200'
    prepared,_=prepare_context(context)
    out=prose_output(actual,'[N0]与[N2]两项独立设施同一时段公告不可用容量合计20.6mcm/d；这不是实际供气量损失。')
    _validate_notice_source_facts(out,context,prepared)


@pytest.mark.parametrize('claim',[
    '[N0]与[N2]两条未来维护合计容量下降20.6mcm/d，若需求上升则气价可能涨。',
    'Troll两条未来维护合计容量下降20.6mcm/d，若需求上升则气价可能涨。',
])
def test_unrelated_later_condition_does_not_license_earlier_rate_sum(actual,claim):
    with pytest.raises(ValueError,match='不重叠时段'):
        _validate_notice_source_facts(prose_output(actual,claim),actual[1],actual[2])


def test_warning_against_nonoverlapping_sum_is_not_an_assertion(actual):
    out=prose_output(actual,'不能把[N0]与[N2]不同日期容量合计为20.6mcm/d。')
    _validate_notice_source_facts(out,actual[1],actual[2])


def test_semicolon_inherited_source_set_still_checks_nonoverlapping_sum(actual):
    bad=prose_output(actual,'[N0]与[N2]为两项未来计划维护；两项未来维护合计容量下降20.6mcm/d。')
    with pytest.raises(ValueError,match='不重叠时段'):
        _validate_notice_source_facts(bad,actual[1],actual[2])
    good=prose_output(actual,'[N2]与[N0]是未来公告；不可用容量分别为7.6和13.0mcm/d，未在该句指定各数值日期。')
    _validate_notice_source_facts(good,actual[1],actual[2])


def test_unbound_two_notice_sum_requires_identified_sources_not_empty_scope_bypass(actual):
    out=prose_output(actual,'两项未来维护合计容量下降20.6mcm/d。')
    with pytest.raises(ValueError,match='未绑定N来源与同一时段'):
        _validate_notice_source_facts(out,actual[1],actual[2])
    out=prose_output(actual,'不能把两项不同日期公告容量合计为20.6mcm/d。')
    _validate_notice_source_facts(out,actual[1],actual[2])


def test_explicit_nonoperator_source_quantity_not_rebound_to_gassco(actual):
    context=copy.deepcopy(actual[1]);context['current_news']={'id':'manual-other','source':'company','title':'Two independent trains','summary':'Both operating trains combined capacity is 20.6mcm/d.','published_at':context['as_of']}
    prepared,_=prepare_context(context)
    out=prose_output(actual,'[N0]两项独立设施公告容量合计20.6mcm/d。')
    _validate_notice_source_facts(out,context,prepared)
