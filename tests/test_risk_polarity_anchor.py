import ast
import copy
import importlib.util
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
from ttf_news import semantics as candidate

def actual_fixture(name):
    return json.loads((Path(__file__).parent/'fixtures'/name).read_text())


def ctx(summary='公告撤销减少该公告供应风险，实际流量未知。'):
    item={'source':'gassco','title':'Gassco test: Unplanned unavailability (Dismissed)',
          'published_at':'2026-10-20T10:00:00Z','summary':summary,'event_status':'Dismissed',
          'temporal_state':'dismissed_notice','unavailable_capacity':20}
    context={'as_of':'2026-10-20T12:00:00Z','current_news':item,'window_news':[],'historical_events':[],'price_reference':{}}
    prepared={'as_of':context['as_of'],'current_news':dict(item,id='N0'),'window_news':[],'price_reference':{}}
    return context,prepared


def check(text,field='bearish_evidence',source=None):
    context,prepared=ctx(source or '公告撤销减少该公告供应风险，实际流量未知。')
    output={'news_impact':{'direction':'flat','contextual_news_score':0,field:[text]}}
    before=copy.deepcopy((output,context,prepared))
    candidate._check_supply_risk_buckets(output,context,prepared)
    candidate._check_evidence_buckets(output)
    candidate._check_causal_statement('news_impact.'+field+'[0]',text)
    assert (output,context,prepared)==before


def test_actual_accepted_body_is_rejected_only_by_new_risk_polarity_gate():
    data=actual_fixture('dismissed_risk_polarity_actual_v2_3.json');raw=data['raw_result']
    prepared=data['prepared_context'];context=data['input_snapshot'];before=copy.deepcopy((raw,prepared,context))
    errors=candidate.collect_analysis_semantic_errors(raw,context,prepared)
    assert any('供应风险降低' in error and 'bullish_evidence[0]' in error for error in errors)
    assert (raw,prepared,context)==before


def test_risk_relief_is_not_actual_supply_reduction():
    signals=candidate._validation_mechanisms('供应风险降低')
    assert [(s['pressure'],s['evidence_kind']) for s in signals]==[('down','risk_only')]
    assert not any(s['mechanism']=='supply_decrease' for s in signals)


import pytest

@pytest.mark.parametrize('verb',['降低','下降','减少','减弱','缓解','减轻','消退','移除','撤销','解除','消除'])
def test_relief_bearish_valid_bullish_wrong_symmetric(verb):
    text='[N0]仅该公告撤销，实流未知；TTF机制：供应风险'+verb
    check(text)
    with pytest.raises(ValueError,match='证据栏|证据栏与'):
        check(text,'bullish_evidence')

@pytest.mark.parametrize('verb',['上升','增加','提高','加剧','增大'])
def test_risk_increase_bullish_valid_bearish_wrong_symmetric(verb):
    text='[N0]未来计划容量约束，非实际流量；TTF机制：供气中断风险'+verb
    check(text,'bullish_evidence',source='计划未来公告使供气中断风险上升，实际流量未知。')
    with pytest.raises(ValueError):check(text,'bearish_evidence')

@pytest.mark.parametrize('text',[ '供应风险未降低','供应风险没有下降','供应风险不会降低',
    '供应风险是否降低','无法确认供应风险降低','供应风险降低仍待核实','并非供应风险降低'])
def test_negation_and_missing_confirmation_do_not_assert_risk_relief(text):
    assert candidate._validation_mechanisms(text)==[]
    check('[N0]TTF机制：'+text,'bullish_evidence')

@pytest.mark.parametrize('text,sign',[('供应实际增加40mcm/d','down'),('供应实际减少40mcm/d','up'),
    ('实际需求增加15%','up'),('取暖需求下降15%','down')])
def test_actual_supply_demand_signs_not_relabelled(text,sign):
    assert {row['pressure'] for row in candidate._validation_mechanisms(text)}=={sign}
    assert candidate._validation_mechanisms(text)==candidate._matching_mechanisms(text)


def test_risk_relief_and_actual_cut_both_preserved_not_single_sign_cooccurrence():
    values=candidate._validation_mechanisms('供应风险降低，但实际供气减少40mcm/d')
    assert {row['pressure'] for row in values}=={'up','down'}
    assert any(row['mechanism']=='supply_decrease' for row in values)


def test_grounded_mixed_demand_increase_can_support_bullish_slot_without_changing_net_flat():
    check('[N0]TTF机制：供应风险降低，但需求实际增加15%，净上行压力', 'bullish_evidence',
          source='该公告风险减轻；需求实际增加15%，欧洲寒冷天气增加取暖需求。')


def test_historical_source_quote_is_not_forced_into_a_present_pressure_label():
    check('[N0]历史资料曾称供应风险降低，当前需求增加15% → TTF上行压力','bullish_evidence',source='当前取暖需求增加15%。')
    check('[N0]历史资料称供应风险降低','bullish_evidence')


def test_historical_quote_does_not_exempt_new_opposite_mechanism():
    with pytest.raises(ValueError):check('[N0]历史材料在前；TTF机制：供应风险降低','bullish_evidence')


def test_surplus_risk_has_opposite_sign_to_shortage_risk():
    check('[N0]TTF机制：供应过剩风险上升','bearish_evidence')
    check('[N0]TTF机制：供应过剩风险降低','bullish_evidence')


def test_unknown_flow_not_converted_to_actual_recovery_and_no_default_direction():
    context,prepared=ctx();output={'news_impact':{'direction':'flat','contextual_news_score':0,
       'bearish_evidence':['[N0]仅该公告撤销；实流未知；TTF机制：供应风险降低']}}
    before=copy.deepcopy(output)
    candidate._check_supply_risk_buckets(output,context,prepared)
    assert output==before
    assert not any(s.get('evidence_kind')=='actual_flow' for s in candidate._validation_mechanisms(output['news_impact']['bearish_evidence'][0]))


def test_source_operator_risk_extraction_preserves_conditional_capacity_scope():
    context,_=ctx('No independent actual flow measurement or recovery confirmation is provided. The notice was Dismissed.')
    signals=candidate.extract_mechanisms(context['current_news'])
    assert [(row['mechanism'],row['pressure']) for row in signals]==[('capacity_risk_removed','down')]
    assert signals[0]['conditional'] is True and signals[0]['evidence_kind']=='capacity_only'


def test_same_actual_body_with_correct_risk_bucket_passes_every_semantic_gate():
    data=actual_fixture('dismissed_risk_polarity_actual_v2_3.json');raw=data['raw_result']
    raw['news_impact']['bearish_evidence']=raw['news_impact'].pop('bullish_evidence')
    raw['news_impact']['bullish_evidence']=[]
    prepared=data['prepared_context'];context=data['input_snapshot'];before=copy.deepcopy((raw,prepared,context))
    assert candidate.collect_analysis_semantic_errors(raw,context,prepared)==[]
    candidate.validate_analysis_semantics(raw,context,prepared)
    assert (raw,prepared,context)==before


@pytest.mark.parametrize('invented',[ '需求实际增加','需求可能增加','供气实际减少40mcm/d'])
def test_mixed_risk_exception_cannot_invent_an_opposing_source_fact(invented):
    with pytest.raises(ValueError):
        check('[N0]TTF机制：供应风险降低，但'+invented+'，净上行压力','bullish_evidence')


def test_distinct_cited_known_source_can_supply_opposing_countermechanism():
    context,prepared=ctx()
    row={'id':'N1','source':'eia','facts':'当前欧洲取暖需求增加15%。','published_at':'2026-10-20T11:00:00Z','known_at':'2026-10-20T11:01:00Z'}
    prepared['window_news']=[row]
    output={'news_impact':{'direction':'flat','bullish_evidence':['[N0][N1]TTF机制：供应风险降低，但取暖需求增加15%，上行压力仍有支持']}}
    candidate._check_supply_risk_buckets(output,context,prepared)
    for key in ('known_at','published_at'):
        changed=copy.deepcopy(prepared);changed['window_news'][0][key]='2026-10-20T12:01:00Z'
        with pytest.raises(ValueError):candidate._check_supply_risk_buckets(output,context,changed)


def test_uncited_other_source_and_pure_historical_quote_are_not_current_counterfact():
    context,prepared=ctx()
    prepared['window_news']=[{'id':'N1','source':'eia','facts':'当前取暖需求增加15%。'}]
    output={'news_impact':{'bullish_evidence':['[N0]TTF机制：供应风险降低，但取暖需求增加15%，净上行']}}
    with pytest.raises(ValueError):candidate._check_supply_risk_buckets(output,context,prepared)
    output['news_impact']['bullish_evidence']=['[N0][N1]TTF机制：供应风险降低，但取暖需求增加15%，净上行']
    prepared['window_news'][0]['facts']='历史报告曾称取暖需求增加15%。'
    with pytest.raises(ValueError):candidate._check_supply_risk_buckets(output,context,prepared)


def test_conditional_sourced_demand_remains_conditional_countermechanism():
    check('[N0]TTF机制：供应风险降低，但寒潮可能使取暖需求增加，存在条件上行压力','bullish_evidence',
          source='公告容量风险下降；本次未来寒潮可能增加取暖需求。')


def test_sourced_future_demand_is_not_an_actual_observed_counterfact():
    source='当前公告供应风险降低；未来寒潮可能增加取暖需求。'
    with pytest.raises(ValueError):
        check('[N0]TTF机制：供应风险降低，但需求实际增加，净上行压力','bullish_evidence',source=source)
    check('[N0]TTF机制：供应风险降低，但寒潮可能增加取暖需求，存在条件上行压力','bullish_evidence',source=source)
    check('[N0]TTF机制：供应风险降低，但取暖需求实际增加15%，存在相反上行压力','bullish_evidence',
          source='当前公告供应风险降低；当前实际取暖需求增加15%。')


def anchor_actual():
    data=actual_fixture('material_anchor_no_counter_actual_v2_3.json')
    return (data['raw_result'],data['input_snapshot'],data['prepared_context'])


def test_actual_legal_material_anchor_no_reverse_proof_is_not_a_forecast_veto():
    raw,context,prepared=anchor_actual();before=copy.deepcopy((raw,context,prepared))
    with pytest.raises(ValueError,match='不能单独取消'):
        candidate.validate_analysis_semantics(raw,context,prepared)
    assert (raw,context,prepared)==before


@pytest.mark.parametrize('direction,delta',[('up',1.76),('down',-1.76)])
def test_material_baseline_null_requires_sourced_opposite_conflict_symmetrically(direction,delta):
    raw,context,prepared=anchor_actual()
    prepared['price_reference']['market_baseline'].update(observed_direction=direction,observed_delta=delta)
    for reason in ('没有逆转证据，无法判断。','已有有效行情，但缺少连续数据，无法判断。','无法判断未来走势。'):
        raw['close_forecast'].update(summary=reason,basis=reason)
        with pytest.raises(ValueError,match='不能单独取消'):
            candidate._check_known_price_not_claimed_missing(raw,context,prepared)


def test_missing_stale_nonmaterial_baselines_remain_no_outlook_eligible():
    raw,context,prepared=anchor_actual()
    for mode in ('missing','stale','flat','nonmaterial'):
        local=copy.deepcopy(prepared)
        baseline=local['price_reference']['market_baseline']
        if mode=='missing':baseline['available']=False
        elif mode=='stale':local['price_reference']['intraday']['stale']=True
        elif mode=='flat':baseline.update(observed_direction='flat',observed_delta=0)
        else:baseline.update(observed_direction='up',observed_delta=.2)
        candidate._check_known_price_not_claimed_missing(raw,context,local)


def test_real_cited_known_opposite_facts_preserve_legitimate_material_anchor_no_outlook():
    raw,context,prepared=anchor_actual()
    add=[{'id':'N1','source':'operator_meter','title':'欧洲运营计量','facts':'欧洲实际供气增加40mcm/d。','time':'2026-10-20T12:05:00Z'},
         {'id':'N2','source':'demand_meter','title':'欧洲需求更新','facts':'欧洲实际取暖需求增加15%。','time':'2026-10-20T12:05:00Z'}]
    prepared['window_news']=copy.deepcopy(add)
    context['window_news']=[dict(row,summary=row['facts'],published_at=row['time']) for row in add]
    raw['news_impact']['bullish_evidence']=['[N2]取暖需求增加15% → 上行压力']
    raw['news_impact']['bearish_evidence']=['[N1]实际供气增加40mcm/d → 下行压力']
    raw['news_impact']['summary']='本次消息存在相反的供需压力，权重尚不明确。'
    raw['close_forecast'].update(summary='[N1]供气恢复与已观察的上行价格基准相反，[N2]需求增加又提供上行压力，当前难以权衡。',
       basis='[N1]实际供气增加40mcm/d形成下行压力，与已观察上行基准冲突；[N2]需求增加又提供上行压力，抵消程度未知。')
    assert candidate.collect_analysis_semantic_errors(raw,context,prepared)==[]
    before=copy.deepcopy(raw)
    candidate.validate_analysis_semantics(raw,context,prepared)
    assert raw==before
    for mode in ('uncited','future_source'):
        local=copy.deepcopy(prepared);out=copy.deepcopy(raw)
        if mode=='uncited':
            for field in ('summary','basis'):out['close_forecast'][field]=out['close_forecast'][field].replace('[N1]','')
        else:local['window_news'][0]['known_at']='2026-10-20T12:11:00Z'
        with pytest.raises(ValueError,match='不能单独取消'):
            candidate._check_known_price_not_claimed_missing(out,context,local)


@pytest.mark.parametrize('text',[
    '当前供应风险降低，实际流量未知。','当前供应风险增加，实际流量未知。',
    'Current supply risk is lower; actual flow is unknown.',
    'Current supply risks increase; actual flows are unknown.',
])
def test_risk_object_never_becomes_physical_supply_in_model_input(text):
    from ttf_news.analysis import prepare_context
    article={'id':'risk-news','source':'eia','title':'European gas market update','summary':text,
             'published_at':'2026-10-20T10:00:00Z','first_seen_at':'2026-10-20T10:00:00Z'}
    context={'as_of':'2026-10-20T12:00:00Z','current_news':article,'window_news':[article],
             'historical_events':[],'previous_days':[],'price_reference':{}}
    before=copy.deepcopy(context)
    prepared,_=prepare_context(context,False,reserve_tokens=32)
    assert candidate.extract_mechanisms(article)==[]
    assert not prepared['current_news'].get('mechanism_candidates')
    assert prepared['current_news']['facts']==text
    assert context==before


@pytest.mark.parametrize('text,pressure',[
    ('风险导致实际供气减少40mcm/d。','up'),
    ('供应风险降低，但欧洲实际供气减少40mcm/d。','up'),
    ('供应风险上升，但欧洲实际供气增加40mcm/d。','down'),
    ('Current supply risks decline, but confirmed actual flows fell by 40mcm/d.','up'),
])
def test_risk_mask_preserves_distinct_actual_flow_fact(text,pressure):
    article={'source':'eia','title':'European gas market update','summary':text}
    signals=candidate.extract_mechanisms(article)
    assert {signal['pressure'] for signal in signals}=={pressure}
    assert all(signal['evidence_kind']=='actual_flow' for signal in signals)
    assert all(signal['magnitude_mcm_d']==40 for signal in signals)
    assert all(signal['quoted_fact']==text.rstrip('。') for signal in signals)


@pytest.mark.parametrize('change,field',[('降低','bearish_evidence'),('增加','bullish_evidence')])
def test_explicit_source_risk_and_correct_result_risk_do_not_reverse_physical_direction(change,field):
    article={'id':'risk-news','source':'eia','title':'European gas market update',
             'summary':'当前供应风险'+change+'，实际流量未知。','published_at':'2026-10-20T10:00:00Z'}
    context={'as_of':'2026-10-20T12:00:00Z','current_news':article,'window_news':[],'price_reference':{}}
    prepared={'as_of':context['as_of'],'current_news':dict(article,id='N0'),'window_news':[],'price_reference':{}}
    evidence='[N0]TTF机制：供应风险'+change
    output={'news_impact':{'direction':'flat','contextual_news_score':0,field:[evidence]},
            'close_forecast':{'direction':None,'evidence_status':'insufficient_evidence','summary':'缺少前交易日锚点，无法形成收盘预判','basis':'缺少前日价格比较锚点'},
            'analysis':{'summary':'新闻传导较弱，实流尚待核实','evidence':[evidence],
                        'priced_in_comment':'未提供公告前后价格反应，无法确认是否计价'}}
    assert candidate.collect_analysis_semantic_errors(output,context,prepared)==[]
    candidate.validate_analysis_semantics(output,context,prepared)
