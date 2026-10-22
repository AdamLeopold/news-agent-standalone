"""Immutable actual price-unit and local flow/degree-scope regressions."""
import copy
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
from ttf_news.analysis import prepare_context
from ttf_news import semantics as candidate
R=ROOT/'reports/repair_2026-10-20'

def decoded(value):return json.loads(value) if isinstance(value,str) else copy.deepcopy(value)

@pytest.fixture(scope='module')
def frozen():
 d=json.loads((Path(__file__).parent/'fixtures/fifth_production_scopes_v2_3.json').read_text())
 for raw,expected in zip(d['manual']['raw_content'],d['manual']['raw_sha256']):
  assert hashlib.sha256(raw.encode()).hexdigest()==expected
 assert hashlib.sha256(d['current']['raw_content'].encode()).hexdigest()==d['current']['raw_sha256']
 return d

@pytest.fixture(scope='module')
def actual(frozen):
 d=frozen['manual'];return decoded(d['input_snapshot']),[json.loads(raw) for raw in d['raw_content']]

@pytest.fixture(scope='module')
def live(frozen):
 d=frozen['current'];return decoded(d['input_snapshot']),json.loads(d['raw_content'])


def test_actual_fifth_second_is_scoped_epistemic_not_observed_zero(actual):
 ctx,results=actual;p,_=prepare_context(ctx)
 assert candidate.collect_analysis_semantic_errors(results[1],ctx,p)==[]
 assert candidate.collect_analysis_semantic_errors(results[0],ctx,p)  # real first errors retained

@pytest.mark.parametrize('statement',[
 '截至分析时点，Gassco Troll计划维护尚未开始，未见实际流量损失。',
 'Gassco Troll计划尚未开始，尚未观察到实际流量损失。',
 'Gassco Troll计划维护未开始，未确认实际流量损失。',
])
def test_local_epistemic_physical_clause_allowed(actual,statement):
 ctx,_=actual;p,_=prepare_context(ctx)
 candidate._check_unknown_as_fact({'analysis':{'summary':statement}},ctx,p)

@pytest.mark.parametrize('statement',[
 '未见实际流量损失，但实际流量保持不变。',
 '未确认实际流量损失，但实际流量保持不变。',
 'Gassco Troll计划维护未开始，实际流量保持不变。',
 '未见实际流量损失；实际流量保持不变。',
 'Gassco Troll公告缺少流量资料，因此实际流量保持不变。',
 'Gassco Troll计划维护未开始，欧洲实际流量没有损失。',
])
def test_epistemic_prefix_does_not_hide_later_or_global_measured_claim(actual,statement):
 ctx,_=actual;p,_=prepare_context(ctx)
 with pytest.raises(ValueError):candidate._check_unknown_as_fact({'analysis':{'summary':statement}},ctx,p)

@pytest.mark.parametrize('claim',['无显著供需冲击','没有明显供应影响'])
def test_future_news_pressure_degree_not_measurement(actual,claim):
 ctx,_=actual;p,_=prepare_context(ctx)
 result={'analysis':{'evidence':['[N1]未来容量风险','[N2]未来容量风险']},'close_forecast':{'summary':f'基于报价上升和{claim}，预计价格可能上涨。'}}
 candidate._check_gassco_physical_state(result,ctx,p)

@pytest.mark.parametrize('state',['active','dismissed','elapsed'])
def test_degree_not_future_scope_escape_for_unknown_active_cancelled_ended(actual,state):
 ctx,_=actual;ctx=copy.deepcopy(ctx)
 for row in ctx['window_news']:
  if row.get('source')=='gassco':
   row['event_start']='2026-10-20T04:00:00Z'
   if state=='dismissed':row['event_status']='Dismissed';row['title']=row['title'].replace('Active','Dismissed')
   if state=='elapsed':row['event_end']='2026-10-20T08:00:00Z'
 p,_=prepare_context(ctx)
 body={'analysis':{'evidence':['[N1]公告容量风险','[N2]公告容量风险']},'close_forecast':{'summary':'无显著供需冲击'}}
 with pytest.raises(ValueError):candidate._check_gassco_physical_state(body,ctx,p)

@pytest.mark.parametrize('claim',[
 '欧洲总体实际流量无显著供应影响',
 '没有实际流量损失',
 '缺乏实际流量确认，因此无显著供需冲击',
 '新闻未产生可观测价格反应',
 '截至目前无价格反应',
])
def test_degree_or_future_words_never_hide_explicit_bad_observation(actual,claim):
 ctx,_=actual;p,_=prepare_context(ctx)
 res={'analysis':{'evidence':['[N1]未来容量风险','[N2]未来容量风险']},'close_forecast':{'summary':claim}}
 errors=candidate.collect_analysis_semantic_errors(res,ctx,p)
 assert errors


def test_actual_price_unit_wrong_is_newly_rejected(live):
 ctx,result=live;p,_=prepare_context(ctx)
 with pytest.raises(ValueError,match='EUR/mwh'):
  candidate._check_ttf_price_unit_identity(result,ctx,p)
 assert any('单位' in e for e in candidate.collect_analysis_semantic_errors(result,ctx,p))

@pytest.mark.parametrize('unit',['EUR/MWh','€/MWh','欧元/兆瓦时','EUR per MWh','欧元/ MWh'])
def test_unit_spelling_preserved_energy_identity(live,unit):
 ctx,result=live;p,_=prepare_context(ctx)
 body=copy.deepcopy(result);body['close_forecast']['basis']=f'参考价格上涨1.733 {unit}，据此作条件预测。'
 candidate._check_ttf_price_unit_identity(body,ctx,p)

@pytest.mark.parametrize('unit',['EUR/mcm','EUR/m3','欧元/百万立方米','EUR/MW','USD/MWh','USD/MMBtu'])
def test_quote_currency_and_denominator_not_silently_replaced(live,unit):
 ctx,result=live;p,_=prepare_context(ctx)
 for path in ['basis','summary']:
  body=copy.deepcopy(result);body['close_forecast'][path]=f'TTF价格上涨1.733 {unit}'
  with pytest.raises(ValueError,match='单位'):candidate._check_ttf_price_unit_identity(body,ctx,p)

@pytest.mark.parametrize('claim',[
 '设施公告不可用容量13.0mcm/d，若供给风险扩大则影响气价。',
 '项目合同金额200M EUR，为长期建设支出。',
 'TTF参考价格为86.795 EUR/MWh，供应公告容量为13mcm/d。',
 'TTF报价单位不是1 EUR/mcm，应保持EUR/MWh。',
])
def test_nonprice_quantity_or_unit_warning_unchanged(live,claim):
 ctx,_=live;p,_=prepare_context(ctx)
 candidate._check_ttf_price_unit_identity({'analysis':{'summary':claim}},ctx,p)


def test_filtered_reference_does_not_recover_future_unit(live):
 ctx,_=live
 candidate._check_ttf_price_unit_identity({'close_forecast':{'basis':'参考价上涨1.733 EUR/mcm'}},ctx,{'price_reference':{}})


def verified_historical_bytes(path,expected_source_sha,project_root=ROOT):
 """Exact private bytes or the strict, complete derived publication map."""
 path=Path(path);project_root=Path(project_root)
 assert path.resolve().is_relative_to(project_root.resolve())
 original=path.read_bytes()
 actual_sha=hashlib.sha256(original).hexdigest()
 if actual_sha==expected_source_sha:
  return original  # Private historical identity remains the strict fast path.
 manifest=project_root/'reports/publication_manifest.json'
 assert manifest.is_file(),'Historical bytes differ without an explicit publication manifest'
 helper_path=ROOT/'scripts/repair-validation-report.py'
 spec=importlib.util.spec_from_file_location('ttf_historical_test_publication_map',helper_path)
 helper=importlib.util.module_from_spec(spec)
 sys.modules[spec.name]=helper
 spec.loader.exec_module(helper)
 from unittest.mock import patch
 with patch.object(helper,'ROOT',project_root):
  # Constructor validates every mapped public artifact, path and public SHA.
  mapped=helper.PublicationManifest(manifest)
  assert mapped.identity(path)==expected_source_sha,'Published source identity differs from historical ledger'
 assert path.read_bytes()==original,'Historical artifact changed while validating publication identity'
 return original


def load_old(row):
 location=row.get('source_record') or row.get('source_fixture')
 raw=verified_historical_bytes(ROOT/location['file'],location['sha256'])
 d=json.loads(raw)
 ctx=decoded(d['input_snapshot'])
 body=d.get('raw_result') or d.get('raw_content') or d.get('result')
 return ctx,decoded(body)

@pytest.mark.parametrize('index',range(14))
def test_all_fourteen_known_bad_original_bodies_still_rejected(index):
 rows=json.loads((R/'risk_polarity_original_accepted_defects_gate_revalidation_v2_3.json').read_text())['records']
 ctx,result=load_old(rows[index]);p,_=prepare_context(ctx)
 assert candidate.collect_analysis_semantic_errors(result,ctx,p)

@pytest.mark.parametrize('change',[
 {'observed_at':'2026-10-21T02:00:00Z'},
 {'available_at':'2026-10-21T02:00:00Z'},
 {'fetched_at':'2026-10-21T02:00:00Z'},
 {'contract':'another contract'},
 {'value':999.0},
])
def test_unit_recovery_never_borrows_filtered_changed_or_future_quote(live,change):
 ctx,_=live;p,_=prepare_context(ctx)
 # Exercise the legacy compressed shape only; fresh requests now retain unit.
 p['price_reference']['intraday'].pop('unit',None)
 changed=copy.deepcopy(ctx);changed['price_reference']['intraday'].update(change)
 body={'close_forecast':{'basis':'参考价上涨1.733 EUR/mcm'}}
 # The candidate has no authority to reintroduce a discarded unit/quote.
 candidate._check_ttf_price_unit_identity(body,changed,p)


def test_source_supported_actual_no_loss_is_still_allowed(actual):
 ctx,_=actual;ctx=copy.deepcopy(ctx)
 source={'id':'meter','title':'Troll metered flow measurement','summary':'Troll实际计量流量保持不变，没有损失。',
 'source':'meter','published_at':ctx['as_of'],'known_at':ctx['as_of']}
 ctx['window_news'].append(source)
 p,_=prepare_context(ctx)
 candidate._check_unknown_as_fact({'analysis':{'summary':'Troll实际流量保持不变。'}},ctx,p)


@pytest.mark.parametrize('claim',[
 '若维护没有提前开始，则实际流量没有损失。',
 '若该维护尚未开始，实际流量没有损失。',
])
def test_existing_condition_consequent_scope_across_comma_is_retained(actual,claim):
 ctx,_=actual;p,_=prepare_context(ctx)
 candidate._check_unknown_as_fact({'analysis':{'summary':claim}},ctx,p)

@pytest.mark.parametrize('claim',[
 '若需求增加可能推高价格，但实际流量保持不变。',
 '未核实实际流量损失，然而实际流量保持不变。',
])
def test_condition_or_epistemic_does_not_cross_contrast_to_new_observation(actual,claim):
 ctx,_=actual;p,_=prepare_context(ctx)
 with pytest.raises(ValueError):candidate._check_unknown_as_fact({'analysis':{'summary':claim}},ctx,p)


def test_future_cancelled_status_not_degree_exception_even_if_title_unchanged(actual):
 ctx,_=actual;ctx=copy.deepcopy(ctx)
 for row in ctx['window_news']:
  if row.get('source')=='gassco':row['event_status']='Dismissed'
 p,_=prepare_context(ctx)
 body={'analysis':{'evidence':['[N1]容量公告','[N2]容量公告']},'close_forecast':{'summary':'无显著供需冲击'}}
 with pytest.raises(ValueError):candidate._check_gassco_physical_state(body,ctx,p)

@pytest.mark.parametrize('name,value,unit',[('JKM',12,'USD/MMBtu'),('NBP',90,'GBP/therm'),('NBP',90,'英镑/therm'),('JKM',1.733,'USD/MMBtu')])
def test_known_different_market_unit_does_not_become_ttf(live,name,value,unit):
 ctx,_=live;ctx=copy.deepcopy(ctx)
 ctx['window_news'].append({'id':'foreign','title':f'{name} market observation','summary':f'{name} gas price is {value} {unit}.',
 'source':'independent quote','published_at':ctx['as_of'],'known_at':ctx['as_of']})
 p,_=prepare_context(ctx)
 body={'close_forecast':{'basis':f'{name}价格为{value} {unit}，作为其他市场背景，TTF基准为86.795 EUR/MWh。'}}
 candidate._check_ttf_price_unit_identity(body,ctx,p)
 body['close_forecast']['basis']+=f'但TTF参考价上涨1.733 EUR/mcm。'
 with pytest.raises(ValueError,match='单位'):candidate._check_ttf_price_unit_identity(body,ctx,p)


def test_foreign_source_cannot_be_borrowed_after_asof_or_for_wrong_instrument(live):
 ctx,_=live;ctx=copy.deepcopy(ctx)
 ctx['window_news'].append({'id':'foreign','title':'JKM market observation','summary':'JKM gas price is 12 USD/MMBtu.',
 'source':'independent quote','published_at':'2026-10-21T03:00:00Z','known_at':'2026-10-21T03:00:00Z'})
 p,_=prepare_context(ctx)
 with pytest.raises(ValueError,match='JKM'):candidate._check_ttf_price_unit_identity({'close_forecast':{'basis':'JKM价格为12 USD/MMBtu。'}},ctx,p)

@pytest.mark.parametrize('claim',[
 'Actual flows remain unconfirmed, but actual flows are unaffected.',
 'If maintenance is postponed, but actual flows are unchanged.',
])
def test_english_comma_contrast_does_not_hide_measured_zero(actual,claim):
 ctx,_=actual;p,_=prepare_context(ctx)
 with pytest.raises(ValueError):candidate._check_unknown_as_fact({'analysis':{'summary':claim}},ctx,p)


def test_english_condition_consequent_is_still_conditional(actual):
 ctx,_=actual;p,_=prepare_context(ctx)
 candidate._check_unknown_as_fact({'analysis':{'summary':'If future metering confirms this scenario, actual flows are unaffected.'}},ctx,p)

@pytest.mark.parametrize('claim',['N3无显著供需冲击','[N3]无显著供需冲击','Nyhamna无显著供需冲击'])
def test_degree_exception_cannot_borrow_future_notices_for_other_source(actual,claim):
 ctx,_=actual;ctx=copy.deepcopy(ctx)
 ctx['window_news'].append({'id':'active-other','title':'Gassco Nyhamna: Planned Other unavailability (Active)',
 'summary':'Technical capacity: 79.8 mcm/d; available: 60.0; unavailable: 19.8.', 'source':'gassco',
 'event_start':'2026-10-20T04:00:00Z','event_end':'2026-10-23T04:00:00Z','event_status':'Active',
 'published_at':ctx['as_of'],'known_at':ctx['as_of']})
 p,_=prepare_context(ctx)
 # N3 is this appended active notice; no Unicode-boundary loss may turn it
 # into the N1/N2 future-only degree exception.
 assert p['window_news'][-1]['id']=='N3'
 body={'analysis':{'evidence':['[N1]未来容量风险','[N2]未来容量风险']},'close_forecast':{'summary':claim}}
 with pytest.raises(ValueError):candidate._check_gassco_physical_state(body,ctx,p)


def candidate_analysis():
 from ttf_news import analysis
 return analysis


def test_unit_input_preserved_only_if_supplied_and_quote_known(live):
 ctx,_=live;mod=candidate_analysis()
 p,_=mod.prepare_context(ctx)
 assert p['price_reference']['intraday']['unit']=='EUR/MWh'
 for key in ['observed_at','available_at','fetched_at']:
  changed=copy.deepcopy(ctx);changed['price_reference']['intraday'][key]='2026-10-21T03:00:00Z'
  p,_=mod.prepare_context(changed)
  assert not p['price_reference'].get('intraday')
 absent=copy.deepcopy(ctx);absent['price_reference']['intraday'].pop('unit')
 p,_=mod.prepare_context(absent);assert 'unit' not in p['price_reference']['intraday']


@pytest.mark.parametrize('amount',[1.733,2.0])
def test_known_transport_tariff_is_not_ttf_price_even_with_same_numeric_delta(live,amount):
 ctx,_=live;ctx=copy.deepcopy(ctx)
 ctx['window_news'].append({'id':'tariff','title':'Pipeline transport tariff notice',
 'summary':f'The transport tariff is {amount} EUR/mcm.', 'source':'operator tariff',
 'published_at':ctx['as_of'],'known_at':ctx['as_of']})
 p,_=prepare_context(ctx)
 good={'close_forecast':{'basis':f'输气费率为{amount} EUR/mcm，TTF参考变动为1.733 EUR/MWh；项目费为200M EUR，实流恢复40mcm/d。'}}
 candidate._check_ttf_price_unit_identity(good,ctx,p)
 good['close_forecast']['basis']+='但TTF参考价格上涨1.733 EUR/mcm。'
 with pytest.raises(ValueError,match='单位'):candidate._check_ttf_price_unit_identity(good,ctx,p)


@pytest.mark.parametrize('claim',[
 'TTF价格不是下跌而是上涨1.733 EUR/mcm。',
 'TTF价格并非下跌而是上涨1.733 EUR/mcm。',
])
def test_negating_price_direction_is_not_a_unit_warning(live,claim):
 ctx,_=live;p,_=prepare_context(ctx)
 with pytest.raises(ValueError,match='单位'):candidate._check_ttf_price_unit_identity({'close_forecast':{'basis':claim}},ctx,p)


@pytest.mark.parametrize('change',['none','missing_manifest','wrong_source','wrong_public','wrong_path','tampered_file','unrelated_stale_entry'])
def test_historical_copy_requires_exact_complete_publication_identity(tmp_path,change):
 source=b'{"input_snapshot":{},"raw_result":{}}\n'
 source_sha=hashlib.sha256(source).hexdigest()
 path=tmp_path/'reports/historical.json';path.parent.mkdir();path.write_text('{\n "input_snapshot": {}, "raw_result": {}\n}\n')
 public_sha=hashlib.sha256(path.read_bytes()).hexdigest()
 entry={'source_bytes_sha256':source_sha,'public_bytes_sha256':public_sha,'is_derived':True,'transformation':'public_safe_json'}
 mapping={'schema_version':'ttf-publication-manifest-v1','originals_modified':False,'manifest_self_excluded':True,'artifacts':{'reports/historical.json':entry}}
 manifest=tmp_path/'reports/publication_manifest.json'
 if change=='wrong_source':entry['source_bytes_sha256']='f'*64
 if change=='wrong_public':entry['public_bytes_sha256']='f'*64
 if change=='wrong_path':mapping['artifacts']['reports/another.json']=mapping['artifacts'].pop('reports/historical.json')
 if change=='unrelated_stale_entry':mapping['artifacts']['reports/missing.json']=dict(entry)
 if change!='missing_manifest':manifest.write_text(json.dumps(mapping))
 if change=='tampered_file':path.write_text('{"different":true}')
 if change=='none':
  assert verified_historical_bytes(path,source_sha,tmp_path)==path.read_bytes()
 else:
  with pytest.raises((AssertionError,ValueError)):
   verified_historical_bytes(path,source_sha,tmp_path)


def test_exact_original_historical_bytes_need_no_public_copy_map(tmp_path):
 path=tmp_path/'original.json';path.write_text('{"unchanged":true}')
 source_sha=hashlib.sha256(path.read_bytes()).hexdigest()
 assert verified_historical_bytes(path,source_sha,tmp_path)==path.read_bytes()
 path.write_text('{"changed":true}')
 with pytest.raises(AssertionError):verified_historical_bytes(path,source_sha,tmp_path)
