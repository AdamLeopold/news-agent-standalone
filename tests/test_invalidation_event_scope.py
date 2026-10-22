"""Production EPCM invalidation criteria are conditions, not observed flows."""
import copy
import json
from pathlib import Path

import pytest

from ttf_news.analysis import prepare_context, _validate_notice_source_facts
from ttf_news.semantics import (collect_analysis_semantic_errors,
    _check_gassco_physical_state, _check_prospective_supply_scope,
    _check_contract_party_roles, _check_priced_in,
    _check_future_well_action_not_completed)


@pytest.fixture
def actual():
    data=json.loads((Path(__file__).parent/'fixtures/production_epcm_invalidation_scope_v2_3.json').read_text())
    ctx=data['input_snapshot'];prepared,_=prepare_context(ctx,True)
    return data,ctx,prepared


def condition(text):
    return {'detail':{'invalidation_conditions':[text]}}


def test_both_actual_raw_criteria_are_allowed_but_real_summary_error_remains(actual):
    fixture,ctx,prepared=actual;before=copy.deepcopy(fixture)
    for row in fixture['raw_results']:
        errors=collect_analysis_semantic_errors(row['result'],ctx,prepared)
        assert not any('detail.invalidation_conditions' in error for error in errors)
        assert any('analysis.summary:新闻覆盖不足不能证明' in error for error in errors)
    assert fixture==before


@pytest.mark.parametrize('text',[
    '[N1]或[N2]维护提前至本窗口并导致实际供气损失',
    '[N1]或[N2]实际流量损失被确认',
    '[N1]若维护提前至本窗口并导致实际供气损失',
    '[N2]如果实际流量损失被确认，则须改变原结论',
])
def test_future_operator_criteria_with_or_without_if_are_conditional(actual,text):
    _check_gassco_physical_state(condition(text),actual[1],actual[2])


@pytest.mark.parametrize('text',[
    '[N0]合同导致的项目进度提前导致即时供气增加',
    '[N0]若合同导致项目进度提前并导致即时供气增加',
])
def test_project_acceleration_criteria_do_not_assert_current_delivery(actual,text):
    _check_prospective_supply_scope(condition(text),actual[1],actual[2])


@pytest.mark.parametrize('section,field',[
    ('analysis','summary'),('analysis','evidence'),('news_impact','summary'),
])
def test_same_unmarked_flow_words_in_factual_fields_remain_assertions(actual,section,field):
    text='[N1]维护提前至本窗口并导致实际供气损失'
    out={section:{field:[text] if field=='evidence' else text}}
    with pytest.raises(ValueError,match='未来维护公告不能写成当前实际'):
        _check_gassco_physical_state(out,actual[1],actual[2])
    text='[N0]合同导致的项目进度提前导致即时供气增加'
    out={section:{field:[text] if field=='evidence' else text}}
    with pytest.raises(ValueError,match='未给已投产或实际交付'):
        _check_prospective_supply_scope(out,actual[1],actual[2])


@pytest.mark.parametrize('text',[
    '[N1]已经确认当前停供，因此需要改变原结论',
    '[N1]实际流量损失已经确认，因此原判断失效',
    '[N1]若未来维护提前，但[N1]已经确认当前停供，因此须修改预测',
])
def test_invalidation_field_does_not_hide_current_confirmed_flow_fact(actual,text):
    with pytest.raises(ValueError,match='未来维护公告不能写成当前实际'):
        _check_gassco_physical_state(condition(text),actual[1],actual[2])


@pytest.mark.parametrize('text',[
    '[N0]合同已经导致即时供气增加，因此需要修改结论',
    '[N0]项目已经完成投产并导致即时供气增加',
    '[N0]若未来项目扩建，但[N0]合同已经导致即时供气增加',
])
def test_project_current_completed_facts_are_not_implicit_hypotheses(actual,text):
    with pytest.raises(ValueError,match='未给已投产或实际交付'):
        _check_prospective_supply_scope(condition(text),actual[1],actual[2])


def test_quantities_capacity_roles_pricing_and_well_completion_are_still_checked(actual):
    ctx,prepared=actual[1:]
    with pytest.raises(ValueError,match='公告不可用容量不等于'):
        _check_gassco_physical_state(condition('[N1]维护提前并停产99mcm/d'),ctx,prepared)
    with pytest.raises(ValueError):
        _check_gassco_physical_state(condition('[N1]实际流量损失99mcm/d被确认'),ctx,prepared)
    with pytest.raises(ValueError):
        _validate_notice_source_facts(condition('[N1]若公告不可用容量为99mcm/d'),ctx,prepared)
    with pytest.raises(ValueError,match='授予/委托方'):
        _check_contract_party_roles(condition('[N0]LNG Canada获授EPCM合同'),ctx,prepared)
    with pytest.raises(ValueError,match='不能宣称已计价'):
        _check_priced_in(condition('[N0]该消息已计价'),ctx,prepared)
    well=copy.deepcopy(ctx);well['current_news']={'title':'Well discovery',
        'summary':'The well will now be permanently plugged and abandoned.',
        'published_at':ctx['as_of']}
    well_prepared,_=prepare_context(well,True)
    with pytest.raises(ValueError,match='来源仅说该井将封堵'):
        _check_future_well_action_not_completed(condition('[N0]井已弃置'),well,well_prepared)


def test_explicit_if_can_make_a_confirmation_itself_hypothetical(actual):
    _check_gassco_physical_state(condition('[N1]若已经确认当前实际流量损失，则重新评估'),actual[1],actual[2])
