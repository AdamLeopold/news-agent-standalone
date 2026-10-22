"""Exact seventh-run output-scope controls; no inference or record mutation."""
import copy,hashlib,json
from pathlib import Path
import pytest
from ttf_news import analysis as a,semantics as s
ROOT=next(p for p in Path(__file__).resolve().parents if (p/'pyproject.toml').is_file() and (p/'ttf_news/analysis.py').is_file())
@pytest.fixture(scope='module')
def actual():
 path=Path(__file__).parent/'actual_fixture.json'
 if not path.is_file():path=Path(__file__).parent/'fixtures/seventh_source_scope_actual_v2_3.json'
 row=json.loads(path.read_text())
 for source in [row,row['detail_actual']]:
  for raw,expected in zip(source['raw_content'],source['raw_sha256']):assert hashlib.sha256(raw.encode()).hexdigest()==expected
 return row

def setup(actual,detail=False):
 row=actual['detail_actual'] if detail else actual;ctx=copy.deepcopy(row['input_snapshot']);p,m=a.prepare_context(ctx,detail);return row,ctx,p,m

def test_actual_first_pair_is_valid_capacity_but_wrong_price_clock_stays_failed(actual):
 row,ctx,p,_=setup(actual);body=json.loads(row['raw_content'][0]);before=copy.deepcopy(body)
 a._validate_notice_source_facts(body,ctx,p)
 with pytest.raises(ValueError,match='价格时间角色不符'):a._validate_prediction_semantics(body,ctx,False)
 assert body==before and row['original_status']=='failed'

@pytest.mark.parametrize('summary',[
 '两条未来维护公告，分别导致7.6和13mcm/d的容量损失。',
 '两项未来公告分别有7.6mcm/d和13mcm/d不可用容量。',
])
def test_unnamed_explicit_pair_binds_evidenced_notices(actual,summary):
 row,ctx,p,_=setup(actual);b=json.loads(row['raw_content'][0]);b['news_impact']['summary']=summary;b['analysis']['summary']=summary
 a._validate_notice_source_facts(b,ctx,p)

@pytest.mark.parametrize('summary',[
 '[N0]两条未来公告分别导致7.6和13mcm/d容量损失。',
 '两条未来维护公告，分别导致7.6和20mcm/d的容量损失。',
 '两条未来维护公告分别有7.6和13mcm/d容量损失，合计20.6mcm/d。',
])
def test_pair_does_not_relax_explicit_N_unknown_amount_or_cross_period_sum(actual,summary):
 row,ctx,p,_=setup(actual);b=json.loads(row['raw_content'][0]);b['news_impact']['summary']=summary;b['analysis']['summary']=summary
 with pytest.raises(ValueError):a._validate_notice_source_facts(b,ctx,p)


def test_unreferenced_pair_is_not_assumed_from_arbitrary_notices(actual):
 _,ctx,p,_=setup(actual)
 with pytest.raises(ValueError):a._validate_notice_source_facts({'analysis':{'summary':'两条未来公告分别有7.6和13mcm/d容量损失。'}},ctx,p)


def test_actual_detail_start_days_resolve_correct_notices(actual):
 row,ctx,p,_=setup(actual,True);body=json.loads(row['raw_content'][1]);before=copy.deepcopy(body)
 a._validate_notice_source_facts(body,ctx,p)
 assert body==before

@pytest.mark.parametrize('claim,allowed',[
 ('Troll气田计划维护将于10月22日开始，导致7.6mcm/d容量不可用；另一公告于10月21日开始，容量不可用13mcm/d。',True),
 ('Troll计划维护将于10月22日开始，容量不可用13mcm/d。',False),
 ('Troll计划维护将于2027年10月22日开始，容量不可用7.6mcm/d。',False),
 ('[N2]Troll计划维护将于10月22日开始，容量不可用7.6mcm/d。',False),
 ('Troll计划维护在10月22日容量不可用7.6mcm/d。',False),
])
def test_start_clock_binding_is_distinct_from_ambiguous_day_overlap(actual,claim,allowed):
 row,ctx,p,_=setup(actual,True);out={'analysis':{'summary':claim,'evidence':json.loads(row['raw_content'][1])['analysis']['evidence']}}
 if allowed:a._validate_notice_source_facts(out,ctx,p)
 else:
  with pytest.raises(ValueError):a._validate_notice_source_facts(out,ctx,p)


def test_duplicate_alias_of_exact_notice_not_a_new_date_candidate(actual):
 _,ctx,p,_=setup(actual,True)
 duplicate=copy.deepcopy(p['current_news']);duplicate['id']='N3'
 duplicate.pop('same_as_current_news_id',None);duplicate.pop('same_as_window_news_id',None)
 assert duplicate.get('operational',{}).get('event_start_utc')
 p['window_news'].append(duplicate)  # Copy the full card, never an alias pointer.
 a._validate_notice_source_facts({'analysis':{'summary':'Troll于10月22日开始，公告不可用容量7.6mcm/d。'}},ctx,p)
 conflict=copy.deepcopy(p);conflict['window_news'][-1]['operational']['unavailable_capacity']='8.0'
 with pytest.raises(ValueError,match='唯一N来源'):a._validate_notice_source_facts({'analysis':{'summary':'Troll于10月22日开始，公告不可用容量7.6mcm/d。'}},ctx,conflict)

@pytest.mark.parametrize('claim',[
 '前一交易日价格从85.062升至86.9，观察到上涨1.838。',
 '上一交易日报价从85.062上涨至86.9 EUR/MWh。',
])
def test_known_previous_to_current_levels_cannot_be_prior_day_movement(actual,claim):
 _,ctx,_,_=setup(actual)
 with pytest.raises(ValueError,match='价格时间角色不符'):a._validate_prediction_semantics({'close_forecast':{'basis':claim}},ctx,False)

@pytest.mark.parametrize('claim',[
 '当前报价较前一交易日参考价上涨1.838 EUR/MWh。',
 '从前一交易日85.062到当前报价86.9，观察到同合约参考变化。',
 '前一交易日参考价为85.062，当前报价为86.9。',
 '当前报价从前一交易日参考价85.062上涨至86.9 EUR/MWh。',
])
def test_explicit_current_vs_previous_roles_remain_valid(actual,claim):
 _,ctx,_,_=setup(actual);a._validate_prediction_semantics({'close_forecast':{'basis':claim}},ctx,False)

@pytest.mark.parametrize('claim',[
 '预计收盘价上涨1.838 EUR/MWh。',
 '当前报价相对前价上涨85.062 EUR/MWh。',
])
def test_known_numbers_do_not_become_uncalibrated_target_or_wrong_role(actual,claim):
 _,ctx,_,_=setup(actual)
 with pytest.raises(ValueError):a._validate_prediction_semantics({'close_forecast':{'basis':claim}},ctx,False)


def test_current_minus_previous_downward_levels_have_same_clock_contract(actual):
 _,ctx,_,_=setup(actual);ctx['price_reference']['intraday']['value']=83.062;ctx['price_reference']['market_baseline'].update(latest_price=83.062,observed_delta=-2,observed_direction='down')
 with pytest.raises(ValueError,match='价格时间角色不符'):a._validate_prediction_semantics({'close_forecast':{'basis':'前一交易日价格从85.062下跌至83.062。'}},ctx,False)
 a._validate_prediction_semantics({'close_forecast':{'basis':'当前报价较前一交易日参考下跌2 EUR/MWh。'}},ctx,False)


def test_exact_future_subject_contrast_is_not_global_no_loss(actual):
 row,ctx,p,_=setup(actual);body=json.loads(row['raw_content'][1]);out={'news_impact':{'summary':body['news_impact']['summary']}}
 s._check_unknown_as_fact(out,ctx,p)
 assert s.collect_analysis_semantic_errors(body,ctx,p)  # pricing/zero-price still rejected

@pytest.mark.parametrize('claim',[
 '计划维护公告提示未来供给受限，但未导致即时供气损失。',
 'N2所述计划维护公告提示未来供给受限，但未导致即时供气损失。',
])
def test_future_event_ellipsis_is_scoped_to_its_known_start(actual,claim):
 _,ctx,p,_=setup(actual);s._check_unknown_as_fact({'analysis':{'summary':claim}},ctx,p)
 active=copy.deepcopy(ctx);active['as_of']='2026-10-21T04:01:00Z';pa,_=a.prepare_context(active)
 with pytest.raises(ValueError):s._check_unknown_as_fact({'analysis':{'summary':claim}},active,pa)

@pytest.mark.parametrize('claim',[
 '计划维护公告提示未来供给受限，但全网实际流量没有损失。',
 '计划维护公告提示未来供给受限，但另一气田故障未导致即时供气损失。',
 '本窗口实际流量保持不变。',
])
def test_future_notice_does_not_excuse_global_or_new_subject_measurement(actual,claim):
 _,ctx,p,_=setup(actual)
 with pytest.raises(ValueError):s._check_unknown_as_fact({'analysis':{'summary':claim}},ctx,p)


def test_future_physical_exception_does_not_waive_price_zero(actual):
 _,ctx,p,_=setup(actual)
 with pytest.raises(ValueError):s._check_observed_absence_of_price_reaction({'analysis':{'summary':'计划维护公告提示未来供给受限，但未产生即时价格变动。'}},ctx,p)

@pytest.mark.parametrize('label',['低','中','高','未知'])
def test_short_risk_level_only_is_valid_chinese_field(actual,label):
 row,ctx,_,meta=setup(actual);body=json.loads(row['raw_content'][1]);body['close_forecast']['extreme_move_risk']=label
 parsed=a._parse_result(json.dumps(body,ensure_ascii=False),False,meta['source_index'],ctx)
 assert parsed['close_forecast']['extreme_move_risk']==label

@pytest.mark.parametrize('field',[('news_impact','summary'),('close_forecast','basis'),('analysis','summary')])
def test_summary_or_basis_is_not_reduced_to_one_chinese_label(actual,field):
 row,ctx,_,meta=setup(actual);body=json.loads(row['raw_content'][1]);body['close_forecast']['extreme_move_risk']='低';body[field[0]][field[1]]='低'
 with pytest.raises(ValueError,match='核心分析文案必须为中文'):a._parse_result(json.dumps(body,ensure_ascii=False),False,meta['source_index'],ctx)

@pytest.mark.parametrize('value',['low','低 risk','高。'])
def test_risk_exception_is_only_the_explicit_chinese_short_labels(actual,value):
 row,ctx,_,meta=setup(actual);body=json.loads(row['raw_content'][1]);body['close_forecast']['extreme_move_risk']=value
 with pytest.raises(ValueError,match='核心分析文案必须为中文'):a._parse_result(json.dumps(body,ensure_ascii=False),False,meta['source_index'],ctx)


def test_actual_invalidation_physical_consequence_is_a_condition(actual):
 row,ctx,p,_=setup(actual,True);body=json.loads(row['raw_content'][1]);claim=body['detail']['invalidation_conditions'][0]
 out={'detail':{'invalidation_conditions':[claim]}}
 s._check_unknown_as_fact(out,ctx,p);s._check_gassco_physical_state(out,ctx,p)
 for key in ['summary','evidence']:
  statement={'analysis':{key:[claim] if key=='evidence' else claim}}
  with pytest.raises(ValueError):s._check_unknown_as_fact(statement,ctx,p)
  with pytest.raises(ValueError):s._check_gassco_physical_state(statement,ctx,p)

@pytest.mark.parametrize('claim',[
 '[N1]已确认当前实际流量保持不变，因此该结论失效。',
 '[N1]当前实际流量未受影响，因此该结论失效。',
 '[N1]事件被取消，实际流量已确认保持不变。',
])
def test_invalidation_does_not_become_blanket_current_observation_exemption(actual,claim):
 _,ctx,p,_=setup(actual,True);out={'detail':{'invalidation_conditions':[claim]}}
 with pytest.raises(ValueError):s._check_unknown_as_fact(out,ctx,p)


def test_invalidation_does_not_waive_units_or_pricing(actual):
 _,ctx,p,_=setup(actual,True)
 with pytest.raises(ValueError):s._check_ttf_price_unit_identity({'detail':{'invalidation_conditions':['[N1]TTF价格上涨1.838 EUR/mcm。']}},ctx,p)
 with pytest.raises(ValueError):s._check_priced_in({'detail':{'invalidation_conditions':['[N1]消息已计价，所以条件失效。']}},ctx,p)


@pytest.mark.parametrize('tail,allowed',[
 ('不能合计20.6mcm/d。',True),
 ('合计20.6mcm/d，若需求上升价格可能上涨。',False),
])
def test_plural_pair_warning_does_not_assert_a_sum_and_later_condition_cannot_erase_it(actual,tail,allowed):
 row,ctx,p,_=setup(actual);body=json.loads(row['raw_content'][0])
 claim='两条未来维护公告分别有7.6和13mcm/d容量损失，'+tail
 for section in ['news_impact','analysis']:body[section]['summary']=claim
 if allowed:a._validate_notice_source_facts(body,ctx,p)
 else:
  with pytest.raises(ValueError):a._validate_notice_source_facts(body,ctx,p)
