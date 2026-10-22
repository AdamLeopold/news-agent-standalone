import copy
import asyncio
import json
from pathlib import Path

import pytest

from ttf_news.analysis import NewsAnalyst, _detail_limitation_options, prepare_context
from ttf_news.calendar import session_at
from ttf_news.context import market_baseline, model_price_reference, settlement_anchor
from ttf_news.semantics import collect_analysis_semantic_errors, validate_analysis_semantics


FIXTURE=Path(__file__).parent/'fixtures'/'manual_stale_previous_settlement_6601.json'
ASOF='2026-10-20T20:02:05.460066+00:00'


def original():
    return json.loads(FIXTURE.read_text())


def test_real_manual_stale_reference_is_preserved_but_not_a_previous_settlement():
    frozen=original();ctx=frozen['input_snapshot'];before=copy.deepcopy(ctx)
    prepared,_=prepare_context(ctx)
    price=prepared['price_reference'];anchor=price['settlement_anchor']
    assert not anchor['available'] and anchor['expected_previous_trade_date']=='2026-10-20'
    assert anchor['reference_date']=='2026-10-19'
    assert price['intraday']['value']==86.585 and price['intraday']['contract']==ctx['price_reference']['contract']
    assert 'previous_price' not in price and 'price_date' not in price
    assert '88.04' not in json.dumps(price)
    assert price['historical_reference_scope']['reference_date']=='2026-10-19'
    use=prepared['evidence_ledger']['pricing_observation']['forecast_use']
    assert not use['same_contract_anchor_available'] and not use['same_contract_market_baseline_available']
    assert ctx==before and frozen['stored_result']['close_forecast']['direction']=='down'
    errors=collect_analysis_semantic_errors(frozen['raw_result'],ctx,prepared)
    assert any('缺少前交易日2026-10-20同合约锚点' in error for error in errors)


def test_no_other_provider_previous_close_delta_path_leaks_into_request():
    ctx=original()['input_snapshot'];price=ctx['price_reference']
    price.update(change=-1.455,previousClose=88.04,dayChange=-1.455,
                 provider={'previousReference':{'value':88.04},'nested':{'changePercent':-1.65,'quoteDirection':'down'}})
    price['intraday'].update(previous_close=88.04,change=-1.455,percent_change=-1.65,direction='down')
    before=copy.deepcopy(ctx);prepared,_=prepare_context(ctx)
    model=prepared['price_reference'];serialized=json.dumps(model)
    assert '88.04' not in serialized and '-1.455' not in serialized and '-1.65' not in serialized
    assert model['intraday']['value']==86.585
    assert ctx==before
    # Comparable announcement evidence has a separate time/cause scope and
    # remains available even while the daily close-comparison anchor is absent.
    price['announcement_price_reaction']={'source':'same-news before/after comparison','delta':-0.2,'observed_at':'2026-10-20T19:50:00Z','available_at':'2026-10-20T19:55:00Z'}
    prepared,_=prepare_context(ctx)
    assert prepared['price_reference']['announcement_price_reaction']['delta']==-0.2


@pytest.mark.parametrize('valid_anchor',[False,True])
def test_unavailable_baseline_is_observation_whitelist_not_provider_alias_payload(valid_anchor):
    ctx=original()['input_snapshot'];price=ctx['price_reference']
    if valid_anchor:
        price.update(price_date='2026-10-20',previous_price=87.0,price_known_at='2026-10-20T18:00:00Z')
    price['market_baseline'].update(available=False,reason='comparison_unavailable',reference_move=1.5,referenceMove=2.5,
                                    observed_delta=-1.455,derived={'referenceMove':3.5,'nested':{'unrecognisedMove':4.5}},
                                    latest_price=86.585,observed_at='2026-10-20T19:49:00Z',available_at='2026-10-20T19:59:49Z')
    prepared,_=prepare_context(ctx);baseline=prepared['price_reference']['market_baseline']
    assert not baseline['available']
    assert set(baseline)<={'available','reason','interpretation','kind','contract','as_of','observed_at','available_at',
                           'latest_price','price_type','source_url','minimum_delay_minutes','verified_settlement'}
    assert baseline['latest_price']==86.585 and baseline['observed_at']=='2026-10-20T19:49:00Z'
    assert not set(baseline)&{'reference_move','referenceMove','derived','unrecognisedMove','observed_delta'}
    assert not any(value in {1.5,2.5,3.5,4.5,-1.455} for value in baseline.values() if isinstance(value,(int,float)))
    assert prepared['price_reference']['intraday']['value']==86.585


def test_missing_anchor_is_available_as_grounded_detail_limitation():
    ctx=original()['input_snapshot'];prepared,meta=prepare_context(ctx,detail=True)
    assert any('前一同合约价格锚点' in line for line in _detail_limitation_options(prepared))
    # A source-prefix limitation of this precise missing comparison remains a
    # permissible factual explanation despite an absolute quote being visible.
    from ttf_news.analysis import _missing_data_basis
    assert _missing_data_basis('price_reference:缺少前一交易日同合约价格锚点','limitations',ctx)=='price_reference.previous_trading_day_anchor'


def test_both_inference_requests_keep_absolute_quote_and_never_recover_stale_anchor():
    frozen=original();good=copy.deepcopy(frozen['raw_result'])
    good['close_forecast'].update(direction=None,evidence_status='insufficient_evidence',summary='缺少前交易日同合约锚点，当前新闻方向有限。',basis='已观察绝对报价86.585；目标日的前交易日10月20日参考尚缺，不能作昨收比较。')
    class LocalOnly(NewsAnalyst):
        def __init__(self):
            super().__init__();self.requests=[];self.outputs=[frozen['raw_result'],good]
        async def _request(self,system,data,detail):
            self.requests.append(copy.deepcopy(data))
            return {'message':{'content':json.dumps(self.outputs.pop(0),ensure_ascii=False)},'done_reason':'stop'}
    analyst=LocalOnly();out=asyncio.run(analyst.analyze(frozen['input_snapshot']))
    assert len(analyst.requests)==2 and out['close_forecast']['direction'] is None
    for request in analyst.requests:
        price=request['price_reference']
        assert price['intraday']['value']==86.585
        assert not price['settlement_anchor']['available']
        assert '88.04' not in json.dumps(price) and 'previous_price' not in price
    repair = analyst.requests[1]
    feedback = repair['REPAIR_FEEDBACK']
    errors = out['_meta']['model_calls'][0]['validation_errors']
    assert feedback['origin'] == 'local_validator' and feedback['is_evidence'] is False
    assert feedback['errors'] == errors
    assert 'UNTRUSTED_DRAFT' not in repair
    assert frozen['raw_result']['close_forecast']['summary'] not in json.dumps(repair, ensure_ascii=False)
    assert frozen['raw_result']['analysis']['summary'] not in json.dumps(repair, ensure_ascii=False)
    assert any('缺少前交易日' in error for error in errors)


@pytest.mark.parametrize('target,previous',[('2026-10-20','2026-10-19'),('2026-10-19','2026-10-16'),('2026-04-07','2026-04-02')])
def test_exact_previous_trading_day_weekend_and_holiday_remain_legal(target,previous):
    ref={'available':True,'contract':'Nov26','previous_price':88.04,'price_date':previous}
    status=settlement_anchor(ref,{'trade_date':target},target+'T12:00:00Z')
    assert status['available'] and status['known_time_precision']=='not_provided'
    assert 'price_known_at' not in ref  # No fabricated clock for old fixtures.


@pytest.mark.parametrize('mutation,reason',[
    ({'price_date':'2026-10-19'},'previous_trading_day_reference_missing'),
    ({'previous_contract':'Dec26'},'previous_contract_mismatch'),
    ({'price_known_at':'2026-10-20T20:03:00Z'},'previous_reference_not_known_as_of'),
    ({'previous_price':0},'invalid_previous_reference_value'),
])
def test_date_identity_value_and_explicit_future_clock_cannot_anchor(mutation,reason):
    ref={'available':True,'contract':'Nov26','previous_price':88.04,'price_date':'2026-10-20',**mutation}
    status=settlement_anchor(ref,{'trade_date':'2026-10-21'},ASOF)
    assert not status['available'] and status['reason']==reason


def test_valid_previous_day_preserves_baseline_and_cross_contract_quote_does_not():
    ctx=original()['input_snapshot'];price=ctx['price_reference']
    price.update(price_date='2026-10-20',previous_price=87.0,price_known_at='2026-10-20T18:00:00Z')
    price['market_baseline']=market_baseline(price,ctx['session'],ctx['as_of'])
    assert price['market_baseline']['available']
    prepared,_=prepare_context(ctx);model=prepared['price_reference']
    assert model['previous_price']==87.0 and model['market_baseline']['observed_delta']==-0.415
    assert model['market_baseline']==price['market_baseline']
    assert prepared['evidence_ledger']['pricing_observation']['forecast_use']['same_contract_anchor_available']
    price['intraday']['contract']='another-contract'
    prepared,_=prepare_context(ctx)
    assert not prepared['price_reference']['market_baseline']['available']
    assert 'observed_delta' not in prepared['price_reference']['market_baseline']
    assert prepared['price_reference']['intraday']['contract']=='another-contract'


def test_weak_news_without_legal_anchor_is_no_outlook_not_a_default_flat():
    frozen=original();ctx=frozen['input_snapshot'];prepared,_=prepare_context(ctx)
    for direction in ('up','down','flat','extreme_up','extreme_down'):
        out=copy.deepcopy(frozen['raw_result']);out['close_forecast']['direction']=direction
        with pytest.raises(ValueError,match='新闻也无方向依据时须insufficient_evidence'):
            validate_analysis_semantics(out,ctx,prepared)
    out=copy.deepcopy(frozen['raw_result']);out['close_forecast'].update(direction=None,evidence_status='insufficient_evidence',summary='缺少目标日前交易日同合约价格和方向性新闻，无法形成收盘比较。',basis='已观察绝对报价86.585，但缺少10月20日同合约锚点。')
    validate_analysis_semantics(out,ctx,prepared)
    ctx['price_reference']['announcement_price_reaction']={'source':'independent before/after announcement comparison','observed_at':'2026-10-20T19:50:00Z','available_at':'2026-10-20T19:55:00Z','observed_delta':-0.2}
    prepared,_=prepare_context(ctx)
    out['close_forecast']['basis']='公告前后可比反应显示参考价格已下跌0.2；缺少昨日前价，不能据此形成前结算比较。'
    validate_analysis_semantics(out,ctx,prepared)


@pytest.mark.parametrize('direction,news,pressure',[('up','欧洲实际供气减少5mcm/d。','供应减少形成上行压力'),('down','欧洲实际供气增加5mcm/d。','供应改善形成下行压力')])
def test_directional_news_without_anchor_still_allows_conditional_forecast(direction,news,pressure):
    frozen=original();ctx=frozen['input_snapshot'];ctx['current_news']['summary']=news;ctx['current_news']['title']='欧洲供气计量更新';ctx['window_news']=[]
    prepared,_=prepare_context(ctx);out=copy.deepcopy(frozen['raw_result'])
    evidence='[N0]'+news+' → TTF'+pressure
    out['news_impact'].update(direction=direction,summary=pressure,bullish_evidence=[evidence] if direction=='up' else [],bearish_evidence=[evidence] if direction=='down' else [])
    out['close_forecast'].update(direction=direction,evidence_status='forecast',summary='若这一供需变化延续且无反向冲击，条件性预计TTF'+('上涨' if direction=='up' else '下跌'),basis='[N0]'+pressure+'；缺少目标日前日价格，比较幅度未知。')
    out['analysis'].update(summary=news,evidence=[evidence])
    validate_analysis_semantics(out,ctx,prepared)
    # A real directional news forecast cannot smuggle in the invalid observed
    # multi-day quote difference as though it were a previous-day comparison.
    out['close_forecast']['basis']='参考价格已下跌1.45点，'+pressure
    with pytest.raises(ValueError,match='不能把其他日期reference'):
        validate_analysis_semantics(out,ctx,prepared)
