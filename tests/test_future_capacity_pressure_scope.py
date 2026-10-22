"""Finite controls for the two real provenance-pilot findings; no model calls."""
import asyncio,copy,hashlib,json,re
from datetime import datetime
from pathlib import Path
import pytest
from test_production_price_unit_scopes import verified_historical_bytes
from ttf_news import analysis as a,semantics as s
D=Path(__file__).resolve().parents[1];R=next(p for p in (D,*D.parents) if (p/'pyproject.toml').is_file())
FIX=json.loads((Path(__file__).parent/'fixtures/future_capacity_pressure_actual_v2_3.json').read_text())
BY={row['case']:row for row in FIX['records']}
def c():return copy.deepcopy(BY['ninth_manual_pilot_2']['input_snapshot'])
def prepare():ctx=c();p,m=a.prepare_context(ctx);return ctx,p,m

def captured_legacy_prepared(key,context):
 rows=json.loads((Path(__file__).parent/'fixtures/legacy_manual_prepared_1aeb_v2_3.json').read_text())['records']
 row=next(item for item in rows if item['key']==key)
 assert hashlib.sha256(a._json(context).encode()).hexdigest()==row['input_sha256']
 assert hashlib.sha256(a._json(row['prepared']).encode()).hexdigest()==row['prepared_sha256']
 assert hashlib.sha256(a._json(row['source_index']).encode()).hexdigest()==row['source_index_sha256']
 return copy.deepcopy(row['prepared']),copy.deepcopy(row['metadata'])

def test_actual_manual_pilot_both_drafts_now_reject_the_specific_future_current_window_claim():
 for name in ['ninth_manual_pilot_1','ninth_manual_pilot_2']:
  row=BY[name];assert hashlib.sha256(row['raw_content'].encode()).hexdigest()==row['raw_sha256']
  ctx=copy.deepcopy(row['input_snapshot']);p,m=a.prepare_context(ctx);out=json.loads(row['raw_content']);before=copy.deepcopy(out)
  with pytest.raises(ValueError,match='未来公告的容量下降') as error:s._check_notice_settlement_timing(out,ctx,p,source_index=m["source_index"])
  stamp=re.search(r'公告开始([^，]+)',str(error.value))[1]
  assert datetime.strptime(stamp,'%Y-%m-%dT%H:%M:%S%z')==datetime.strptime('2026-10-22T06:00:00+0200','%Y-%m-%dT%H:%M:%S%z')
  assert out==before

@pytest.mark.parametrize('claim',[
 '[N0]本窗口内计划维护导致可用产能下降7.6mcm/d。',
 '[N1]在本窗口内可用容量减少7.6mcm/d。',
 '[N0]将在本窗口内导致可用产能下降。',
 '本窗口内Troll油气田及其他设施因年度维护计划导致可用产能下降，形成供给约束。',
 '在本窗口内，Troll油气田及其他设施因年度维护计划导致可用产能分别下降7.6mcm/d和13mcm/d。',
 '若需求变化可能影响气价，但[N0]本窗口内可用产能已下降7.6mcm/d。',
])
def test_future_notice_is_not_current_window_effective_capacity(claim):
 ctx,p,m=prepare();out=json.loads(BY['ninth_manual_pilot_2']['raw_content']);out['analysis']['summary']=claim
 with pytest.raises(ValueError,match='未来公告的容量下降'):s._check_notice_settlement_timing({'analysis':{'summary':claim,'evidence':out['analysis']['evidence']}},ctx,p,source_index=m["source_index"])

@pytest.mark.parametrize('claim',[
 '[N0]本窗口内公布未来计划，10月22日开始后可用产能下降7.6mcm/d。',
 '本窗口内发布[N0]未来维护计划，届时可用容量减少7.6mcm/d。',
 '本窗口内预计[N0]将于22日开始维护，届时可用产能下降。',
 '本窗口内公布[N0]未来维护安排，预计届时可用容量下降。',
 '[N0]未来计划安排容量不可用7.6mcm/d，预期影响本窗口TTF价格。',
 '[N0]预计未来容量约束可能抬升本窗口TTF风险溢价。',
 '若[N0]提前至本窗口内生效，可用容量下降的时点需要重新核实。',
 '不能说[N0]本窗口内可用产能已下降7.6mcm/d。',
 '[N2]本窗口内可用产能下降13mcm/d，实际流量仍未知。',
])
def test_publication_future_expectations_conditions_and_actual_current_notice_remain_separate(claim):
 ctx,p,m=prepare();s._check_notice_settlement_timing({'analysis':{'summary':claim}},ctx,p,source_index=m["source_index"])


def test_future_start_inside_window_keeps_asof_and_plan_modality_distinct():
 ctx=c();ctx['window_news'][1]['event_start']='2026-10-21T12:00:00Z';p,m=a.prepare_context(ctx)
 s._check_notice_settlement_timing({'analysis':{'summary':'[N2]本窗口内将于12:00开始，可用容量下降13mcm/d，实流未知。'}},ctx,p,source_index=m["source_index"])
 with pytest.raises(ValueError):s._check_notice_settlement_timing({'analysis':{'summary':'[N2]本窗口内可用产能已经下降13mcm/d。'}},ctx,p,source_index=m["source_index"])

@pytest.mark.parametrize('text',[
 'TTF供给约束压力上升','供给约束上升','供应压力上升','供气压力增加','供应风险上升',
 'Supply constraint pressure rises.','Gas supply pressure increases.',
])
def test_abstract_supply_constraint_risk_pressure_change_is_not_physical_supply_increase(text):
 assert not any(row['mechanism']=='supply_increase' for row in s._matching_mechanisms(text))
 row={'title':'European market information','summary':text,'source':'eia','published_at':'2026-10-20T10:00:00Z'}
 assert not any(x['mechanism']=='supply_increase' for x in s.extract_mechanisms(row))
 ctx=c();ctx['current_news']=row;ctx['window_news']=[];p,_=a.prepare_context(ctx)
 assert not any(x['mechanism']=='supply_increase' for x in p['current_news'].get('mechanism_candidates',[]))

@pytest.mark.parametrize('text,mechanism,pressure',[
 ('欧洲实际供应增加40mcm/d，TTF价格上升。','supply_increase','down'),
 ('欧洲实际供应减少40mcm/d，TTF价格下降。','supply_decrease','up'),
 ('供应压力导致欧洲实际供气增加40mcm/d。','supply_increase','down'),
 ('供给约束压力上升，但实际供应减少40mcm/d。','supply_decrease','up'),
 ('欧洲需求量增加15%，价格下降。','demand_increase','up'),
 ('欧洲需求量下降15%，价格上升。','demand_decrease','down'),
 ('Supply pressure increases but actual supply increases40mcm/d.','supply_increase','down'),
])
def test_mask_is_only_the_abstract_object_not_a_later_actual_quantity_or_price_sign(text,mechanism,pressure):
 signals=s._matching_mechanisms(text)
 assert any(x['mechanism']==mechanism and x['pressure']==pressure for x in signals)
 if '供应减少' in text or 'supply decreases' in text:assert not any(x['mechanism']=='supply_increase' for x in signals)
 if '需求量下降' in text:assert not any(x['mechanism']=='demand_increase' for x in signals)


def test_current_first_correct_bullish_constraint_phrase_loses_only_the_false_physical_increase_error():
 row=BY['ninth_current_pilot_1'];ctx=copy.deepcopy(row['input_snapshot']);p,m=a.prepare_context(ctx);out=json.loads(row['raw_content'])
 s._check_evidence_buckets(out)
 errors=s.collect_analysis_semantic_errors(out,ctx,p,source_index=m["source_index"])
 assert errors and not any('证据栏与明确单向供需机制相反' in x for x in errors)
 assert any('流量' in x or '供需冲击' in x for x in errors)


def full_errors(out,ctx):
 p,m=a.prepare_context(ctx);errors=s.collect_analysis_semantic_errors(out,ctx,p,source_index=m["source_index"])
 for check in [lambda:a._validate_notice_source_facts(out,ctx,p),lambda:a._validate_prediction_semantics(out,ctx,False),lambda:a._validate_historical_reactions(out,p),lambda:a._validate_priced_in(out,p)]:
  try:check()
  except (ValueError,TypeError) as exc:errors.append(str(exc))
 return errors

@pytest.mark.parametrize('row',FIX['records'],ids=lambda x:x['case'])
def test_actual_originals_and_pilot_wrong_bodies_remain_rejected(row):
 verified_historical_bytes(R/row['record']['file'],row['record']['sha256'],project_root=R)
 assert hashlib.sha256(row['raw_content'].encode()).hexdigest()==row['raw_sha256']
 out=json.loads(row['raw_content']);before=copy.deepcopy(out);assert full_errors(out,copy.deepcopy(row['input_snapshot']));assert out==before

OLD=json.loads((Path(__file__).parent/'fixtures/future_capacity_pressure_old_bad_v2_3.json').read_text())['records']
@pytest.mark.parametrize('row',OLD,ids=lambda x:x['case'])
def test_old_twenty_one_bad_bodies_still_rejected_exactly(row):
 assert hashlib.sha256(row['raw_content'].encode()).hexdigest()==row['raw_sha256']
 out=json.loads(row['raw_content']);assert full_errors(out,copy.deepcopy(row['input_snapshot']))

@pytest.mark.parametrize('change',['no_index','missing_title','missing_summary','reason_only','different_notice','future_known','ambiguous_interval'])
def test_missing_or_nonidentical_original_source_cannot_borrow_the_future_clock(change):
 ctx=c()
 if change=='missing_title':ctx['current_news']['title']=ctx['window_news'][0]['title']=None
 if change=='missing_summary':ctx['current_news']['summary']=None
 if change=='reason_only':ctx['current_news']['summary']=ctx['window_news'][0]['summary']='Reason: Yearly maintenance. Remarks: Extended maintenance.'
 if change=='different_notice':ctx['current_news']['summary']=ctx['current_news']['summary'].replace('2026-252-A','different-current-notice').replace('2026-10-22T06:00:00+0200 to 2026-10-23T06:00:00+0200','2026-10-21T06:00:00+0200 to 2026-10-22T06:00:00+0200')
 if change=='future_known':ctx['window_news'][0]['known_at']='2030-01-01T00:00:00Z'
 if change=='ambiguous_interval':
  extra=copy.deepcopy(ctx['window_news'][0]);extra['id']='conflicting-original';extra['event_start']='2026-10-23T04:00:00Z';extra['event_end']='2026-10-24T04:00:00Z';ctx['window_news'].append(extra)
 prepared,meta=captured_legacy_prepared('future_'+change,ctx)
 index=None if change=='no_index' else meta['source_index']
 s._check_notice_settlement_timing({'analysis':{'summary':'[N0]本窗口内公告可用产能下降7.6mcm/d，实际流量未知。'}},ctx,prepared,source_index=index)


def test_source_identity_lookup_uses_indexed_original_id_not_context_row_position():
 ctx,p,m=prepare();ctx['window_news'].reverse()
 with pytest.raises(ValueError,match='未来公告的容量下降'):
  s._check_notice_settlement_timing({'analysis':{'summary':'[N0]本窗口内可用产能下降7.6mcm/d。'}},ctx,p,source_index=m['source_index'])


def test_fresh_analyze_and_diagnostic_collector_explicitly_receive_the_same_metadata_index(monkeypatch):
 ctx=c();raw=BY['ninth_manual_pilot_2']['raw_content'];received=[]
 for name in ['validate_analysis_semantics','collect_analysis_semantic_errors']:
  original=getattr(s,name)
  def wrapped(*args,_name=name,_original=original,**kwargs):
   assert 'source_index' in kwargs and kwargs['source_index']
   received.append((_name,copy.deepcopy(kwargs['source_index'])))
   return _original(*args,**kwargs)
  monkeypatch.setattr(s,name,wrapped)
 class Replay(a.NewsAnalyst):
  def __init__(self):super().__init__();self.requests=[]
  async def _request(self,system,data,detail):
   self.requests.append(copy.deepcopy(data));return {'message':{'content':raw},'done_reason':'stop','prompt_eval_count':100,'eval_count':100}
 model=Replay()
 with pytest.raises(a.AnalysisError) as error:asyncio.run(model.analyze(ctx))
 assert len(model.requests)==2
 assert {name for name,_ in received}=={'validate_analysis_semantics','collect_analysis_semantic_errors'}
 assert all(index==a.prepare_context(ctx)[1]['source_index'] for _,index in received)
 assert all('source_index' not in request for request in model.requests)
 assert any('未来公告的容量下降' in issue for call in error.value.audit['model_calls'] for issue in call['validation_errors'])

@pytest.mark.parametrize('mode',['single_known','conflicting_known','missing_end'])
def test_borrowed_interval_uses_normalized_operational_utc_clocks_and_is_complete_unique(mode):
 ctx=c();p,m=captured_legacy_prepared('future_base',ctx);original=ctx['window_news'][0]
 original['operational']={'event_start_utc':original.pop('event_start'),'event_end_utc':original.pop('event_end')}
 if mode=='missing_end':original['operational'].pop('event_end_utc')
 if mode=='conflicting_known':
  extra=copy.deepcopy(original);extra['id']='different-operational-interval';extra['operational'].update(event_start_utc='2026-10-23T04:00:00Z',event_end_utc='2026-10-24T04:00:00Z');ctx['window_news'].append(extra)
  card=copy.deepcopy(p['window_news'][0]);card['id']='N3';p['window_news'].append(card)
  m['source_index'].append({'alias':'N3','id':extra['id'],'version':extra['version']})
 out={'analysis':{'summary':'[N0]本窗口内可用产能下降7.6mcm/d。'}}
 if mode=='single_known':
  with pytest.raises(ValueError,match='未来公告的容量下降'):s._check_notice_settlement_timing(out,ctx,p,source_index=m['source_index'])
 else:s._check_notice_settlement_timing(out,ctx,p,source_index=m['source_index'])
