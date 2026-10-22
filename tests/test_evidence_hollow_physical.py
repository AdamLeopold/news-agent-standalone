"""Narrow result-only channel regressions; source data remains immutable."""
import copy,hashlib,json
from pathlib import Path
import pytest
from ttf_news.analysis import prepare_context
from ttf_news import semantics as candidate
ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture(scope='module')
def actual():
 row=json.loads((Path(__file__).parent/'fixtures/evidence_hollow_physical_actual_v2_3.json').read_text())
 assert hashlib.sha256(row['raw_content'].encode()).hexdigest()==row['raw_sha256']
 return copy.deepcopy(row['input_snapshot']),json.loads(row['raw_content'])

def test_actual_mixed_pipeline_and_lng_channels_are_not_corporate_templates(actual):
 ctx,result=copy.deepcopy(actual);before=copy.deepcopy((ctx,result));p,_=prepare_context(ctx)
 assert candidate.collect_analysis_semantic_errors(result,ctx,p)==[]
 assert (ctx,result)==before
 assert result['news_impact']['direction']=='down'

@pytest.mark.parametrize('slot,claim',[
 ('bullish_evidence','[N0]管道输气减少20mcm/d → TTF看涨机制'),
 ('bearish_evidence','[N0]LNG再气化流量增加30mcm/d → TTF看跌机制'),
 ('bullish_evidence','[N0]管道输气实际减少20mcm/d → TTF看涨机制'),
 ('bearish_evidence','[N0]LNG再气化增加30mcm/d → TTF看跌机制'),
 ('bullish_evidence','[N0]若管道输气减少20mcm/d → TTF看涨机制'),
 ('bearish_evidence','[N0]若LNG再气化增加30mcm/d → TTF看跌机制'),
])
def test_physical_flow_changes_have_real_causal_intermediates(actual,slot,claim):
 ctx,_=actual;p,_=prepare_context(ctx)
 result={'news_impact':{slot:[claim]}}
 candidate._check_source_bound_evidence_slot_prices(result,ctx,p)

@pytest.mark.parametrize('slot,claim',[
 ('bearish_evidence','[N0]管道输气减少20mcm/d → TTF看涨机制'),
 ('bullish_evidence','[N0]LNG再气化流量增加30mcm/d → TTF看跌机制'),
 ('bearish_evidence','[N0]管道输气减少20mcm/d → TTF看跌机制'),
 ('bullish_evidence','[N0]LNG再气化增加30mcm/d → TTF看涨机制'),
])
def test_opposite_slot_or_inverted_price_endpoint_still_rejects(actual,slot,claim):
 ctx,_=actual;p,_=prepare_context(ctx)
 with pytest.raises(ValueError):candidate._check_source_bound_evidence_slot_prices({'news_impact':{slot:[claim]}},ctx,p)

@pytest.mark.parametrize('claim',[
 '[N0]公司开启天然气项目 → TTF看涨机制',
 '[N0]企业签署合同 → TTF看跌机制',
 '[N0]公司获得200百万欧元工程合同 → TTF看涨机制',
])
def test_unmediated_project_and_corporate_endpoint_templates_remain_hollow(actual,claim):
 ctx,_=actual;p,_=prepare_context(ctx)
 field='bearish_evidence' if '看跌' in claim else 'bullish_evidence'
 with pytest.raises(ValueError,match='TTF涨跌模板'):
  candidate._check_source_bound_evidence_slot_prices({'news_impact':{field:[claim]}},ctx,p)


def test_physical_arrow_has_channel_but_still_needs_source_backed_expectation_reverse(actual):
 ctx,_=copy.deepcopy(actual);p,_=prepare_context(ctx)
 result={'news_impact':{'bullish_evidence':['[N0]潜在供给↑但发现规模低于此前预期，远期预期被下修 → TTF看涨']}}
 candidate._check_source_bound_evidence_slot_prices(result,ctx,p)
 with pytest.raises(ValueError,match='物理量箭头不是TTF价格箭头|资源发现仅支持'):
  candidate._check_quantity_arrow_scope(result,ctx,{'current_news':{'id':'N0','source':'offshore_energy','title':'New gas discovery','facts':'New gas discovery may increase future supply.'}})


def test_negated_or_missing_flow_change_does_not_invent_a_channel():
 for claim in ['未确认管道输气减少20mcm/d','没有发生管道输气减少20mcm/d','未提供LNG再气化增加30mcm/d的证据']:
  assert candidate._evidence_flow_change_mechanisms(claim)==[]
