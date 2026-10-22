"""Private post-validator controls only; no source rewriting or model calls."""
import copy,hashlib,json
from pathlib import Path
import pytest
from ttf_news import analysis as a,semantics as s
ROOT=Path(__file__).resolve().parents[1]
@pytest.fixture(scope='module')
def actuals():
 rows=json.loads((Path(__file__).parent/'fixtures/future_price_observation_role_actual_v2_3.json').read_text())['records']
 for row in rows.values():assert hashlib.sha256(row['raw_content'].encode()).hexdigest()==row['raw_sha256']
 return rows

def test_actual_future_zero_now_rejected_without_changing_flat_label(actuals):
 row=actuals['future_zero'];ctx=copy.deepcopy(row['input_snapshot']);res=json.loads(row['raw_content']);before=copy.deepcopy(res);p,_=a.prepare_context(ctx)
 with pytest.raises(ValueError,match='价格反应观测'):s._check_observed_absence_of_price_reaction(res,ctx,p)
 assert s.collect_analysis_semantic_errors(res,ctx,p)
 assert res==before

@pytest.mark.parametrize('claim',[
 '该事件为未来公告，未提供实际流量，当前不影响TTF价格。',
 '[N0]目前不影响气价。',
 '尚未开始物理停供，但当前不影响TTF价格。',
 '若需求增加可能上行，但当前不影响TTF价格。',
])
def test_exact_price_absence_is_not_waived_by_future_physical_scope(actuals,claim):
 row=actuals['future_zero'];ctx=copy.deepcopy(row['input_snapshot']);p,_=a.prepare_context(ctx)
 with pytest.raises(ValueError):s._check_observed_absence_of_price_reaction({'news_impact':{'summary':claim}},ctx,p)

@pytest.mark.parametrize('claim',[
 '预计当前不影响TTF价格。',
 '若需求不变，当前不影响TTF价格。',
 '无法确认当前不影响TTF价格。',
 '没有证据表明当前不影响TTF价格。',
 '当前不影响TTF价格的数据未提供。',
 '不能说当前不影响TTF价格。',
 '并非当前不影响TTF价格。',
 '预计对TTF价格影响有限。',
 '该公告尚未开始，尚未造成该事件的实际供气损失。',
 '未产生已知价格反应。',
])
def test_epistemic_conditions_pressure_and_future_physical_scope_remain_valid(actuals,claim):
 ctx=copy.deepcopy(actuals['future_zero']['input_snapshot']);p,_=a.prepare_context(ctx)
 s._check_observed_absence_of_price_reaction({'news_impact':{'summary':claim}},ctx,p)


def test_zero_reaction_same_source_and_known_clock_are_required(actuals):
 original=actuals['future_zero']['input_snapshot']
 for available,alias,allowed in [(original['as_of'],'N1',True),('2030-01-01T00:00:00Z','N1',False),(original['as_of'],'N0',False)]:
  ctx=copy.deepcopy(original);ctx['window_news']=[{'id':'independent','source':'independent event quote observations','title':'Comparable before/after reaction','summary':'独立公告前后可比报价观测确认当前不影响TTF价格。','published_at':available,'known_at':available}]
  p,_=a.prepare_context(ctx);out={'analysis':{'summary':alias+'所述消息当前不影响TTF价格。'}}
  if allowed:s._check_observed_absence_of_price_reaction(out,ctx,p)
  else:
   with pytest.raises(ValueError):s._check_observed_absence_of_price_reaction(out,ctx,p)


def body(claim):return {'analysis':{'summary':claim},'news_impact':{'summary':'新闻影响有限'},'close_forecast':{'summary':'条件预测'}}
def context(actuals,delta=.178):
 ctx=copy.deepcopy(actuals['eni_time_role']['input_snapshot']);ref=ctx['price_reference'];ref['intraday']['value']=ref['previous_price']+delta
 ref['market_baseline'].update(latest_price=ref['intraday']['value'],observed_delta=delta)
 return ctx

@pytest.mark.parametrize('delta,claim',[
 (.178,'当前报价较前一交易日上涨0.178 EUR/MWh。'),
 (-.178,'当前报价较前一交易日下跌0.178 EUR/MWh。'),
 (.178,'当前报价较前一交易日参考价格上涨0.178 EUR/MWh，预计收盘可能维持。'),
 (.178,'已观察到同合约基准上涨0.178 EUR/MWh，未来变动尚未校准。'),
 (.178,'盘中价格已上涨至87.725 EUR/MWh。'),
 (.178,'盘中价格已上涨至87.73 EUR/MWh。'),
 (.178,'当前TTF价格涨幅0.2033%。'),
 (.178,'当前报价较前收上涨0.18 EUR/MWh。'),
])
def test_known_current_comparison_is_not_fabricated_target(actuals,delta,claim):
 ctx=context(actuals,delta);before=copy.deepcopy(ctx);a._validate_prediction_semantics(body(claim),ctx,False);assert ctx==before

@pytest.mark.parametrize('claim',[
 '基于前一交易日价格上涨0.178 EUR/MWh，预计收盘保持平稳。',
 '上一交易日价格上涨0.178 EUR/MWh。',
])
def test_prior_day_itself_is_not_the_current_comparison(actuals,claim):
 with pytest.raises(ValueError,match='价格时间角色不符'):a._validate_prediction_semantics(body(claim),context(actuals),False)


def test_exact_eni_second_fails_for_time_role_not_fabricated_forecast(actuals):
 row=actuals['eni_time_role'];result=json.loads(row['raw_content']);before=copy.deepcopy(result)
 with pytest.raises(ValueError,match='价格时间角色不符'):a._validate_prediction_semantics(result,row['input_snapshot'],False)
 assert result==before

@pytest.mark.parametrize('claim',[
 '预计收盘价上涨0.178 EUR/MWh。',
 '基于昨日参考价预计收盘上涨0.178 EUR/MWh。',
 '当前目标价87.725 EUR/MWh。',
 '预计TTF价格上行约0.5–1.0 €/MWh。',
 '当前报价较前一交易日上涨87.725 EUR/MWh。',
 '当前报价较前一交易日上涨0 EUR/MWh。',
 '盘中价格已上涨至88 EUR/MWh。',
 '当前报价较前一交易日下跌0.178 EUR/MWh。',
 '当前报价较前一交易日上涨-0.178 EUR/MWh。',
 '当前TTF价格涨幅0.178%。',
 '当前报价较前一交易日上涨0.17 EUR/MWh。',
 '当前报价较前一交易日上涨0.178 EUR/MWh，但预计收盘上涨0.178 EUR/MWh。',
])
def test_level_delta_sign_percent_rounding_and_future_target_roles_do_not_mix(actuals,claim):
 with pytest.raises(ValueError,match='没有校准'):a._validate_prediction_semantics(body(claim),context(actuals),False)

@pytest.mark.parametrize('change',[
 'quote_contract','reference_date','future_available','future_observed','future_known','missing_unit','wrong_unit',
])
def test_known_clock_same_contract_previous_day_and_unit_identity_required(actuals,change):
 ctx=context(actuals);ref=ctx['price_reference'];quote=ref['intraday']
 if change=='quote_contract':quote['contract']='another-month'
 elif change=='reference_date':ref['price_date']='2026-10-15'
 elif change=='future_available':quote['available_at']='2030-01-01T00:00:00Z'
 elif change=='future_observed':quote['observed_at']='2030-01-01T00:00:00Z'
 elif change=='future_known':quote['known_at']='2030-01-01T00:00:00Z'
 elif change=='missing_unit':quote.pop('unit',None)
 elif change=='wrong_unit':quote['unit']='USD/MWh'
 with pytest.raises(ValueError,match='没有校准'):a._validate_prediction_semantics(body('当前报价较前一交易日上涨0.178 EUR/MWh。'),ctx,False)
