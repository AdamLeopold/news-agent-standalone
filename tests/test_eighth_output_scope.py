"""A future window-end knowledge state is not an as-of observation."""
import copy,hashlib,json
from pathlib import Path
import pytest
from ttf_news import analysis as a,semantics as s
from test_future_capacity_pressure_scope import captured_legacy_prepared
@pytest.fixture(scope='module')
def actual():
 p=Path(__file__).parent/'actual_fixture.json'
 if not p.is_file():p=Path(__file__).parent/'fixtures/eighth_output_scope_actual_v2_3.json'
 row=json.loads(p.read_text());assert hashlib.sha256(row['raw_content'].encode()).hexdigest()==row['raw_sha256'];return row

def setup(row):
 ctx=copy.deepcopy(row['input_snapshot']);p,_=a.prepare_context(ctx);return ctx,p


def test_exact_accepted_eighth_raw_is_rejected_without_rewriting_it(actual):
 ctx,p=setup(actual);body=json.loads(actual['raw_content']);before=copy.deepcopy(body)
 with pytest.raises(ValueError,match='截至未来窗末'):s._check_notice_settlement_timing(body,ctx,p)
 assert any('截至未来窗末' in e for e in s.collect_analysis_semantic_errors(body,ctx,p))
 assert body==before and actual['original_status']=='completed'

@pytest.mark.parametrize('claim',[
 '截至窗口结束时尚未确认实际流量损失。',
 '截至本结算窗口结束时仍未核实实际流量损失。',
 '截至收盘时仍未确认实际供气损失。',
 '若需求增加，但截至窗口结束时尚未确认实际流量损失。',
])
def test_future_deadline_epistemic_status_is_not_known(actual,claim):
 ctx,p=setup(actual)
 with pytest.raises(ValueError,match='截至未来窗末'):s._check_notice_settlement_timing({'analysis':{'summary':claim}},ctx,p)

@pytest.mark.parametrize('claim',[
 '截至分析时点尚未确认实际流量损失。',
 '截至as_of仍未确认实际流量损失。',
 '若截至收盘仍未确认实际流量损失，则重新评估条件预测。',
 '如果截至本结算窗口结束时仍未核实实际流量损失，则判断须更新。',
 '不能断言截至窗口结束时仍未确认实际流量损失。',
 '截至上一结算窗口结束时尚未确认实际流量损失。',
 '截至前一交易日收盘时尚未确认实际流量损失。',
 '预计截至收盘时仍未核实实际流量损失。',
])
def test_asof_conditions_warnings_and_explicit_past_window_are_distinct(actual,claim):
 ctx,p=setup(actual);s._check_notice_settlement_timing({'analysis':{'summary':claim}},ctx,p)

@pytest.mark.parametrize('now', ['2026-10-21T15:15:00Z','2026-10-21T16:00:00Z'])
def test_target_window_that_has_already_ended_is_not_future(actual,now):
 ctx,p=setup(actual);ctx['as_of']=now;p['as_of']=now
 s._check_notice_settlement_timing({'analysis':{'summary':'截至窗口结束时尚未确认实际流量损失。'}},ctx,p)


def test_field_condition_can_omit_if_but_not_summary_observation(actual):
 ctx,p=setup(actual);claim='截至收盘时仍未确认实际流量损失'
 s._check_notice_settlement_timing({'detail':{'invalidation_conditions':[claim]}},ctx,p)
 with pytest.raises(ValueError):s._check_notice_settlement_timing({'analysis':{'summary':claim}},ctx,p)


def test_controlled_asof_wording_keeps_whole_body_and_up_labels_valid(actual):
 ctx,p=setup(actual);body=json.loads(actual['raw_content']);body['news_impact']['summary']=body['news_impact']['summary'].replace('截至窗口结束时','截至分析时点')
 assert s.collect_analysis_semantic_errors(body,ctx,p)==[]
 assert body['news_impact']['direction']=='up' and body['close_forecast']['direction']=='up'



def detail_setup(actual):
 row=actual['detail_actual'];ctx=copy.deepcopy(row['input_snapshot']);p,_=a.prepare_context(ctx,True);return row,ctx,p


def test_actual_detail_first_allocated_sources_are_valid_and_second_field_error_stays(actual):
 row,ctx,new_prepared=detail_setup(actual);p,_=captured_legacy_prepared('eighth_detail',ctx)
 body=json.loads(row['raw_content'][0]);before=copy.deepcopy(body)
 a._validate_notice_source_facts(body,ctx,p)
 assert s.collect_analysis_semantic_errors(body,ctx,p)==[]
 assert body==before and row['original_status']=='failed'
 with pytest.raises(ValueError):a._validate_notice_source_facts(json.loads(row['raw_content'][1]),ctx,p)
 # The old generation's auxiliary table must not be passed off as a new
 # projection contract; the legacy body itself remains byte-for-byte intact.
 with pytest.raises(ValueError,match='source_fact_checks与本次已知输入'):
  s._check_source_fact_projection(body,new_prepared)

@pytest.mark.parametrize('claim',[
 '[N0]Troll维护导致7.6mcm/d容量不可用，另有13mcm/d的Other设施在窗口内开始。',
 'N0所述Troll维护导致7.6mcm/d容量不可用，另有13mcm/d的Other设施在窗口内开始。',
 'Troll维护导致7.6mcm/d容量不可用，另有20mcm/d的Other设施在窗口内开始。',
 'Troll维护导致7.6mcm/d容量不可用，另有13mcm/d的可用容量在窗口内开始。',
 'Troll维护导致7.6mcm/d容量不可用，另有13mcm/d的Other设施在窗口之后开始。',
 'Troll维护导致7.6mcm/d容量不可用，另有13mcm/d的Other设施在窗口内开始，合计20.6mcm/d。',
])
def test_allocated_summary_does_not_waive_N_value_field_time_or_sum(actual,claim):
 row,ctx,p=detail_setup(actual);body=json.loads(row['raw_content'][0])
 for section in ['news_impact','analysis']:body[section]['summary']=claim
 with pytest.raises(ValueError):a._validate_notice_source_facts(body,ctx,p)


def test_separate_capacity_does_not_prove_actual_flow_loss(actual):
 row,ctx,p=detail_setup(actual);body=json.loads(row['raw_content'][0]);body['news_impact']['summary']='Troll维护导致7.6mcm/d容量不可用，另有13mcm/d的Other设施在窗口内开始；实际供气损失已确认。'
 assert s.collect_analysis_semantic_errors(body,ctx,p)

@pytest.mark.parametrize('text,expected',[
 ('若实际流量未恢复，供应约束持续，价格上升',{'up'}),
 ('若实际流量已恢复，供应约束缓解，价格下降',{'down'}),
 ('若未出现实际流量损失，供应约束未发生，价格下降',set()),
])
def test_local_conditional_recovery_and_absent_loss_polarity(text,expected):
 assert s._condition_mechanism_signs(text)==expected


def test_real_bullish_nonrestoration_condition_is_valid_but_opposite_is_not(actual):
 row,ctx,p=detail_setup(actual);body=json.loads(row['raw_content'][1])
 s._check_directional_conditions(body)
 with pytest.raises(ValueError):s._check_directional_conditions({'detail':{'scenarios':{'bearish':body['detail']['scenarios']['bullish']}}})
 with pytest.raises(ValueError):s._check_directional_conditions({'detail':{'scenarios':{'bullish':'若实际流量已恢复，供应约束缓解，价格下跌'}}})

@pytest.mark.parametrize('summary',[
 '实际流量未恢复，供应约束持续，价格上升。',
 '若实际流量未恢复，供应约束持续，价格上升。',
 '供应受限，但价格上涨。',
])
def test_source_price_predicate_is_not_physical_supply_increase(summary):
 candidates=s.extract_mechanisms({'source':'eia','title':'European gas market update','summary':summary})
 assert not any(c['mechanism']=='supply_increase' for c in candidates)

@pytest.mark.parametrize('change,mechanism,pressure',[
 ('减少','supply_decrease','up'),('增加','supply_increase','down'),('已恢复','supply_increase','down'),
])
def test_source_actual_same_clause_changes_remain_supported(change,mechanism,pressure):
 summary='欧洲实际供气'+change+'40mcm/d，TTF价格上涨。'
 candidates=s.extract_mechanisms({'source':'eia','title':'European gas update','summary':summary})
 assert any(c['mechanism']==mechanism and c['pressure']==pressure for c in candidates)


def test_prepared_source_card_does_not_inherit_price_up_as_supply_up(actual):
 ctx=copy.deepcopy(actual['input_snapshot']);ctx['current_news']={'id':'source-control','source':'eia','title':'European gas update','summary':'实际流量未恢复，供应约束持续，价格上升。','published_at':ctx['as_of'],'first_seen_at':ctx['as_of']};ctx['window_news']=[]
 p,_=a.prepare_context(ctx)
 assert not any(c['mechanism']=='supply_increase' for c in p['current_news'].get('mechanism_candidates',[]))
 assert '未恢复' in p['current_news']['facts']


@pytest.mark.parametrize('summary,forbidden,expected',[
 ('需求疲弱，价格上升。','demand_increase','demand_decrease'),
 ('需求强劲，价格下降。','demand_decrease',None),
 ('库存下降，价格上升。','inventory_build','inventory_draw'),
 ('库存充足，价格下降。','inventory_draw','inventory_build'),
])
def test_all_physical_source_candidates_do_not_borrow_price_verbs(summary,forbidden,expected):
 candidates=s.extract_mechanisms({'source':'eia','title':'European gas market update','summary':summary})
 assert not any(c['mechanism']==forbidden for c in candidates)
 if expected:assert any(c['mechanism']==expected for c in candidates)

@pytest.mark.parametrize('summary,forbidden',[
 ('需求疲弱，价格上升。','demand_increase'),('需求强劲，价格下降。','demand_decrease'),
 ('库存下降，价格上升。','inventory_build'),('库存充足，价格下降。','inventory_draw'),
])
def test_prepared_demand_inventory_cards_keep_physical_subject_scope(actual,summary,forbidden):
 ctx=copy.deepcopy(actual['input_snapshot']);ctx['current_news']={'id':'physical-scope-control','source':'eia','title':'European gas market update','summary':summary,'published_at':ctx['as_of'],'first_seen_at':ctx['as_of']};ctx['window_news']=[]
 prepared,_=a.prepare_context(ctx)
 assert not any(c['mechanism']==forbidden for c in prepared['current_news'].get('mechanism_candidates',[]))
 assert summary.rstrip('。') in prepared['current_news']['facts']
