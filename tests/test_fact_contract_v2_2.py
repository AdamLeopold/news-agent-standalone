"""Generation contract2.2: source calendar/categories, no directional defaults."""
import copy
import json
from pathlib import Path

import pytest

from ttf_news.analysis import _json, _validate_notice_source_facts, count_tokens, prepare_context, response_schema
from ttf_news.prompts import PROMPT_VERSION, system_prompt
from ttf_news.semantics import _check_source_fact_projection, source_fact_projection


def paired_fixture():
    return json.loads((Path(__file__).parent/'fixtures/live_same_facility_paired_capacity_1508.json').read_text())


def test_exact_source_calendar_and_type_are_copied_without_net_pressure():
    fixture=paired_fixture();context=fixture['input_snapshot'];before=copy.deepcopy(context)
    prepared,_=prepare_context(context)
    rows=prepared['evidence_ledger']['notice_facts'];facts=source_fact_projection(prepared)
    assert [row['id'] for row in rows]==['N0','N2']
    assert rows[0]['facility_type']=='Gas production field（气田/生产设施）'
    assert rows[1]['facility_type']=='Other（来源未给具体类别）'
    assert facts['operator_notices'][0]['period_utc']==['2026-10-22T04:00Z','2026-10-23T04:00Z']
    assert facts['operator_notices'][1]['period_utc']==['2026-10-21T04:00Z','2026-10-22T04:00Z']
    assert facts['non_overlapping_notice_ids']==[['N0','N2']]
    assert '不是尚未计价或价格无反应' in facts['pricing_observation']['pricing_status']
    assert context==before
    assert not any(key in _json(facts) for key in ['"direction"','"contextual_news_score"','看涨','看跌'])


def test_relation_requires_complete_nonoverlapping_same_facility_intervals():
    from ttf_news.analysis import _notice_nonoverlap_relations
    rows=[{'id':'N0','facility':'Troll','event_start_utc':'2026-10-21T04:00:00Z','event_end_utc':'2026-10-22T04:00:00Z'},
          {'id':'N2','facility':'Troll','event_start_utc':'2026-10-22T04:00:00Z','event_end_utc':'2026-10-23T04:00:00Z'}]
    assert _notice_nonoverlap_relations(rows)[0]['same_time_rate_sum_supported'] is False
    for change in [{'event_start_utc':'2026-10-21T12:00:00Z'},{'event_end_utc':None},{'facility':'Nyhamna'}]:
        modified=copy.deepcopy(rows);modified[1].update(change)
        assert _notice_nonoverlap_relations(modified)==[]


def test_new_const_fact_table_exact_while_legacy_table_keeps_old_fields():
    context=paired_fixture()['input_snapshot'];prepared,_=prepare_context(context);new=source_fact_projection(prepared)
    _check_source_fact_projection({'source_fact_checks':new},prepared)
    for path,value in [('facility_type','Pipeline（管道）'),('period_utc',['2027-10-22T04:00:00Z','2027-10-23T04:00:00Z'])]:
        wrong=copy.deepcopy(new);wrong['operator_notices'][0][path]=value
        with pytest.raises(ValueError,match='事实表不一致'):_check_source_fact_projection({'source_fact_checks':wrong},prepared)
    legacy=copy.deepcopy(paired_fixture()['attempts'][0]['result']['source_fact_checks'])
    _check_source_fact_projection({'source_fact_checks':legacy},prepared)
    legacy['operator_notices'][0]['unavailable_capacity']='20.6'
    with pytest.raises(ValueError,match='事实表不一致'):_check_source_fact_projection({'source_fact_checks':legacy},prepared)
    schema=response_schema(False,prepared)
    assert schema['properties']['source_fact_checks']['const']==new
    assert legacy!=schema['properties']['source_fact_checks']['const']


def test_risk_enum_is_observational_but_all_directions_and_trader_risks_remain_free():
    prepared,_=prepare_context(paired_fixture()['input_snapshot']);schema=response_schema(True,prepared)
    risk=schema['properties']['risk_flags']['items']
    assert 'enum' in risk and all('尚未计价' not in option and '未产生价格反应' not in option for option in risk['enum'])
    assert '未来公告未计价' not in risk['enum']
    assert schema['$defs']['NewsImpact']['properties']['direction']['enum']==['extreme_up','up','flat','down','extreme_down']
    assert 'enum' not in schema['$defs']['CloseForecast']['properties']['extreme_move_risk']
    assert all('enum' not in node for node in schema['$defs']['Scenarios']['properties'].values())


def test_first_prompt_prioritizes_same_facts_all_fields_and_keeps_symmetric_directions():
    assert PROMPT_VERSION=='ttf-news-standalone-2.3'
    prompt=system_prompt()
    assert prompt.index('统一事实契约')<prompt.index('决策顺序')
    for text in ['所有summary','risk_flags','不重叠UTC时段','分子分母各须有N来源','不是新闻、价格或历史证据']:
        if text=='不是新闻、价格或历史证据':continue  # Existing retry draft rule unchanged.
        assert text in prompt
    assert '供应减少' in prompt and '供应增加' in prompt
    assert '强制涨跌' in prompt and '所有五档方向保持可选' in prompt


def test_actual_pilot_multi_amount_summary_is_not_excused_by_daily_list():
    pilot=json.loads((Path(__file__).parent/'fixtures/live_no_draft_pilot_69c.json').read_text())
    context=pilot['input_snapshot'];prepared,_=prepare_context(context)
    result=pilot['raw_result'];before=copy.deepcopy(result)
    assert '合计约20.6' in result['news_impact']['summary']
    with pytest.raises(ValueError,match='不重叠时段'):_validate_notice_source_facts(result,context,prepared)
    assert result==before


def test_source_period_mean_and_integrated_quantity_keep_correct_units():
    fixture=paired_fixture();context=fixture['input_snapshot'];prepared,_=prepare_context(context)
    for claim in ['Troll两项公告时段加权平均不可用容量10.3mcm/d。',
                  'Troll两项公告时段加权平均可用容量127.05mcm/d。',
                  'Troll两项公告时段加权平均技术容量137.35mcm/d。',
                  'Troll两项公告时段的不可用容量分别7.6和13.0mcm/d，不把相邻时段相加成同一速率。',
                  '[N0]与[N2]相邻两天容量积分对应20.6百万立方米，非同一时刻减供。']:
        out={'analysis':{'summary':claim}};_validate_notice_source_facts(out,context,prepared)
    with pytest.raises(ValueError,match='加权平均容量'):
        _validate_notice_source_facts({'analysis':{'summary':'Troll两项公告时段加权平均不可用容量12mcm/d。'}},context,prepared)


def test_natural_daily_rates_bind_dates_without_literal_respectively():
    fixture=paired_fixture();context=fixture['input_snapshot'];prepared,_=prepare_context(context)
    good='截至2026-10-20，Troll将于10月21-22日因年度维护导致13.0mcm/d容量不可用，10月22-23日再有7.6mcm/d不可用，为未来容量约束。'
    original={'analysis':{'summary':good}};before=copy.deepcopy(original)
    _validate_notice_source_facts(original,context,prepared)
    assert original==before
    for wrong in [good.replace('13.0mcm/d','7.6mcm/d').replace('再有7.6','再有13.0'),
                  good.replace('10月21-22','2027年10月21-22'),
                  good.replace('为未来容量约束','合计约20.6mcm/d，为未来容量约束')]:
        with pytest.raises(ValueError,match='容量|时段|日期|年份'):
            _validate_notice_source_facts({'analysis':{'summary':wrong}},context,prepared)
