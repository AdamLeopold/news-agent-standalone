"""Exact live 7866 detail diagnostics: hypothetical startup and well time."""
import copy
import json
from pathlib import Path

import pytest

from ttf_news.analysis import prepare_context
from ttf_news.semantics import collect_analysis_semantic_errors


@pytest.fixture
def actual():
    fixture=json.loads((Path(__file__).parent/'fixtures/live_detail_scope_and_future_sequence_7866.json').read_text())
    context=fixture['input_snapshot'];prepared,_=prepare_context(context,detail=True)
    return fixture,context,prepared


def result_with_detail(actual,field,text):
    result=copy.deepcopy(actual[0]['attempts'][1]['result'])
    result['detail']['new_facts']=['[N0]该井将永久封井并弃置，资源发现不等于即时交付。']
    result['detail'][field]=[text]
    return result


def test_actual_first_global_zero_flow_is_still_rejected_and_second_gets_correct_tense_diagnostic(actual):
    fixture,context,prepared=actual;original=copy.deepcopy(fixture)
    first=collect_analysis_semantic_errors(fixture['attempts'][0]['result'],context,prepared)
    assert any('不能证明未形成供需冲击' in error for error in first)
    second=collect_analysis_semantic_errors(fixture['attempts'][1]['result'],context,prepared)
    assert any('detail.new_facts[0]:N0来源仅说该井将封堵' in error for error in second)
    assert not any('detail.invalidation_conditions' in error for error in second)
    assert fixture==original


@pytest.mark.parametrize('text',[
    '[N0]发现后若立即投入产能并显著提升供给。',
    '[N0]若资源经开发后投入产能并增加供给，则需重新评估。',
    '[N0]如果立即投入产能并提升供给，需要重新检查净压力。',
])
def test_hypothetical_production_invalidation_is_not_current_delivery(actual,text):
    errors=collect_analysis_semantic_errors(result_with_detail(actual,'invalidation_conditions',text),actual[1],actual[2])
    assert not any('仅有资源发现' in error for error in errors)


@pytest.mark.parametrize('text',[
    '[N0]该发现当前增加供给并抵消维护风险。',
    '[N0]当前已经提升供给，若价格下降则修订判断。',
])
def test_invalidation_field_is_not_wholly_exempt_from_current_supply_claims(actual,text):
    errors=collect_analysis_semantic_errors(result_with_detail(actual,'invalidation_conditions',text),actual[1],actual[2])
    assert any('仅有资源发现' in error for error in errors)


@pytest.mark.parametrize('text',[
    '[N0]发现气体与凝析油，随后永久封井并弃置。',
    '[N0]发现后继而永久封井。',
    '[N0]该井已完成封井。',
])
def test_future_well_action_cannot_be_narrated_as_completed_sequence(actual,text):
    errors=collect_analysis_semantic_errors(result_with_detail(actual,'new_facts',text),actual[1],actual[2])
    assert any('来源仅说该井将封堵' in error for error in errors)


@pytest.mark.parametrize('text',[
    '[N0]发现气体与凝析油，随后将永久封井并弃置。',
    '[N0]该井将随后永久封井并弃置。',
    '[N0]下一步随后永久封井并弃置。',
    '[N0]计划随后永久封井并弃置。',
    '[N0]发现未产出，永久封井。',
    '[N0]若随后永久封井，则尚须核对项目开发计划。',
])
def test_explicit_future_or_bare_action_fragment_keeps_bounded_scope(actual,text):
    errors=collect_analysis_semantic_errors(result_with_detail(actual,'new_facts',text),actual[1],actual[2])
    assert not any('来源仅说该井将封堵' in error for error in errors)


def test_actual_completed_source_supports_completed_sequence(actual):
    context=copy.deepcopy(actual[1]);context['current_news']['summary']='The well was permanently plugged and abandoned after samples were collected.'
    prepared,_=prepare_context(context,detail=True)
    errors=collect_analysis_semantic_errors(result_with_detail(actual,'new_facts','[N0]采样后随后永久封井并弃置。'),context,prepared)
    assert not any('来源仅说该井将封堵' in error for error in errors)
