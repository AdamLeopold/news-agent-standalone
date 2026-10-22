"""Previous-reference aliases do not change the observed quote's time role."""
import copy,hashlib,json
from pathlib import Path
import pytest
from ttf_news import analysis as a,semantics as s
ALIASES=['参考价','参考价格','收盘价','收盘价格','结算价','结算价格','报价','气价','价格']
@pytest.fixture(scope='module')
def actual():
 p=Path(__file__).parent/'actual_fixture.json'
 if not p.is_file():p=Path(__file__).parent/'fixtures/ninth_price_role_alias_actual_v2_3.json'
 row=json.loads(p.read_text());assert hashlib.sha256(row['raw_content'].encode()).hexdigest()==row['raw_sha256'];return row

def ctx(row):return copy.deepcopy(row['input_snapshot'])
def body(claim):return {'close_forecast':{'basis':claim}}

def test_actual_ninth_body_is_rejected_for_time_role_without_rewriting_up(actual):
 result=json.loads(actual['raw_content']);before=copy.deepcopy(result)
 with pytest.raises(ValueError,match='价格时间角色不符') as exc:a._validate_prediction_semantics(result,ctx(actual),False)
 assert all(x in str(exc.value) for x in ['85.062','87.73','2026-10-20','2026-10-21T04:36:00','2.668'])
 assert result==before and result['close_forecast']['direction']=='up'

@pytest.mark.parametrize('alias',ALIASES)
@pytest.mark.parametrize('form',['前一交易日{a}从85.062 EUR/MWh升至87.73 EUR/MWh。','基于前一交易日{a}上涨且无明显反向因素。'])
def test_previous_day_subject_aliases_do_not_relabel_current_observations(actual,alias,form):
 with pytest.raises(ValueError,match='价格时间角色不符'):a._validate_prediction_semantics(body(form.format(a=alias)),ctx(actual),False)

@pytest.mark.parametrize('claim',[
 '当前报价较前一交易日参考价上涨2.668 EUR/MWh。',
 '当前报价较前一交易日收盘价上涨2.67 EUR/MWh。',
 '从前一交易日85.062到当前报价87.73，观察到同合约参考变化。',
 '前一交易日参考价为85.062，当前报价为87.73。',
 '当前报价从前一交易日参考价85.062上涨至87.73 EUR/MWh。',
 '从前一交易日参考价85.062上涨至当前87.73 EUR/MWh。',
 '若前一交易日参考价上涨，则核对相应记录。',
])
def test_explicit_current_vs_previous_roles_or_hypotheses_remain_distinct(actual,claim):
 a._validate_prediction_semantics(body(claim),ctx(actual),False)

@pytest.mark.parametrize('claim',[
 '预计收盘价上涨2.668 EUR/MWh。',
 '当前报价较前一交易日参考价上涨0 EUR/MWh。',
 '当前报价上涨至88 EUR/MWh。',
 '当前报价较前一交易日参考价上涨2 EUR/MWh。',
 '当前报价较前一交易日参考价上涨2.66 EUR/MWh。',
 '当前报价较前一交易日参考价下跌2.668 EUR/MWh。',
 '当前报价较前一交易日参考价上涨2.668 EUR/mcm。',
])
def test_future_target_sign_unit_and_rounding_cannot_borrow_a_known_number(actual,claim):
 with pytest.raises(ValueError):
  c=ctx(actual);out=body(claim)
  a._validate_prediction_semantics(out,c,False)
  s._check_ttf_price_unit_identity(out,c,{})


def test_downward_current_comparison_has_the_same_role_boundary(actual):
 c=ctx(actual);c['price_reference']['intraday']['value']=83.062;c['price_reference']['market_baseline'].update(latest_price=83.062,observed_delta=-2,observed_direction='down')
 with pytest.raises(ValueError,match='价格时间角色不符'):a._validate_prediction_semantics(body('前一交易日结算价从85.062 EUR/MWh跌至83.062 EUR/MWh。'),c,False)
 a._validate_prediction_semantics(body('当前报价较前一交易日结算价下跌2 EUR/MWh。'),c,False)

@pytest.mark.parametrize('change',['future_quote','contract','missing_previous_day'])
def test_observation_allowance_still_requires_known_same_contract_previous_day(actual,change):
 c=ctx(actual)
 if change=='future_quote':c['price_reference']['intraday']['available_at']='2030-01-01T00:00:00Z'
 elif change=='contract':c['price_reference']['intraday']['contract']='other-contract'
 else:c['price_reference']['price_date']='2026-10-19'
 with pytest.raises(ValueError):a._validate_prediction_semantics(body('当前报价较前一交易日参考价上涨2.668 EUR/MWh。'),c,False)

@pytest.mark.parametrize('claim',[
 '前一交易日结算价为85.062 EUR/MWh，当前报价为87.73 EUR/MWh。',
 '从前一交易日结算价85.062 EUR/MWh上涨至当前87.73 EUR/MWh。',
])
def test_previous_settlement_price_is_a_level_operand_not_a_target(actual,claim):
 a._validate_prediction_semantics(body(claim),ctx(actual),False)

@pytest.mark.parametrize('change,allowed',[(None,True),('future',False),('contract',False),('day',False),('opposite',False),('projected',False),('current_comparison',False),('negated',False),('uncited',False)])
def test_independent_known_previous_day_own_move_requires_its_own_source(actual,change,allowed):
 c=ctx(actual)
 source={'id':'own-daily-report','contract':c['price_reference']['contract'],'price_date':'2026-10-20',
  'title':'同合约日价记录','summary':'当日参考价上涨0.50 EUR/MWh。','published_at':'2026-10-20T23:30:00Z'}
 c['window_news'].append(source);alias='N'+str(len(c['window_news']))
 if change=='future':source['available_at']='2030-01-01T00:00:00Z'
 if change=='contract':source['contract']='another-month'
 if change=='day':source['price_date']='2026-10-19'
 if change=='opposite':source['summary']='当日参考价下跌0.50 EUR/MWh。'
 if change=='projected':source['summary']='预计当日参考价上涨0.50 EUR/MWh。'
 if change=='current_comparison':source['summary']='当前报价较前一交易日参考价上涨0.50 EUR/MWh。'
 if change=='negated':source['summary']='当日参考价未上涨0.50 EUR/MWh。'
 claim=('' if change=='uncited' else '['+alias+']')+'前一交易日参考价上涨0.50 EUR/MWh。'
 if allowed:a._validate_prediction_semantics(body(claim),c,False)
 else:
  with pytest.raises(ValueError):a._validate_prediction_semantics(body(claim),c,False)


def test_corresponding_historical_comparison_keeps_separate_source_validation(actual):
 c=ctx(actual);claim='[E1]前一交易日参考价上涨0.50 EUR/MWh，作为已知历史变化比较。'
 out={'analysis':{'historical_comparisons':[claim]}}
 a._validate_prediction_semantics(out,c,False)

@pytest.mark.parametrize('marker',['当前同合约报价','目前同合约报价','当前的同合约价格','目前的同合约气价','当前TTF报价','目前的TTF价格'])
@pytest.mark.parametrize('delta',[2.668,-2.668])
def test_explicit_same_contract_observation_marker_keeps_identity_guards(actual,marker,delta):
 c=ctx(actual);c['price_reference']['intraday']['value']=85.062+delta
 c['price_reference']['market_baseline'].update(latest_price=85.062+delta,observed_delta=delta)
 verb='上涨' if delta>0 else '下跌'
 a._validate_prediction_semantics(body(marker+'较前一交易日结算价'+verb+'2.67 EUR/MWh。'),c,False)

@pytest.mark.parametrize('change',['amount','sign','unit','future_clock','contract'])
def test_same_contract_words_alone_cannot_authorize_observation(actual,change):
 c=ctx(actual);claim='当前同合约报价较前一交易日结算价上涨2.67 EUR/MWh。'
 if change=='amount':claim=claim.replace('2.67','2.00')
 if change=='sign':claim=claim.replace('上涨','下跌')
 if change=='unit':claim=claim.replace('EUR/MWh','EUR/mcm')
 if change=='future_clock':c['price_reference']['intraday']['available_at']='2030-01-01T00:00:00Z'
 if change=='contract':c['price_reference']['intraday']['contract']='another-contract'
 with pytest.raises(ValueError):
  out=body(claim);a._validate_prediction_semantics(out,c,False);s._check_ttf_price_unit_identity(out,c,{})
