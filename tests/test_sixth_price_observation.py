"""Two actual reaction-observation/pricing-premise regressions; no inference calls."""
import copy,hashlib,json
from pathlib import Path
import pytest
from ttf_news import analysis as a,semantics as s
ROOT=next(p for p in Path(__file__).resolve().parents if (p/'ttf_news/analysis.py').is_file())

@pytest.fixture(scope='module')
def actuals():
 f=Path(__file__).parent/'actual_fixture.json'
 if not f.is_file():f=Path(__file__).parent/'fixtures/sixth_price_observation_actual_v2_3.json'
 rows=json.loads(f.read_text())['records']
 for row in rows.values():assert hashlib.sha256(row['raw_content'].encode()).hexdigest()==row['raw_sha256']
 return rows


def prepared(actuals,name='manual_reaction_degree'):
 ctx=copy.deepcopy(actuals[name]['input_snapshot']);p,_=a.prepare_context(ctx)
 return ctx,p

@pytest.mark.parametrize('name',['manual_reaction_degree','current_pricing_unknown'])
def test_actual_sixth_bodies_rejected_without_mutating_direction_or_raw(actuals,name):
 row=actuals[name];ctx,p=prepared(actuals,name);body=json.loads(row['raw_content']);before=copy.deepcopy(body)
 errors=s.collect_analysis_semantic_errors(body,ctx,p)
 assert errors
 assert any(('价格反应观测' if name=='manual_reaction_degree' else '计价未知或缺少计价证据') in e for e in errors)
 assert body==before
 assert body['news_impact']['direction']=='flat' and body['close_forecast']['direction']=='up'

@pytest.mark.parametrize('name',['manual_reaction_degree','current_pricing_unknown'])
def test_controlled_corrected_body_preserves_valid_baseline_forecast(actuals,name):
 row=actuals[name];ctx,p=prepared(actuals,name);body=json.loads(row['raw_content'])
 if name=='manual_reaction_degree':
  for section in ('news_impact','analysis'):
   body[section]['summary']=body[section]['summary'].replace('市场反应不明显','公告价格反应未知')
 else:
  body['close_forecast']['basis']='公告计价状态未知；已知同合约行情基准由85.062升至86.85，据此作条件上涨预测。'
 assert s.collect_analysis_semantic_errors(body,ctx,p)==[]
 assert body['news_impact']['direction']=='flat' and body['close_forecast']['direction']=='up'

@pytest.mark.parametrize('claim',[
 '市场反应不明显。','市场反应不显著。','N1所述市场反应目前不明显。',
 'N0公告的价格反应不显著。','价格反应不明显。','TTF价格反应暂不明显。',
 '预计未来有供给约束，但市场反应不明显。',
])
def test_bare_reaction_degree_requires_observation(actuals,claim):
 ctx,p=prepared(actuals)
 with pytest.raises(ValueError,match='价格反应观测'):s._check_observed_absence_of_price_reaction({'analysis':{'summary':claim}},ctx,p)

@pytest.mark.parametrize('claim',[
 '预计市场反应不明显。','市场反应可能不明显。','可能市场反应不明显。',
 '若需求不变，市场反应不明显。','若市场反应不明显，则更新条件预测。',
 '无法确认市场反应不明显。','不能说市场反应不明显。','并非市场反应不明显。',
 '无证据表明市场反应不明显。','没有证据证明市场反应不显著。',
 '市场反应不明显的证据未提供。','尚未观察到市场反应不明显。',
 '未见明显市场反应。','未见市场反应不明显的证据。',
 '当前新闻传导较弱，预计价格影响有限。','公告预计产生弱上行压力。',
 '该未来维护尚未开始，尚未造成该事件的实际供气损失。',
])
def test_epistemic_conditional_and_pressure_judgements_not_observation(actuals,claim):
 ctx,p=prepared(actuals)
 s._check_observed_absence_of_price_reaction({'analysis':{'summary':claim}},ctx,p)

@pytest.mark.parametrize('clock,alias,source_claim,allowed',[
 ('known','N1','独立公告前后可比报价观测确认市场反应不明显。',True),
 ('future','N1','独立公告前后可比报价观测确认市场反应不明显。',False),
 ('known','N0','独立公告前后可比报价观测确认市场反应不明显。',False),
 ('known','N1','无证据表明市场反应不明显。',False),
 ('known','N1','预计市场反应不明显。',False),
 ('known','N1','未见市场反应不明显的证据。',False),
])
def test_reaction_support_is_same_source_known_and_not_merely_epistemic(actuals,clock,alias,source_claim,allowed):
 ctx,_=prepared(actuals);when=ctx['as_of'] if clock=='known' else '2030-01-01T00:00:00Z'
 ctx['window_news']=[{'id':'reaction-observation','source':'independent quotations','title':'Comparable announcement response',
  'summary':source_claim,'published_at':when,'known_at':when}]
 p,_=a.prepare_context(ctx);out={'analysis':{'summary':alias+'所述市场反应不明显。'}}
 if allowed:s._check_observed_absence_of_price_reaction(out,ctx,p)
 else:
  with pytest.raises(ValueError):s._check_observed_absence_of_price_reaction(out,ctx,p)

@pytest.mark.parametrize('direction',['上涨','下跌','持平'])
@pytest.mark.parametrize('premise',[
 '基准上涨且无证据表明公告已被计价，故预测收盘价相对前一交易日{d}。',
 '无法确认公告是否已计价，因此预计TTF价格{d}。',
 '计价状态未知，故预测收盘价{d}。',
 '基于计价未知，预测收盘价{d}。',
 '基于计价未知且基准上涨，因此预测收盘价{d}。',
])
def test_unknown_cannot_be_signed_forecast_factor(premise,direction):
 with pytest.raises(ValueError,match='计价未知或缺少计价证据'):
  s._check_pricing_unknown_not_directional_evidence({'close_forecast':{'basis':premise.format(d=direction)}})

@pytest.mark.parametrize('claim',[
 '计价状态未知。已观察到同合约基准上涨，据此预计收盘可能上涨。',
 '计价未知，已观察到同合约基准下跌，因此预测收盘可能下跌。',
 '是否已计价未知，且已知同合约基准上涨，因此条件预计收盘上涨。',
 '计价状态未知，且来源确认需求下降，因此预计TTF价格下跌。',
 '虽然无法确认是否已计价，但已知基准上涨，据此作条件上涨预测。',
 '虽然计价状态未知，但是已知基准下跌支持条件下跌预测。',
 '公告计价状态未知；只依据已知行情基准作条件预测。',
 '若公告尚未计价，新增实流可能带来下行压力。',
 '无法确认是否已计价，因此不能断言价格上涨。',
 '计价未知，因此无法确定收盘价下跌。',
 '计价未知，不能据此预测收盘价上涨。',
 '不能因计价未知，因此预测价格上涨。',
 '计价未知，Troll故障恢复导致价格下行。',
 '计价未知；若需求上升，则TTF价格可能上涨。',
 '对未知计价须核实，不得推定尚未计价。',
])
def test_unknown_notes_concessions_conditions_and_warnings_remain_valid(claim):
 s._check_pricing_unknown_not_directional_evidence({'close_forecast':{'basis':claim}})


def test_same_actual_unknown_note_does_not_become_evidence_due_valid_anchor(actuals):
 ctx,p=prepared(actuals,'current_pricing_unknown');body=json.loads(actuals['current_pricing_unknown']['raw_content'])
 assert p['price_reference']['market_baseline']['available'] is True
 with pytest.raises(ValueError):s._check_pricing_unknown_not_directional_evidence(body)


def test_real_price_reaction_can_support_confirmed_pricing(actuals):
 ctx,p=prepared(actuals,'current_pricing_unknown')
 p['price_reference']['announcement_price_reaction']={'available':True,'source':'independent comparable announcement quotations','observed_at':ctx['as_of']}
 out={'close_forecast':{'basis':'独立公告前后可比报价证实消息已计价；基于已知行情作条件上涨预测。'}}
 s._check_priced_in(out,ctx,p)
 s._check_pricing_unknown_not_directional_evidence(out)
