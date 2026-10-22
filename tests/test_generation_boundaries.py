import copy
import hashlib
import json
from pathlib import Path
import re

import httpx
import pytest

from ttf_news.analysis import AnalysisError, NewsAnalyst, _parse_result, prepare_context, response_schema
from ttf_news.semantics import source_fact_projection


def timing_context():
    now='2026-10-20T12:10:58.642680+00:00'
    quote={'value':88.04,'observed_at':'2026-10-20T11:50:58.642680+00:00',
           'available_at':'2026-10-20T12:10:57.642680+00:00','price_type':'delayed_last_trade'}
    return {'as_of':now,'analysis_kind':'manual','session':{'session_date':'2026-10-20','close_at':'2026-10-20T15:15:00Z'},
            'current_news':{'id':'current','title':'欧洲处理厂提前复产','summary':'欧洲实际流量增加40mcm/d。',
                            'published_at':now,'timestamp_precision':'exact','source':'manual','url':''},
            'window_news':[],'historical_events':[],'previous_sessions':[],
            'price_reference':{'available':True,'contract':'test-Nov26','previous_price':88.04,'price_date':'2026-10-19',
                               'intraday':quote,'market_baseline':{'available':True,'contract':'test-Nov26','as_of':now,
                                                                'observed_delta':0,'observed_at':quote['observed_at'],
                                                                'available_at':quote['available_at']}}}


def test_quote_occurrence_not_availability_positions_news_and_preserves_input():
    context=timing_context()
    context['window_news']=[copy.deepcopy(context['current_news']),
                            {'id':'earlier','title':'早间新闻','summary':'供给安排仍待确认','published_at':'2026-10-20T11:00:00Z'},
                            {'id':'date-only','title':'只有日期','summary':'时间精度不详','published_date':'2026-10-20','timestamp_precision':'date'}]
    before=copy.deepcopy(context)
    prepared,_=prepare_context(context)
    card=prepared['evidence_ledger']['pricing_observation']
    assert card['observed_at'].startswith('2026-10-20T11:50:')
    assert card['available_at'].startswith('2026-10-20T12:10:57')
    assert set(card['quote_vs_news_publication']['before_publication'])=={'N0','N1'}
    assert card['quote_vs_news_publication']['at_or_after_publication']==['N2']
    assert card['quote_vs_news_publication']['unknown']==['N3']
    assert '冲击前基准' in card['current_news_timing']['quote_role']
    assert '不是报价发生时刻' in card['clock_roles']['available_at']
    assert not card['announcement_reaction_evidence_available']
    assert context==before
    assert prepared['current_news']['time']==context['current_news']['published_at']


def test_quote_after_publication_is_still_not_announcement_reaction_evidence():
    context=timing_context();context['current_news']['published_at']='2026-10-20T11:00:00Z'
    prepared,_=prepare_context(context)
    card=prepared['evidence_ledger']['pricing_observation']
    assert card['current_news_timing']['quote_relation']=='at_or_after_publication'
    assert '不证明公告反应' in card['current_news_timing']['quote_role']
    assert not card['announcement_reaction_evidence_available']


def test_future_filtered_quote_cannot_reappear_in_timing_card():
    context=timing_context()
    context['price_reference']['market_baseline']['available_at']='2026-10-21T12:00:00Z'
    context['price_reference']['intraday']['available_at']='2026-10-21T12:00:00Z'
    prepared,_=prepare_context(context)
    card=prepared['evidence_ledger']['pricing_observation']
    assert 'observed_at' not in card
    assert card['quote_vs_news_publication']['unknown']==['N0']
    assert not card['market_baseline_available']
    assert card['current_news_timing']['quote_role']=='观测/发布时间精度不足，前后关系未知'


def test_date_only_current_publication_has_unknown_quote_role():
    context=timing_context()
    context['current_news'].update(published_at='2026-10-20',timestamp_precision='date')
    prepared,_=prepare_context(context)
    card=prepared['evidence_ledger']['pricing_observation']
    assert card['market_baseline_available']
    assert card['current_news_timing']['quote_relation']=='unknown'
    assert card['current_news_timing']['quote_role']=='观测/发布时间精度不足，前后关系未知'


def test_operator_const_retains_quote_clock_roles_and_current_publication():
    context=timing_context()
    context['current_news'].update(source='gassco',title='Gassco Troll: Planned unavailability (Active)',
                                   summary='Technical capacity: 100 mcm/d; available: 80; unavailable: 20.',event_status='Active')
    prepared,_=prepare_context(context)
    projection=source_fact_projection(prepared)['pricing_observation']
    assert projection['observed_at']==context['price_reference']['intraday']['observed_at']
    assert projection['available_at']==context['price_reference']['intraday']['available_at']
    assert projection['current_news_timing']['publication_time']==context['current_news']['published_at']
    assert projection['current_news_timing']['quote_relation']=='before_publication'
    assert projection['clock_roles']['observed_at']=='报价发生时刻'


def test_all_saved_successful_real_raw_prose_fits_native_generation_bounds():
    corpus=json.loads((Path(__file__).parent/'fixtures/generation_real_raw_prose.json').read_text())
    schema=response_schema(False)
    checked=0
    for record in corpus['records']:
        for field in record['fields']:
            node=schema['$defs'][field['definition']]['properties'][field['field']]
            if field['array_item']:node=node['items']
            elif field['field']=='extreme_move_risk':node=next(row for row in node['anyOf'] if row.get('type')=='string')
            assert 'pattern' not in node
            assert len(field['text'])<=node['maxLength'],(record['record'],field['field'])
            assert len(field['text'])>=node.get('minLength',0)
            checked+=1
    assert corpus['record_count']==len(corpus['records'])==8
    assert corpus['field_count']==checked==91


def test_long_english_or_bracket_prefix_cannot_bypass_native_field_maximum():
    schema=response_schema(False)
    for definition,field in [('NewsImpact','summary'),('Analysis','evidence'),('CloseForecast','basis')]:
        node=schema['$defs'][definition]['properties'][field]
        if node.get('type')=='array':node=node['items']
        assert 'pattern' not in node
        assert node['maxLength']<=600
        assert len('[N0] Nyhamna unavailability '+' ] '*1500)>node['maxLength']


def test_detail_source_prefix_uses_known_aliases_and_explicit_input_fields():
    prepared={'current_news':{'id':'N0'},'window_news':[{'id':'N3'}],
              'historical_events':[{'id':'E5','facts':'早期供应事件'}]}
    schema=response_schema(True,prepared)
    for field in ('new_facts','transmission','daily_balance','invalidation_conditions'):
        node=schema['$defs']['DetailAnalysis']['properties'][field]['items'];pattern=node['pattern']
        for allowed in ['[N0]新闻对应本窗口供需机制','[N3] Åsgard不可用容量5.2mcm/d，流量未知','[E5]历史情景仅作有条件类比','window_news本窗口只有该新闻','price_reference缺公告前后可比反应']:
            assert re.fullmatch(pattern,allowed),(field,allowed)
        for rejected in ['本窗口仅该新闻净看跌','[N9]输入不存在的新闻','[E9]输入不存在的历史事件','[N0]'+' ] '*1000,'[N0]事实"后续','[N0]事实\\后续']:
            assert re.fullmatch(pattern,rejected) is None,(field,rejected)
        assert '*' not in pattern
        assert node['maxLength']==280
    history=schema['$defs']['DetailAnalysis']['properties']['historical_comparisons']['items']
    assert 'enum' in history and 'pattern' not in history
    limitations=schema['$defs']['DetailAnalysis']['properties']['limitations']['items']
    assert 'enum' in limitations and 'pattern' not in limitations
    assert schema['$defs']['NewsImpact']['properties']['direction']['enum']==['extreme_up','up','flat','down','extreme_down']


def test_detail_uncapped_prose_has_generation_only_native_limit():
    schema=response_schema(True)
    assert schema['$defs']['DetailAnalysis']['properties']['priced_in_assessment']['maxLength']==600
    for field in ('bullish','base','bearish'):
        assert schema['$defs']['Scenarios']['properties'][field]['maxLength']==600
        assert re.fullmatch(schema['$defs']['Scenarios']['properties'][field]['pattern'], '若欧洲供气恢复持续，供需情景待验证')


def test_english_paragraph_still_fails_post_generation_chinese_check():
    context=timing_context();_,meta=prepare_context(context)
    corpus=json.loads((Path(__file__).parent/'fixtures/generation_real_raw_prose.json').read_text())
    # Supply an otherwise structurally valid, single-source output; the native
    # decoding bound permits names, while Chinese remains a post-parse contract.
    from ttf_news.prompts import SHORT_SHAPE
    output=copy.deepcopy(SHORT_SHAPE)
    output['news_impact'].update(summary='欧洲供应增加形成下行压力',direction='down',contextual_news_score=-1,
                                 bullish_evidence=[],bearish_evidence=['[N0]欧洲实际供气增加40mcm/d → TTF下行压力增强'])
    output['close_forecast'].update(summary='依据新闻形成条件性收盘判断',basis='前一同合约基准与累计新闻仍需核实')
    output['analysis'].update(summary='The entire reasoning paragraph stays in English with no Chinese causal explanation.',
                              evidence=['[N0]欧洲实际供气增加40mcm/d → TTF下行压力增强'],priced_in_comment='缺少公告前后反应，无法确认是否已计价')
    with pytest.raises(ValueError,match='核心分析文案必须为中文'):
        _parse_result(json.dumps(output),False,meta['source_index'],context)


async def test_http_500_audit_is_bounded_redacted_and_does_not_add_requests(monkeypatch):
    payload={'error':'runner failed /Users/example/private/model.gguf api_key=hidden-value Bearer abcdefghijklmnop '+'x'*1500}
    response_bytes=json.dumps(payload).encode()
    original=httpx.AsyncClient;requests=[]
    def handler(request):
        requests.append(json.loads(request.content));return httpx.Response(500,content=response_bytes)
    monkeypatch.setattr('ttf_news.analysis.httpx.AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    with pytest.raises(AnalysisError) as failure:
        await NewsAnalyst().analyze(timing_context())
    assert len(requests)==1
    audit=failure.value.audit['model_calls'][0]['request_error_detail']
    assert audit['http_status']==500
    assert audit['body_bytes']==len(response_bytes)
    assert audit['body_sha256']==hashlib.sha256(response_bytes).hexdigest()
    assert len(audit['error_summary'])<=512
    assert '/Users/' not in audit['error_summary']
    assert 'hidden-value' not in audit['error_summary']
    assert 'abcdefghijklmnop' not in audit['error_summary']
    assert 'runner failed' in audit['error_summary']
    assert 'hidden-value' not in str(failure.value)


async def test_http_error_on_second_attempt_keeps_two_call_cap_and_original_parse_failure(monkeypatch):
    original=httpx.AsyncClient;requests=[]
    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests)==1:return httpx.Response(200,json={'message':{'content':'{}'},'prompt_eval_count':100})
        return httpx.Response(500,text='unique-transport-diagnostic')
    monkeypatch.setattr('ttf_news.analysis.httpx.AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    with pytest.raises(AnalysisError) as failure:
        await NewsAnalyst().analyze(timing_context())
    assert len(requests)==2
    calls=failure.value.audit['model_calls']
    assert len(calls)==2 and calls[0]['validation_error']
    assert calls[1]['request_error_detail']['http_status']==500
    assert calls[1]['request_error_detail']['error_summary']=='unique-transport-diagnostic'
    assert 'unique-transport-diagnostic' not in requests[1]['messages'][0]['content']


async def test_non_json_http_error_with_thinking_is_not_echoed(monkeypatch):
    original=httpx.AsyncClient
    def handler(request):return httpx.Response(500,text='broken payload {"thinking":"private-thought"')
    monkeypatch.setattr('ttf_news.analysis.httpx.AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    with pytest.raises(AnalysisError) as failure:
        await NewsAnalyst().analyze(timing_context())
    audit=failure.value.audit['model_calls'][0]['request_error_detail']
    assert audit['http_status']==500
    assert 'private-thought' not in json.dumps(audit)
    assert '正文已省略' in audit['error_summary']
