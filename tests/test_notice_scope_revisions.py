import copy
import json
import re

import pytest

from ttf_news.analysis import (
    _notice_scoped_clauses, _operator_evidence_prefixes, _validate_notice_source_facts,
    prepare_context, response_schema,
)
from ttf_news.semantics import collect_analysis_semantic_errors
from test_analysis import FakeAnalyst, context, result
from test_notice_source_facts import sources


def prepared_sources():
    prepared = sources()
    prepared['as_of'] = '2026-10-20T12:00:00Z'
    prepared['evidence_ledger'] = {'notice_facts': [
        {'id': 'N0', 'facility': 'Nyhamna', 'notice_type': '计划', 'unavailable_capacity': '19.8',
         'capacity_unit': 'mcm/d', 'state': 'active_notice', 'actual_flow_observation': '输入未提供独立实际流量计量'},
        {'id': 'N3', 'facility': 'Åsgard', 'notice_type': '非计划', 'unavailable_capacity': '5.2',
         'capacity_unit': 'mcm/d', 'state': 'active_notice', 'actual_flow_observation': '输入未提供独立实际流量计量'}]}
    return prepared


def check_evidence(text, prepared=None):
    prepared = prepared or prepared_sources()
    output = {'analysis': {'evidence': [text]}}
    before = copy.deepcopy(output)
    _validate_notice_source_facts(output, {}, prepared)
    assert output == before


def test_generated_mixed_notice_prefixes_keep_alias_across_semicolons():
    prepared = prepared_sources()
    prefixes = _operator_evidence_prefixes(prepared)
    n0 = next(value for value in prefixes if value.startswith('[N0]'))
    n3 = next(value for value in prefixes if value.startswith('[N3]'))
    check_evidence(n0 + '条件性供给风险，流量规模仍未知', prepared)
    check_evidence(n3 + '条件性供给风险，流量规模仍未知', prepared)
    check_evidence(n0 + '条件风险；' + n3 + '条件风险', prepared)
    scoped = list(_notice_scoped_clauses(n0 + '条件风险；' + n3 + '条件风险'))
    assert next(aliases for clause, aliases in scoped if clause == '非计划') == {'N3'}


@pytest.mark.parametrize('text,expected', [
    ('[N0]Nyhamna；非计划；公告不可用容量19.8mcm/d；TTF机制：条件风险', 'N0.*Planned'),
    ('[N3]Åsgard；计划停机；公告不可用容量5.2mcm/d；TTF机制：条件风险', 'N3.*Unplanned'),
    ('[N3]Åsgard；非计划；公告不可用容量19.8mcm/d；TTF机制：条件风险', 'N3.*unavailable.*5.2'),
    ('[N0]Nyhamna；计划；公告不可用容量5.2mcm/d；TTF机制：条件风险', 'N0.*unavailable.*19.8'),
])
def test_source_scoping_still_rejects_real_type_and_capacity_errors(text, expected):
    with pytest.raises(ValueError, match=expected):
        check_evidence(text)


def test_two_separately_bound_notices_in_one_clause_are_checked_individually():
    check_evidence('[N0]计划公告不可用容量19.8mcm/d，[N3]非计划公告不可用容量5.2mcm/d')
    with pytest.raises(ValueError, match='N3.*Unplanned'):
        check_evidence('[N0]计划公告不可用容量19.8mcm/d，[N3]计划停机，不可用容量5.2mcm/d')
    with pytest.raises(ValueError, match='N3.*unavailable.*5.2'):
        check_evidence('[N0]计划公告不可用容量19.8mcm/d，[N3]非计划公告不可用容量19.8mcm/d')
    # A joint reference remains a joint claim; no single notice establishes a sum.
    check_evidence('[N0]与[N3]合计25mcm/d容量约束；实际流量未核实')


def revision_prepared():
    prepared = prepared_sources()
    prepared['current_news']['revision_delta'] = {
        'previous_version': 2, 'previous_known_at': '2026-10-19T11:00:00Z',
        'comparison_status': 'compared', 'substantive_change': True,
        'changed_fields': {'unavailable_capacity': {'before': '20', 'after': '19.8'}},
        'capacity_changes': {'unavailable_capacity': {
            'before': '20', 'after': '19.8', 'delta': '-0.2', 'unit': 'mcm/d',
            'interpretation': 'announced_capacity_change_not_actual_flow', 'constraint_change': 'decreased'}}}
    return prepared


def test_verified_capacity_revision_is_bound_for_trigger_and_window_alias_without_new_loss():
    prepared = revision_prepared(); before = copy.deepcopy(prepared)
    prefixes = _operator_evidence_prefixes(prepared)
    n0 = next(value for value in prefixes if value.startswith('[N0]'))
    n6 = next(value for value in prefixes if value.startswith('[N6]'))
    assert '不可用容量修订：前20→后19.8mcm/d，容量差-0.2（非实物流量）' in n0
    assert n0.replace('[N0]', '[N6]') == n6
    assert '新增停产' not in n0 and '实际损失' not in n0
    for prefix in (n0, n6):
        check_evidence(prefix + '本次公告约束较前版降低，实际流量仍待核验', prepared)
        pattern = response_schema(False, prepared)['$defs']['Analysis']['properties']['evidence']['items']['pattern']
        assert re.fullmatch(pattern, prefix + '保留条件性供需判断')
    assert prepared == before


def test_capacity_revision_does_not_license_claiming_current_capacity_as_new_physical_loss():
    prepared = revision_prepared()
    good = {'news_impact': {'summary': '公告不可用容量由20修订为19.8mcm/d，约束变化为-0.2，非实物流量，实际流量未知'}}
    bad = {'news_impact': {'summary': 'Nyhamna本次新增停产19.8mcm/d，实物供气损失已发生'}}
    assert collect_analysis_semantic_errors(good, {}, prepared) == []
    assert any('公告不可用容量' in value for value in collect_analysis_semantic_errors(bad, {}, prepared))


@pytest.mark.parametrize('change', ['future_prior', 'unknown_prior', 'unknown_unit', 'metadata_only'])
def test_revision_prefix_does_not_invent_missing_time_unit_or_substantive_changes(change):
    prepared = revision_prepared(); revision = prepared['current_news']['revision_delta']
    if change == 'future_prior':
        revision['previous_known_at'] = '2026-10-21T11:00:00Z'
    elif change == 'unknown_prior':
        revision['previous_known_at'] = None
        revision['comparison_status'] = 'previous_version_unavailable'
    elif change == 'unknown_unit':
        revision['capacity_changes']['unavailable_capacity']['unit'] = None
    else:
        revision.update(substantive_change=False, changed_fields={}, capacity_changes={})
    prefix = next(value for value in _operator_evidence_prefixes(prepared) if value.startswith('[N0]'))
    assert '前20→' not in prefix and '容量差-0.2' not in prefix
    assert '未知' in prefix or '未提供' in prefix or '无实质事实变化' in prefix


def test_duration_only_revision_copies_dates_without_fabricating_capacity_change():
    prepared = revision_prepared(); revision = prepared['current_news']['revision_delta']
    revision.pop('capacity_changes')
    revision['changed_fields'] = {'event_end': {'before': '2026-10-24T06:00:00+0200',
                                              'after': '2026-11-26T06:00:00+0100'}}
    prefix = next(value for value in _operator_evidence_prefixes(prepared) if value.startswith('[N0]'))
    assert '结束时间修订：2026-10-24T06:00:00+0200→2026-11-26T06:00:00+0100' in prefix
    assert '前20' not in prefix and '容量差' not in prefix
    check_evidence(prefix + '本次为公告结束时间延长，实际流量待核实', prepared)


async def test_pricing_only_repair_does_not_invent_language_error():
    invalid = result(); invalid['analysis']['priced_in_comment'] = '当前消息已经完全计价'
    analyst = FakeAnalyst([json.dumps(invalid), json.dumps(result())])
    output = await analyst.analyze(context())
    request = analyst.calls[1][1]
    feedback = request['REPAIR_FEEDBACK']
    assert feedback['origin'] == 'local_validator' and feedback['is_evidence'] is False
    assert feedback['errors'] == output['_meta']['model_calls'][0]['validation_errors']
    assert 'UNTRUSTED_DRAFT' not in request
    assert invalid['analysis']['priced_in_comment'] not in json.dumps(request, ensure_ascii=False)
    repair = '\n'.join(feedback['errors'])
    assert '计价' in repair
    assert '全部正文用中文' not in repair and '核心分析文案必须为中文' not in repair


async def test_actual_english_failure_retains_chinese_repair_requirement():
    invalid = result(); invalid['analysis']['summary'] = 'The report describes an uncertain change in European natural gas supply.'
    analyst = FakeAnalyst([json.dumps(invalid), json.dumps(result())])
    await analyst.analyze(context())
    repair = analyst.calls[1][0].split('错误：', 1)[1]
    assert '核心分析文案必须为中文' in repair
