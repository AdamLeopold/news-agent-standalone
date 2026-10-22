"""Actual evidence-slot and source-bound physical/price scope regressions."""
import copy,hashlib,importlib.util,json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
from ttf_news.analysis import prepare_context
from ttf_news import semantics as candidate
from test_production_price_unit_scopes import verified_historical_bytes
R=ROOT/'reports/repair_2026-10-20'
def dec(v):return json.loads(v) if isinstance(v,str) else copy.deepcopy(v)

@pytest.fixture(scope='module')
def actuals():
 frozen=json.loads((Path(__file__).parent/'fixtures/evidence_slot_price_scope_actual_v2_3.json').read_text())['records']
 out={}
 for name,row in frozen.items():
  assert hashlib.sha256(row['raw_content'].encode()).hexdigest()==row['raw_sha256']
  out[name]=(dec(row['input_snapshot']),json.loads(row['raw_content']))
 return out


def test_both_actual_new_bodies_reject_without_direction_rewrite(actuals):
 for name,(ctx,result) in actuals.items():
  p,_=prepare_context(ctx);before=copy.deepcopy(result)
  errors=candidate.collect_analysis_semantic_errors(result,ctx,p)
  assert errors
  assert result==before
  if name=='real_eni':
   assert any('bullish_evidence[0]' in e and 'bearish_evidence[0]' in e for e in errors)
  else:assert any('news_impact.summary' in e and 'analysis.summary' in e for e in errors)


def test_correct_causal_slots_allow_the_same_net_flat(actuals):
 ctx,result=copy.deepcopy(actuals['real_eni']);p,_=prepare_context(ctx)
 result['news_impact']['bullish_evidence']=[result['news_impact']['bearish_evidence'][0]]
 result['news_impact']['bearish_evidence']=['[N0]项目未来供应增加→TTF下行压力']
 for section in ('news_impact','analysis'):
  result[section]['summary']=result[section]['summary'].replace('Åsgard停产导致短期容量缺口','Åsgard不可用容量公告显示短期容量风险，实际流量未知')
 result['close_forecast']['basis']=result['close_forecast']['basis'].replace('且无显著供应中断','且公告对TTF价格的压力预计有限')
 candidate._check_source_bound_evidence_slot_prices(result,ctx,p)
 assert candidate.collect_analysis_semantic_errors(result,ctx,p)==[]
 assert result['news_impact']['direction']=='flat'

@pytest.mark.parametrize('alias',['[N3]','N3','N3所述'])
def test_cjk_adjacent_source_sign_cannot_be_in_opposite_slot(actuals,alias):
 ctx,_=actuals['real_eni'];p,_=prepare_context(ctx)
 result={'news_impact':{'bearish_evidence':[alias+'Åsgard；TTF机制：供应约束导致价格上行']}}
 with pytest.raises(ValueError,match='bearish_evidence'):candidate._check_source_bound_evidence_slot_prices(result,ctx,p)

@pytest.mark.parametrize('slot,body',[
 ('bullish_evidence','[N3]Åsgard；TTF机制：供应约束导致价格上行'),
 ('bearish_evidence','[N0]未来供应增加→TTF价格下行'),
 ('bearish_evidence','[N0]若供应恢复→TTF上涨压力减弱'),
 ('bullish_evidence','[N3]供应约束→TTF下跌压力缓解'),
 ('bearish_evidence','[N0]供应增加但需求也可能增加，合力为TTF下行压力'),
 ('bullish_evidence','[N3]当时历史价格上行，作为历史背景'),
])
def test_valid_pressure_qualifiers_mixed_and_history_not_net_quotas(actuals,slot,body):
 ctx,_=copy.deepcopy(actuals['real_eni'])
 if '需求也可能增加' in body:ctx['current_news']['summary']+=' 未来需求也可能增加。'
 p,_=prepare_context(ctx)
 candidate._check_source_bound_evidence_slot_prices({'news_impact':{slot:[body],'direction':'flat'}},ctx,p)


def test_source_supported_reverse_expectation_is_a_real_channel(actuals):
 ctx,_=copy.deepcopy(actuals['real_eni'])
 ctx['current_news']['summary']+=' 新增供应规模低于此前预期，供应预期下修。'
 p,_=prepare_context(ctx)
 result={'news_impact':{'bullish_evidence':['[N0]新增供应规模低于此前预期→TTF看涨机制']}}
 candidate._check_source_bound_evidence_slot_prices(result,ctx,p)

@pytest.mark.parametrize('claim',[
 '对TTF价格暂无即时影响。',
 '项目开启，但对TTF价格暂时没有即时影响。',
 '对当前TTF价格目前无实际影响。',
 '若未来供给增加可能下行，但对TTF价格暂无即时影响。',
])
def test_exact_price_zero_scope_not_weak_news_judgement(actuals,claim):
 ctx,_=actuals['anchor_real_manual_missing_prevday'];p,_=prepare_context(ctx)
 with pytest.raises(ValueError,match='价格反应观测'):
  candidate._check_observed_absence_of_price_reaction({'analysis':{'summary':claim}},ctx,p)

@pytest.mark.parametrize('claim',[
 '预计对TTF价格暂无即时影响。',
 '若项目尚未投产，对TTF价格暂无即时影响。',
 '对TTF价格即时影响有限。',
 '对TTF价格传导较弱。',
 '对TTF价格暂无即时影响的数据。',
 '无法确认对TTF价格暂无即时影响。',
 '新闻当前供需传导弱，价格反应未知。',
 '并非对TTF价格暂无即时影响。',
])
def test_price_zero_guards_do_not_ban_model_pressure_or_epistemic_conditions(actuals,claim):
 ctx,_=actuals['anchor_real_manual_missing_prevday'];p,_=prepare_context(ctx)
 candidate._check_observed_absence_of_price_reaction({'analysis':{'summary':claim}},ctx,p)


def test_independent_asof_reaction_allows_price_absence_but_future_does_not(actuals):
 original,_=actuals['anchor_real_manual_missing_prevday']
 for at,allowed in [(original['as_of'],True),('2030-01-01T00:00:00Z',False)]:
  ctx=copy.deepcopy(original);ctx['price_reference']['announcement_price_reaction']={'observed_change':0,'source':'independent before/after quote observation','available_at':at}
  p,_=prepare_context(ctx);result={'analysis':{'summary':'对TTF价格暂无即时影响。'}}
  if allowed:candidate._check_observed_absence_of_price_reaction(result,ctx,p)
  else:
   with pytest.raises(ValueError):candidate._check_observed_absence_of_price_reaction(result,ctx,p)

@pytest.mark.parametrize('index',range(15))
def test_fifteen_old_known_bad_bodies_remain_rejected(index):
 if index==14:
  frozen=json.loads((ROOT/'tests/fixtures/fifth_production_scopes_v2_3.json').read_text())['current'];ctx=frozen['input_snapshot'];res=json.loads(frozen['raw_content'])
 else:
  rows=json.loads((R/'risk_polarity_original_accepted_defects_gate_revalidation_v2_3.json').read_text())['records'];location=rows[index].get('source_record') or rows[index].get('source_fixture');path=ROOT/location['file']
  d=json.loads(verified_historical_bytes(path,location['sha256']));ctx=dec(d['input_snapshot']);res=dec(d.get('raw_result') or d.get('raw_content') or d.get('result'))
 p,_=prepare_context(ctx)
 assert candidate.collect_analysis_semantic_errors(res,ctx,p)


@pytest.mark.parametrize('claim',[
 'Åsgard停产导致短期容量缺口。',
 'Åsgard已停产。',
])
def test_active_capacity_notice_is_not_actual_facility_shutdown(actuals,claim):
 ctx,_=actuals['real_eni'];p,_=prepare_context(ctx)
 with pytest.raises(ValueError,match='实际停产'):candidate._check_notice_entity_production_stop({'analysis':{'summary':claim}},ctx,p)

@pytest.mark.parametrize('claim',[
 'Åsgard停产公告显示不可用容量5.2mcm/d，实流未知。',
 '若Åsgard停产则未来可能推高气价。',
 '未来Åsgard停产可能形成供应风险。',
 '是否Åsgard停产尚未确认。',
])
def test_notice_description_and_conditional_future_stop_do_not_assert_actual(actuals,claim):
 ctx,_=actuals['real_eni'];p,_=prepare_context(ctx)
 candidate._check_notice_entity_production_stop({'analysis':{'summary':claim}},ctx,p)


def test_known_same_facility_actual_shutdown_confirmation_is_usable(actuals):
 original,_=actuals['real_eni']
 for name,time,ok in [('Åsgard',original['as_of'],True),('Nyhamna',original['as_of'],False),('Åsgard','2030-01-01T00:00:00Z',False)]:
  ctx=copy.deepcopy(original);ctx['window_news'].append({'id':'metered-stop','source':'independent operations report',
   'title':f'{name} independent production confirmation','summary':f'{name}已确认实际停产。','known_at':time,'published_at':time})
  p,_=prepare_context(ctx);body={'analysis':{'summary':'Åsgard停产导致短期容量缺口。'}}
  if ok:candidate._check_notice_entity_production_stop(body,ctx,p)
  else:
   with pytest.raises(ValueError):candidate._check_notice_entity_production_stop(body,ctx,p)

@pytest.mark.parametrize('claim',[
 '市场基准微升，且无显著供应中断，故维持平稳。',
 '缺乏流量数据，且无显著供应中断。',
])
def test_unknown_actual_flows_not_proven_absent_significant_interruption(actuals,claim):
 ctx,result=copy.deepcopy(actuals['real_eni']);p,_=prepare_context(ctx)
 result['close_forecast']['basis']=claim
 with pytest.raises(ValueError,match='独立无变化计量'):candidate._check_gassco_physical_state(result,ctx,p)

@pytest.mark.parametrize('claim',[
 '公告对TTF价格的压力预计有限，实流中断尚未确认。',
 '输入未确认实际供应中断。',
 '若未出现显著供应中断，则基准可能延续。',
 '无显著供应中断的证据。',
])
def test_pressure_judgement_evidence_limit_and_condition_are_not_physical_absence(actuals,claim):
 ctx,result=copy.deepcopy(actuals['real_eni']);p,_=prepare_context(ctx)
 result['close_forecast']['basis']=claim
 candidate._check_gassco_physical_state(result,ctx,p)


def test_known_same_scope_metered_no_interruption_allows_statement(actuals):
 ctx,result=copy.deepcopy(actuals['real_eni'])
 ctx['window_news'].append({'id':'metered','source':'independent metering','title':'Åsgard and Nyhamna metered gas flows',
  'summary':'Åsgard与Nyhamna实际计量流量保持不变，未发生显著供应中断。','published_at':ctx['as_of'],'known_at':ctx['as_of']})
 p,_=prepare_context(ctx);result['close_forecast']['basis']='无显著供应中断。'
 candidate._check_gassco_physical_state(result,ctx,p)



def test_prior_expectation_fact_is_not_historical_waiver_of_wrong_current_slot(actuals):
 ctx,_=copy.deepcopy(actuals['real_eni']);ctx['current_news']['summary']+=' 新增供应规模低于此前预期。'
 p,_=prepare_context(ctx)
 correct={'news_impact':{'bullish_evidence':['[N0]供应低于此前预期→TTF价格上行']}}
 candidate._check_source_bound_evidence_slot_prices(correct,ctx,p)
 for claim in ['[N0]供应低于此前预期→TTF价格上行','[N3]此前公告修订→TTF价格上行']:
  with pytest.raises(ValueError,match='bearish_evidence'):
   candidate._check_source_bound_evidence_slot_prices({'news_impact':{'bearish_evidence':[claim]}},ctx,p)
 candidate._check_source_bound_evidence_slot_prices({'news_impact':{'bearish_evidence':['[N3]当时历史价格上行，作为历史背景']}},ctx,p)

@pytest.mark.parametrize('claim',[
 'Åsgard实际生产未停止。',
 'Åsgard实际生产没有停止。',
 'Åsgard并非已确认实际停产。',
])
def test_negated_source_production_is_not_actual_stop_confirmation(actuals,claim):
 ctx,_=copy.deepcopy(actuals['real_eni'])
 ctx['window_news'].append({'id':'operations','source':'independent production report','title':'Åsgard production report',
  'summary':claim,'published_at':ctx['as_of'],'known_at':ctx['as_of']})
 p,_=prepare_context(ctx)
 with pytest.raises(ValueError):candidate._check_notice_entity_production_stop({'analysis':{'summary':'Åsgard停产导致短期容量缺口。'}},ctx,p)


def test_new_interruption_term_keeps_future_only_degree_scope(actuals):
 original,_=actuals['real_eni']
 for future in [True,False]:
  ctx=copy.deepcopy(original)
  for row in ctx['window_news']:
   if row.get('source')=='gassco':
    row['event_start']='2030-01-01T00:00:00Z' if future else '2026-10-18T00:00:00Z'
    row['event_end']='2030-01-02T00:00:00Z';row['event_status']='Active';row['title']=row['title'].replace('Dismissed','Active')
  p,_=prepare_context(ctx)
  body={'analysis':{'evidence':['[N3]公告容量风险']},'close_forecast':{'summary':'无显著供应中断。'}}
  if future:
   candidate._check_gassco_physical_state(body,ctx,p)
   for bad in ['欧洲整体实际流量无显著供应中断。','全网无显著供应中断。','无价格反应。']:
    body['close_forecast']['summary']=bad
    with pytest.raises(ValueError):candidate._check_gassco_physical_state(body,ctx,p)
  else:
   with pytest.raises(ValueError):candidate._check_gassco_physical_state(body,ctx,p)


def test_whole_collector_source_backed_expectation_gap_remains_valid(actuals):
 ctx,result=copy.deepcopy(actuals['real_eni'])
 ctx['current_news']['summary']+=' 新增供应规模低于此前预期。'
 p,_=prepare_context(ctx)
 result['news_impact']['bullish_evidence']=['[N0]供应低于此前预期→TTF价格上行',result['news_impact']['bearish_evidence'][0]]
 result['news_impact']['bearish_evidence']=[]
 for section in ('news_impact','analysis'):
  result[section]['summary']='新增供应规模低于此前预期，叠加公告容量风险，可能形成较弱上行压力。'
 result['analysis']['evidence']=list(result['news_impact']['bullish_evidence'])
 result['close_forecast']['basis']='同合约市场基准微升，公告对价格压力预计有限。'
 assert candidate.collect_analysis_semantic_errors(result,ctx,p)==[]
