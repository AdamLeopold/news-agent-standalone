"""Captured tenth inputs isolate two local false positives; no generation."""
import copy,hashlib,json
from pathlib import Path
import pytest
from test_production_price_unit_scopes import verified_historical_bytes
from ttf_news import analysis as a,semantics as s
D=Path(__file__).resolve().parents[1]
R=next(p for p in (D,*D.parents) if (p/'pyproject.toml').is_file())
FIX=json.loads((Path(__file__).parent/'fixtures/tenth_actual_v2_3.json').read_text())['records']
BY={r['case']:r for r in FIX}
def row():return copy.deepcopy(BY['tenth_manual_2'])
def errors(out,ctx,p,index):
 result=s.collect_analysis_semantic_errors(out,ctx,p,source_index=index)
 for check in [lambda:a._validate_notice_source_facts(out,ctx,p),lambda:a._validate_prediction_semantics(out,ctx,False),lambda:a._validate_historical_reactions(out,p),lambda:a._validate_priced_in(out,p)]:
  try:check()
  except (ValueError,TypeError) as exc:result.append(str(exc))
 return result

@pytest.mark.parametrize('f',FIX,ids=lambda x:x['case'])
def test_actuals_exact_source_and_raw_and_old_captured_prepared(f):
 verified_historical_bytes(R/f['record']['file'],f['record']['sha256'],project_root=R)
 assert hashlib.sha256(f['raw_content'].encode()).hexdigest()==f['raw_sha256']
 assert hashlib.sha256(a._json(f['captured_prepared']).encode()).hexdigest()==f['captured_prepared_sha256']
 out=json.loads(f['raw_content']);before=copy.deepcopy(out)
 observed=errors(out,f['input_snapshot'],f['captured_prepared'],f['captured_source_index'])
 if f['case']=='tenth_manual_2':assert observed==[]
 else:assert observed
 assert out==before

def test_manual_first_capacity_list_correct_but_previous_day_clock_remains_wrong():
 f=BY['tenth_manual_1'];out=json.loads(f['raw_content'])
 a._validate_notice_source_facts(out,f['input_snapshot'],f['captured_prepared'])
 with pytest.raises(ValueError,match='价格时间角色不符'):a._validate_prediction_semantics(out,f['input_snapshot'],False)

@pytest.mark.parametrize('claim',[
 'Troll气田计划维护导致7.6mcm/d与13.0mcm/d容量约束，未确认实际供气损失。',
 'Troll气田计划维护导致可用容量下降13.0mcm/d（当前）和7.6mcm/d（未来），形成供应约束。',
])
def test_unaliased_capacity_list_binds_each_value_to_a_cited_known_notice(claim):
 f=row();out=json.loads(f['raw_content']);out['news_impact']['summary']=claim;out['analysis']['summary']=claim
 a._validate_notice_source_facts(out,f['input_snapshot'],f['captured_prepared'])

@pytest.mark.parametrize('claim',[
 '[N1]Troll当前公告不可用容量13.0mcm/d。',
 'Troll气田计划维护导致可用容量下降7.6mcm/d（当前）和13.0mcm/d（未来）。',
 'Troll气田计划维护导致两天内可用容量分别为7.6和13.0mcm/d。',
 'Troll气田计划维护导致不可用容量7.6mcm/d与99.0mcm/d。',
 'Troll两项维护合计容量下降20.6mcm/d。',
 'Troll两项维护导致7.6mcm/d与13mcm/d容量约束，合计20.6mcm/d。',
 'Troll气田计划维护导致7.6mcm/d与13.0mcm/d容量约束，可用容量99。',
])
def test_explicit_wrong_alias_clock_absolute_field_unknown_number_and_sum_stay_rejected(claim):
 f=row();out=json.loads(f['raw_content']);out['news_impact']['summary']=claim
 with pytest.raises(ValueError):a._validate_notice_source_facts(out,f['input_snapshot'],f['captured_prepared'])

@pytest.mark.parametrize('claim',[
 '市场基准显示前日参考价85.062 EUR/MWh，当前报价88.105 EUR/MWh，涨幅3.043 EUR/MWh。',
 '观察到的价格上涨3.043 EUR/MWh，表明已知参考变化向上。',
 '当前同合约报价较前一交易日参考价上涨3.04 EUR/MWh。',
])
def test_known_observation_is_not_a_future_target(claim):
 f=row();a._validate_prediction_semantics({'close_forecast':{'basis':claim}},f['input_snapshot'],False)

@pytest.mark.parametrize('claim',[
 '预计收盘价格上涨3.043 EUR/MWh。',
 '当前同合约报价上涨4.0 EUR/MWh。',
 '观察到的价格下跌3.043 EUR/MWh。',
 '前一交易日参考价上涨3.043 EUR/MWh。',
 '市场基准显示前日参考价85.062 EUR/MWh，当前报价88.105 EUR/MWh，预计收盘涨幅3.043 EUR/MWh。',
 '市场基准显示前日参考价88.105 EUR/MWh，当前报价85.062 EUR/MWh，涨幅3.043 EUR/MWh。',
])
def test_future_target_wrong_amount_sign_time_and_reversed_pair_stay_rejected(claim):
 f=row()
 with pytest.raises(ValueError):a._validate_prediction_semantics({'close_forecast':{'basis':claim}},f['input_snapshot'],False)

@pytest.mark.parametrize('mode',['known_future','observed_future','foreign_contract','wrong_unit','missing_anchor'])
def test_numeric_marker_does_not_waive_asof_contract_unit_or_anchor(mode):
 f=row();ctx=f['input_snapshot'];claim='观察到的价格上涨3.043 EUR/MWh。'
 if mode=='known_future':ctx['price_reference']['intraday']['available_at']='2030-01-01T00:00:00Z'
 if mode=='observed_future':ctx['price_reference']['intraday']['observed_at']='2030-01-01T00:00:00Z'
 if mode=='foreign_contract':ctx['price_reference']['intraday']['contract']='Dec26'
 if mode=='wrong_unit':claim=claim.replace('EUR/MWh','EUR/mcm')
 if mode=='missing_anchor':ctx['price_reference']['price_date']='2026-10-19'
 out=json.loads(f['raw_content']);out['close_forecast']['basis']=claim
 assert errors(out,ctx,f['captured_prepared'],f['captured_source_index'])

def test_downward_known_pair_and_observation_remain_symmetric():
 f=row();ctx=f['input_snapshot'];ctx['price_reference']['intraday']['value']=82.019
 for claim in ['观察到的价格下跌3.043 EUR/MWh。','市场基准显示前日参考价85.062 EUR/MWh，当前报价82.019 EUR/MWh，跌幅3.043 EUR/MWh。']:
  a._validate_prediction_semantics({'close_forecast':{'basis':claim}},ctx,False)

OLD=json.loads((Path(__file__).parent/'fixtures/tenth_old_twenty_one_bad_v2_3.json').read_text())['records']
@pytest.mark.parametrize('f',OLD,ids=lambda x:x['case'])
def test_old_twenty_one_bad_raws_remain_rejected(f):
 assert hashlib.sha256(f['raw_content'].encode()).hexdigest()==f['raw_sha256']
 ctx=copy.deepcopy(f['input_snapshot']);p,m=a.prepare_context(ctx)
 assert errors(json.loads(f['raw_content']),ctx,p,m['source_index'])


def test_unaliased_list_cannot_borrow_another_facilitys_cited_capacity():
 f=row();p=f['captured_prepared']
 for card in p['window_news']:
  if card['id']=='N2':card['title']=card['title'].replace('Troll','Nyhamna')
 out=json.loads(f['raw_content']);out['news_impact']['summary']='Troll计划维护导致7.6mcm/d与13.0mcm/d容量约束。'
 with pytest.raises(ValueError):a._validate_notice_source_facts(out,f['input_snapshot'],p)
