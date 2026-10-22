import copy
import json
from pathlib import Path

import pytest

from ttf_news.analysis import prepare_context
from ttf_news.semantics import (_check_observed_absence_of_price_reaction,
    _check_known_price_not_claimed_missing, _check_directional_conditions,
    _check_unknown_as_fact, _check_contract_party_roles)


def records():
    return json.loads((Path(__file__).parent/'fixtures/actual_price_observation_roles_v2_3.json').read_text())


@pytest.mark.parametrize('key', ['weak_exploration_no_quote','supply_restore_40_with_flat_baseline','anchor_legal_prevday_weak_market_up'])
def test_actual_negative_price_observations_are_not_missing_data(key):
    row=records()[key];context=row['input_snapshot'];prepared,_=prepare_context(context)
    original=copy.deepcopy(row['result'])
    with pytest.raises(ValueError,match='独立价格反应'):
        _check_observed_absence_of_price_reaction(original,context,prepared)
    assert original==row['result']


@pytest.mark.parametrize('text',[
    '该新闻未产生可观测的价格反应。',
    '价格没有出现明显波动。',
    '供应增加未产生即时价格变动。',
    '价格未发生即刻波动。',
    '新闻未产生即时价格变动。',
    '新闻未对欧洲天然气市场产生任何可观影响。',
])
def test_single_prior_quote_is_not_announcement_reaction(text):
    context=records()['supply_restore_40_with_flat_baseline']['input_snapshot'];prepared,_=prepare_context(context)
    with pytest.raises(ValueError,match='独立价格反应'):
        _check_observed_absence_of_price_reaction({'analysis':{'summary':text}},context,prepared)


@pytest.mark.parametrize('text',[
    '当前新闻传导较弱，影响有限。',
    '该许可对欧洲供气暂无直接影响。',
    '输入未提供价格反应观测，无法确认新闻是否未产生可观测价格反应。',
    '若新闻未产生可观测价格反应，须重新核对供需渠道。',
    '不能认定该新闻未产生可观测价格反应。',
    '未见该消息后价格反应的资料。',
    '未见价格即刻波动。',
])
def test_model_pressure_judgements_and_epistemic_conditions_remain_open(text):
    context=records()['weak_exploration_no_quote']['input_snapshot'];prepared,_=prepare_context(context)
    _check_observed_absence_of_price_reaction({'analysis':{'summary':text}},context,prepared)


def test_true_asof_reaction_evidence_allows_observation_but_future_does_not():
    context=records()['supply_restore_40_with_flat_baseline']['input_snapshot']
    context['price_reference']['announcement_price_reaction']={'observed_change':0,'available_at':'2026-10-20T12:10:57Z'}
    prepared,_=prepare_context(context);out={'analysis':{'summary':'新闻未产生可观测价格反应。'}}
    _check_observed_absence_of_price_reaction(out,context,prepared)
    context['price_reference']['announcement_price_reaction']['available_at']='2026-10-21T12:10:57Z'
    prepared,_=prepare_context(context)
    with pytest.raises(ValueError):_check_observed_absence_of_price_reaction(out,context,prepared)


@pytest.mark.parametrize('direction,delta',[('up',1.76),('down',-1.76)])
def test_material_reference_trend_is_not_cancelled_by_no_news_causality(direction,delta):
    row=records()['weak_news_observed_market_up'];context=copy.deepcopy(row['input_snapshot'])
    price=context['price_reference'];price['market_baseline'].update(observed_direction=direction,observed_delta=delta,latest_price=88.04+delta)
    price['intraday']['value']=88.04+delta
    prepared,_=prepare_context(context);result=copy.deepcopy(row['result'])
    result['close_forecast']['basis']='已有方向基准，但缺乏新闻对价格的直接影响，无法确认是否已计价。'
    with pytest.raises(ValueError,match='盘中方向基准'):_check_known_price_not_claimed_missing(result,context,prepared)
    for forecast in ('up','flat','down','extreme_up','extreme_down'):
        result['close_forecast'].update(direction=forecast,evidence_status='forecast')
        _check_known_price_not_claimed_missing(result,context,prepared)
    result['close_forecast'].update(direction=None,evidence_status='insufficient_evidence')
    context['window_news'].append({'id':'known-conflicting-facts','title':'反向供需事实',
        'summary':'实际天然气需求增加，同时实际天然气供应增加。','source':'test',
        'published_at':context['as_of'],'first_seen_at':context['as_of'],'known_at':context['as_of']})
    prepared,_=prepare_context(context)
    citation=next(row['id'] for row in prepared['window_news'] if row.get('title')=='反向供需事实')
    result['close_forecast']['basis']=f'[{citation}]有价格基准，但来源明确需求增加与供应增加相互冲突，难以权衡。'
    _check_known_price_not_claimed_missing(result,context,prepared)
    uncited=copy.deepcopy(result)
    uncited['close_forecast']['basis']=uncited['close_forecast']['basis'].replace(f'[{citation}]','')
    with pytest.raises(ValueError,match='盘中方向基准'):
        _check_known_price_not_claimed_missing(uncited,context,prepared)


def test_missing_anchor_or_flat_baseline_does_not_force_close_outlook():
    row=records()['weak_exploration_no_quote'];context=row['input_snapshot'];prepared,_=prepare_context(context)
    _check_known_price_not_claimed_missing(row['result'],context,prepared)
    context=records()['supply_restore_40_with_flat_baseline']['input_snapshot'];prepared,_=prepare_context(context)
    result=copy.deepcopy(row['result']);result['news_impact']['direction']='flat';result['missing_fields']=[]
    _check_known_price_not_claimed_missing(result,context,prepared)


@pytest.mark.parametrize('news_direction',['up','down'])
def test_directional_news_retry_first_100_characters_keep_positive_anchor_contract(news_direction):
    context=records()['supply_restore_40_with_flat_baseline']['input_snapshot'];prepared,_=prepare_context(context)
    out={'news_impact':{'direction':news_direction},
         'close_forecast':{'direction':None,'evidence_status':'insufficient_evidence',
                           'summary':'缺少公告前后价格反应，无法判断是否已计价。','basis':'当前基准持平。'}}
    with pytest.raises(ValueError) as exc:
        _check_known_price_not_claimed_missing(out,context,prepared)
    compact=str(exc.value)[:100]
    for phrase in ('比较锚点已给','报价若早于消息仅是基准','未知后续反应不等于零反应或未计价',
                   '条件收盘预判','五档自由','不自动复制新闻方向'):
        assert phrase in compact
    assert out['news_impact']['direction']==news_direction
    assert out['close_forecast']['direction'] is None


@pytest.mark.parametrize('negation',['未被','没有被','不会被'])
def test_unoffset_supply_increase_is_valid_bearish_condition(negation):
    for factors in ('其他因素','需求上升因素'):
        out={'detail':{'scenarios':{'bearish':f'若供给增加{negation}{factors}抵消，价格可能下行。'}}}
        _check_directional_conditions(out)
    bad={'detail':{'scenarios':{'bearish':'若供给增加抵消下行压力，价格可能下行。'}}}
    with pytest.raises(ValueError):_check_directional_conditions(bad)
    bad['detail']['scenarios']['bearish']='若供给增加抵消下行压力，但需求没有被改变。'
    with pytest.raises(ValueError):_check_directional_conditions(bad)


def test_actual_inventory_absence_is_not_no_opposing_news():
    row=records()['restore_detail'];context=row['input_snapshot'];prepared,_=prepare_context(context)
    for raw in row['raw_results']:
        with pytest.raises(ValueError,match='库存状态'):_check_unknown_as_fact(raw,context,prepared)
    for text in ['输入未提供其他库存变化资料。','未见库存变化的证据。','若未伴随其他库存变化，供给压力可能持续。']:
        _check_unknown_as_fact({'analysis':{'summary':text}},context,prepared)
    context['window_news'].append({'title':'库存测量','summary':'库存测量确认未伴随其他库存变化。','published_at':context['as_of']})
    prepared,_=prepare_context(context)
    _check_unknown_as_fact({'analysis':{'summary':'未伴随其他供需或库存变化。'}},context,prepared)


def test_actual_award_recipient_inversion_rejected_without_direction_change():
    row=records()['anchor_real_manual_missing_prevday'];context=row['input_snapshot'];prepared,_=prepare_context(context);out=copy.deepcopy(row['result'])
    with pytest.raises(ValueError,match='授予/委托方'):_check_contract_party_roles(out,context,prepared)
    assert out==row['result']
