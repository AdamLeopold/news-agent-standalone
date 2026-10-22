"""Isolated proposed observable-object gate; no root source or model calls."""
import copy
import json
from pathlib import Path

import pytest

import ttf_news.semantics as semantics
from ttf_news.semantics import _check_observed_absence_of_price_reaction as check


HERE=Path(__file__).resolve().parent


@pytest.fixture
def actual():
    fixture=json.loads((HERE/'fixtures/dismissed_implicit_observable_actual_v2_3.json').read_text())
    return fixture,copy.deepcopy(fixture['input_snapshot']),copy.deepcopy(fixture['attempts'][0]['input'])


def output(text,path='basis'):
    return {'close_forecast':{path:text}}


def test_loaded_package_is_root_contract():
    assert Path(semantics.__file__).resolve().parent==HERE.parent/'ttf_news'


def test_actual_accepted_objectless_observable_zero_is_rejected_without_rewriting(actual):
    fixture,context,prepared=actual
    body=fixture['attempts'][0]['response']['message']['content'];raw=json.loads(body);before=copy.deepcopy(raw)
    with pytest.raises(ValueError,match='close_forecast.basis.*价格反应观测'):
        check(raw,context,prepared)
    assert raw==before and fixture['contract_passed'] is True


@pytest.mark.parametrize('field',['basis','summary'])
@pytest.mark.parametrize('claim',[
    '新闻未产生可观测冲击。',
    '公告并没有造成任何可测影响。',
    '该消息未产生可量化的影响。',
    '当前新闻无可观察冲击。',
    '若原条件保持，但新闻未产生可观测冲击。',
])
def test_explicit_observable_zeros_in_price_fields_require_observations(actual,field,claim):
    with pytest.raises(ValueError,match='价格反应观测'):
        check(output(claim,field),actual[1],actual[2])


@pytest.mark.parametrize('claim',[
    '输入未提供可观测冲击的证据。',
    '新闻未产生可观测冲击的数据。',
    '没有可测影响的观测资料。',
    '未见可观测冲击。',
    '无法确认新闻是否未产生可观测冲击。',
    '若新闻未产生可量化影响，则需要补充反应资料。',
    '预计新闻可能未产生可观测冲击。',
    '预计当前即时价格影响有限。',
    '该消息对欧洲的直接传导较弱，新增压力有限。',
    '当前同合约基准价格保持持平，新闻反应未知。',
    '该未来公告尚未导致本次实际供气损失。',
    '该未来公告未产生可观测物理冲击。',
    '该未来公告未产生实际供给冲击。',
    '该公告没有造成可测的实际流量变化。',
])
def test_evidence_limits_conditions_weak_judgements_and_physical_objects_stay_distinct(actual,claim):
    check(output(claim),actual[1],actual[2])


def test_implicit_object_is_not_globally_assigned_to_news_or_physical_sections(actual):
    claim='新闻未产生可观测冲击。'
    for value in ({'news_impact':{'summary':claim}}, {'analysis':{'summary':claim}},
                  {'detail':{'transmission':['[N0]该公告未产生可观测物理冲击。']}}):
        check(value,actual[1],actual[2])


def test_epistemic_comment_elsewhere_does_not_qualify_a_price_zero(actual):
    value=output('新闻未产生可观测冲击。')
    value['analysis']={'priced_in_comment':'未提供反应资料，无法确认是否已计价。'}
    with pytest.raises(ValueError):check(value,actual[1],actual[2])


def test_actual_known_price_reaction_can_support_implicit_price_wording(actual):
    context,prepared=actual[1:]
    prepared['price_reference']['announcement_price_reaction']={
        'source':'independent comparable before/after event prices','available_at':context['as_of'],'observed_change':0}
    check(output('新闻未产生可观测冲击。'),context,prepared)


def test_explicit_source_price_observation_cjk_alias_and_asof_binding(actual):
    context,prepared=actual[1:];stamp=context['as_of']
    observation={'id':'N2','source':'independent','title':'Measured TTF event reaction',
        'facts':'该消息未产生可观测价格冲击。','time':stamp,'available_at':stamp}
    prepared['window_news'].append(observation)
    check(output('N2所述新闻未产生可观测冲击。'),context,prepared)
    for alias in ('N0','N1'):
        with pytest.raises(ValueError):check(output(alias+'所述新闻未产生可观测冲击。'),context,prepared)
    observation['available_at']='2026-10-20T12:20:00Z'
    with pytest.raises(ValueError):check(output('N2所述新闻未产生可观测冲击。'),context,prepared)
    observation['available_at']=stamp;observation['facts']='输入没有价格反应数据。'
    with pytest.raises(ValueError):check(output('N2所述新闻未产生可观测冲击。'),context,prepared)
    observation['facts']='该事件未产生可观测物理冲击。'
    with pytest.raises(ValueError):check(output('N2所述新闻未产生可观测冲击。'),context,prepared)


def test_single_flat_reference_level_does_not_establish_event_zero(actual):
    prepared=actual[2];prepared['price_reference']['market_baseline']={
        'available':True,'latest_price':88.04,'previous_price':88.04,'observed_delta':0,
        'observed_at':'2026-10-20T11:50:00Z','available_at':'2026-10-20T12:10:00Z'}
    check(output('当前已观察同合约基准持平，新闻反应未知。'),actual[1],prepared)
    with pytest.raises(ValueError):check(output('新闻未产生可观测冲击。'),actual[1],prepared)


def test_explicit_physical_object_before_predicate_does_not_acquire_price_object(actual):
    context,prepared=actual[1:]
    context['current_news']['summary']='独立计量确认该设施实际天然气流量保持不变。'
    prepared['current_news']['facts']=context['current_news']['summary']
    for claim in ('对实际流量未产生可观测冲击。',
                  '[N0]该公告对实际供气没有可量化影响。',
                  '对计量流量与实际供气未造成任何可测影响。'):
        check(output(claim),context,prepared)
    # A physical observation cannot prove no price effect, including a mixed
    # price/physical object or another declarative subclause.
    for claim in ('对价格及实流未产生可观测冲击。',
                  '对实际流量与价格未产生可量化影响。',
                  '缺乏独立流量确认，新闻未产生可观测冲击。',
                  '对实际流量未产生可观测冲击，但截至目前价格无反应。'):
        with pytest.raises(ValueError):check(output(claim),context,prepared)
