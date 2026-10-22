import copy
import json
import re

import pytest

from ttf_news.analysis import _operator_evidence_prefixes, _parse_result, prepare_context, response_schema
from ttf_news.prompts import SHORT_SHAPE
from test_generation_boundaries import timing_context


def operator_prepared():
    return {
        'current_news': {'id': 'N0', 'source': 'offshore_energy'},
        'window_news': [{'id': 'N3', 'source': 'gassco'}, {'id': 'N7', 'source': 'gassco'},
                        {'id': 'N2', 'source': 'gassco'}],
        'historical_events': [{'id': 'E2', 'facts': '独立历史事实'}],
        'evidence_ledger': {'notice_facts': [
            {'id': 'N3', 'facility': 'Åsgard', 'notice_type': '非计划',
             'unavailable_capacity': 5.2, 'capacity_unit': 'mcm/d', 'state': 'active_notice',
             'cause_original': 'Yearly maintenance', 'actual_flow_observation': '输入未提供独立实际流量计量'},
            {'id': 'N7', 'facility': 'Nyhamna', 'notice_type': '计划',
             'unavailable_capacity': 19.8, 'capacity_unit': 'mcm/d', 'state': 'active_notice',
             'actual_flow_observation': '输入未提供独立实际流量计量'},
            {'id': 'N2', 'facility': 'Troll A (test)', 'notice_type': '非计划',
             'unavailable_capacity': 6.6, 'capacity_unit': 'mcm/d',
             'state': 'announced_end_elapsed_unverified_flow',
             'actual_flow_observation': '输入未提供独立实际流量计量'}]}}


def evidence_nodes(schema):
    for definition, fields in [('NewsImpact', ('bullish_evidence', 'bearish_evidence', 'neutralizing_evidence')),
                              ('Analysis', ('evidence',))]:
        for field in fields:
            yield schema['$defs'][definition]['properties'][field]['items']


def test_operator_evidence_copies_each_sources_type_capacity_state_and_flow():
    prepared = operator_prepared(); before = copy.deepcopy(prepared)
    prefixes = _operator_evidence_prefixes(prepared)
    n3 = next(prefix for prefix in prefixes if prefix.startswith('[N3]'))
    n7 = next(prefix for prefix in prefixes if prefix.startswith('[N7]'))
    ended = next(prefix for prefix in prefixes if prefix.startswith('[N2]'))
    assert n3 == '[N3]Åsgard；非计划；公告不可用容量5.2mcm/d；当前容量公告；实流未知；TTF机制：'
    assert n7 == '[N7]Nyhamna；计划；公告不可用容量19.8mcm/d；当前容量公告；实流未知；TTF机制：'
    assert '公告时段已过，当前容量未知' in ended
    assert 'Yearly maintenance' not in n3  # A cause cannot replace notice type.
    for node in evidence_nodes(response_schema(False, prepared)):
        assert re.fullmatch(node['pattern'], n3 + '容量约束可能影响欧洲供气，流量规模仍待确认')
        assert re.fullmatch(node['pattern'], n7 + '该计划公告持续，未发现本次新增实物损失的计量')
        assert re.fullmatch(node['pattern'], ended + '当前流量状态未知，不能当作仍在持续的已验证损失')
        assert re.fullmatch(node['pattern'], n3.replace('非计划', '计划') + '供需机制') is None
        assert re.fullmatch(node['pattern'], n3.replace('5.2', '19.8') + '供需机制') is None
        assert re.fullmatch(node['pattern'], '[N3]计划不可用容量19.8mcm/d，形成某种压力') is None
    assert prepared == before


def test_bound_facts_do_not_template_mechanism_or_restrict_five_directions():
    prepared = operator_prepared(); prefixes = _operator_evidence_prefixes(prepared)
    prefix = next(value for value in prefixes if value.startswith('[N3]'))
    for forbidden in ('看涨', '看跌', '上涨', '下跌', '判平'):
        assert forbidden not in prefix
    assert prefix.endswith('TTF机制：')
    for node in evidence_nodes(response_schema(False, prepared)):
        for mechanism in ('TTF看涨情景须与可核实供需联系', 'TTF看跌情景须有反向机制',
                          '净影响可能中和，仍需其他证据', '具体价格方向尚不能由容量推定'):
            assert re.fullmatch(node['pattern'], prefix + mechanism)
    schema = response_schema(False, prepared)
    assert schema['$defs']['NewsImpact']['properties']['direction']['enum'] == [
        'extreme_up', 'up', 'flat', 'down', 'extreme_down']
    close_options = schema['$defs']['CloseForecast']['properties']['direction']['anyOf']
    assert next(option['enum'] for option in close_options if 'enum' in option) == [
        'extreme_up', 'up', 'flat', 'down', 'extreme_down']
    assert any(option.get('type') == 'null' for option in close_options)


def test_other_news_and_history_keep_generic_valid_source_prefixes_only():
    schema = response_schema(False, operator_prepared())
    for node in evidence_nodes(schema):
        for text in ('[N0]来源报道未来合作，当前交付未提供', '[E2]历史类比需要说明时点与规模差异'):
            assert re.fullmatch(node['pattern'], text)
        for text in ('没有来源的运营判断', '[N99]输入没有该新闻', '[E99]输入没有该事件'):
            assert re.fullmatch(node['pattern'], text) is None


def test_current_window_alias_cannot_bypass_operator_facts_or_duplicate_input():
    prepared = operator_prepared()
    prepared['current_news'] = {'id': 'N0', 'source': 'gassco', 'same_as_window_news_id': 'N1'}
    prepared['window_news'].append({'id': 'N1', 'same_as_current_news_id': 'N0'})
    prepared['evidence_ledger']['notice_facts'].append({
        'id': 'N0', 'facility': 'Kollsnes', 'notice_type': '计划', 'unavailable_capacity': 20,
        'capacity_unit': 'mcm/d', 'state': 'scheduled_notice',
        'actual_flow_observation': '输入未提供独立实际流量计量'})
    before = copy.deepcopy(prepared)
    prefixes = _operator_evidence_prefixes(prepared)
    n0 = next(prefix for prefix in prefixes if prefix.startswith('[N0]'))
    n1 = next(prefix for prefix in prefixes if prefix.startswith('[N1]'))
    assert n0.replace('[N0]', '[N1]') == n1
    assert '未来容量公告' in n0
    for node in evidence_nodes(response_schema(False, prepared)):
        assert re.fullmatch(node['pattern'], n1 + '该未来容量安排的预期效应须条件分析')
        assert re.fullmatch(node['pattern'], '[N1]当作当前已确认停供') is None
    assert prepared == before


def test_missing_capacity_is_not_zero_and_actual_flow_statement_is_not_unknown():
    prepared = operator_prepared(); row = prepared['evidence_ledger']['notice_facts'][0]
    row.pop('unavailable_capacity'); row.pop('capacity_unit')
    row['state'] = 'dismissed_notice'
    row['actual_flow_observation'] = '原文含实际流量陈述，数值和核验状态见该N原文'
    prefix = next(value for value in _operator_evidence_prefixes(prepared) if value.startswith('[N3]'))
    assert '公告不可用容量未提供' in prefix and '容量0' not in prefix
    assert '仅该公告撤销' in prefix and '实流陈述见原文' in prefix and '实流未知' not in prefix


def test_notice_patterns_have_one_bounded_body_and_respect_total_native_limit():
    prepared = operator_prepared(); prefixes = _operator_evidence_prefixes(prepared)
    longest = max(prefixes, key=len)
    for node in evidence_nodes(response_schema(True, prepared)):
        assert node['maxLength'] == 450
        assert '*' not in node['pattern'] and '+' not in node['pattern']
        assert node['pattern'].count('{') == 1
        assert re.fullmatch(node['pattern'], longest + '事' * (450 - len(longest)))
        assert re.fullmatch(node['pattern'], longest + '事' * (451 - len(longest))) is None
        for unsafe in ('"', '\\', '\n', '\x00', '\x7f'):
            assert re.fullmatch(node['pattern'], longest + '事实' + unsafe) is None


def test_nonoperator_only_prepared_context_keeps_native_evidence_schema():
    prepared = {'current_news': {'id': 'N0', 'source': 'manual'}, 'evidence_ledger': {'notice_facts': []}}
    assert _operator_evidence_prefixes(prepared) == []
    assert all('pattern' not in node for node in evidence_nodes(response_schema(False, prepared)))


@pytest.mark.parametrize('section,field', [('news_impact','bullish_evidence'),
                                          ('news_impact','bearish_evidence'),
                                          ('news_impact','neutralizing_evidence'),
                                          ('analysis','evidence')])
@pytest.mark.parametrize('mechanism', ['该未来容量安排仅支持条件性供给预期，当前流量未知',
                                     '具体供需方向仍需后续事实，当前价格影响尚不确定'])
def test_generated_operator_prefix_satisfies_same_evidence_marker_contract(section, field, mechanism):
    context = timing_context()
    context['current_news'].update(source='gassco',title='Gassco Troll: Planned unavailability',
        summary='Future unavailable capacity: 20 mcm/d. Actual flow is not confirmed.',
        unavailable_capacity=20,capacity_unit='mcm/d',event_status='Active',
        event_start='2026-10-22T10:00:00Z',event_end='2026-10-23T10:00:00Z')
    prepared, meta = prepare_context(context)
    prefix = next(value for value in _operator_evidence_prefixes(prepared) if value.startswith('[N0]'))
    evidence = prefix + mechanism
    node = response_schema(False, prepared)['$defs']['NewsImpact' if section=='news_impact' else 'Analysis']['properties'][field]['items']
    assert re.fullmatch(node['pattern'], evidence)
    assert '→' not in mechanism and '->' not in mechanism and 'TTF' not in mechanism
    output = copy.deepcopy(SHORT_SHAPE)
    output['news_impact'].update(summary='该未来容量公告不等于当前实物流量损失，预期影响仍需后续事实',
                                bullish_evidence=[],bearish_evidence=[],neutralizing_evidence=[])
    output['close_forecast'].update(summary='现有资料形成条件性判断，后续供需变化仍须持续观察',
                                    basis='同合约价格锚点与当前已知事实共同作为条件判断的依据')
    output['analysis'].update(summary='未来容量安排须与当期实际供需区分，当前实物流量仍未知',
                              evidence=[evidence],priced_in_comment='缺少独立可比价格反应，无法确认是否已计价')
    output[section][field] = [evidence]
    before = copy.deepcopy(output)
    parsed = _parse_result(json.dumps(output),False,meta['source_index'],context)
    assert parsed[section][field] == [evidence]
    assert output == before
    # Reproduce the concrete old conflict without changing stored raw results.
    old = copy.deepcopy(output)
    old['analysis']['evidence'] = [evidence.replace('TTF机制：','机制：')]
    old[section][field] = [evidence.replace('TTF机制：','机制：')]
    with pytest.raises(ValueError,match='evidence必须含事实与TTF机制'):
        _parse_result(json.dumps(old),False,meta['source_index'],context)


def test_forecast_scope_retains_valid_anchor_despite_unknown_announcement_reaction():
    context = timing_context(); before = copy.deepcopy(context)
    prepared, _ = prepare_context(context)
    card = prepared['evidence_ledger']['pricing_observation']; use = card['forecast_use']
    assert not card['announcement_reaction_evidence_available']
    assert card['current_news_timing']['quote_relation'] == 'before_publication'
    assert use['same_contract_anchor_available'] and use['same_contract_market_baseline_available']
    assert use['reaction_scope_is_not_forecast_veto']
    assert '条件性收盘预判' in use['interpretation'] and '不自动判平或不预测' in use['interpretation']
    assert context == before


def test_forecast_scope_does_not_fabricate_missing_or_cross_contract_anchor():
    context = timing_context(); context['price_reference']['market_baseline']['contract'] = 'another-contract'
    prepared, _ = prepare_context(context); card = prepared['evidence_ledger']['pricing_observation']
    assert card['forecast_use']['same_contract_anchor_available']
    assert not card['forecast_use']['same_contract_market_baseline_available']
    assert not card['market_baseline_available']
    for prior in (None, 0, float('inf')):
        context = timing_context(); context['price_reference']['previous_price'] = prior
        # prepare_context correctly rejects nonfinite JSON before this scope;
        # use the pure input-card helper to exercise invalid scalar handling.
        from ttf_news.analysis import _pricing_observation_card
        card = _pricing_observation_card(context['price_reference'], [], {})
        assert not card['forecast_use']['same_contract_anchor_available']
        assert not card['forecast_use']['same_contract_market_baseline_available']


def test_all_scenarios_require_bounded_explicit_conditions_without_direction_template():
    schema = response_schema(True)
    for field in ('bullish', 'base', 'bearish'):
        node = schema['$defs']['Scenarios']['properties'][field]
        pattern = node['pattern']
        assert node['minLength'] == 12 and node['maxLength'] == 600
        assert '*' not in pattern and '+' not in pattern and pattern.count('{') == 1
        for text in ('若欧洲供气恢复持续，TTF下行压力可能增强',
                     '如果需求上升超过供给增加，原有判断可能反转',
                     '一旦累积事实继续，后续价格预判仍需核对'):
            assert re.fullmatch(pattern, text)
        for text in ('欧洲供应增加形成下行压力，维持当日判断', '若需求下降', '若' + '事' * 601):
            assert re.fullmatch(pattern, text) is None
        assert re.fullmatch(pattern, '若' + '事' * 11)
        assert re.fullmatch(pattern, '如果' + '事' * 598)
        for unsafe in ('"', '\\', '\n', '\x00', '\x7f'):
            assert re.fullmatch(pattern, '若欧洲供气恢复持续，供需判断待核实' + unsafe) is None


def test_detail_limitations_are_current_price_history_coverage_facts_not_missing_flow_template():
    context = timing_context()
    context['coverage'] = {'sources': [{'id': 'eia', 'status': 'failed'}, {'id': 'gassco', 'status': 'ok'}]}
    prepared, _ = prepare_context(context, True)
    options = response_schema(True, prepared)['$defs']['DetailAnalysis']['properties']['limitations']['items']['enum']
    assert any('独立可比价格反应' in value for value in options)
    assert any('sigma60' in value for value in options)
    assert any('没有独立可比较的历史事件' in value for value in options)
    assert any('来源eia' in value and '最近采集失败' in value for value in options)
    assert not any('gassco' in value for value in options)
    assert not any('实际流量' in value or value.startswith('current_news') for value in options)
    assert not any('未提供可用的前一同合约价格锚点' in value or '盘中变化基准' in value for value in options)
    assert all(value.startswith(('price_reference:', 'historical_events:', 'coverage:')) for value in options)


def test_detail_limitations_have_time_scope_fallback_when_no_proven_gap_exists():
    prepared = {'as_of': '2026-10-20T12:00:00Z',
                'price_reference': {'verified': True, 'sigma60': 1.2, 'announcement_price_reaction': {'known': True}},
                'historical_events': [{'id': 'E1', 'retrieval_role': 'historical_analogue'}],
                'evidence_ledger': {'pricing_observation': {'forecast_use': {
                    'same_contract_anchor_available': True, 'same_contract_market_baseline_available': True}}}}
    before = copy.deepcopy(prepared)
    node = response_schema(True, prepared)['$defs']['DetailAnalysis']['properties']['limitations']['items']
    assert node['enum'] == ['as_of:分析证据范围截至2026-10-20T12:00:00Z，后续变化需另行观察']
    assert 'pattern' not in node and prepared == before
