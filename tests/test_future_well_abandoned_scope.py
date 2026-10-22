"""Observed 2.3 short abandonment claims retain the original source tense."""
import copy
import json
from pathlib import Path

import pytest

from ttf_news.analysis import prepare_context
from ttf_news.semantics import (_check_future_well_action_not_completed,
                                collect_analysis_semantic_errors)


def actual():
    return json.loads((Path(__file__).parent/'fixtures/actual_future_well_abandoned_v2_3.json').read_text())


def context(source='The well will now be permanently plugged and abandoned.'):
    return {'as_of':'2026-10-20T12:00:00+00:00',
            'current_news':{'id':'test-well','title':'Gas discovery at wellA','summary':source,
                            'published_at':'2026-10-20T10:00:00+00:00'}}


def test_both_actual_raw_results_report_short_completion_and_pricing_errors_without_mutation():
    fixture=actual();ctx=fixture['input_snapshot'];prepared,_=prepare_context(ctx)
    before=copy.deepcopy(fixture)
    for row in fixture['raw_results']:
        result=row['result']
        with pytest.raises(ValueError,match='来源仅说该井将封堵/弃置'):
            _check_future_well_action_not_completed(result,ctx,prepared)
        errors=collect_analysis_semantic_errors(result,ctx,prepared)
        assert any('来源仅说该井将封堵/弃置' in text for text in errors)
        assert any('不能宣称未计入价格/尚未计价' in text for text in errors)
    assert fixture==before


@pytest.mark.parametrize('text',[
    '[N0]井已弃置。', '[N0]该井已被弃置。', '[N0]该井已经弃井。',
    '[N0]井现已永久弃置。', '[N0]井已弃置，若需求回升则重新分析。',
])
def test_sourced_future_action_cannot_become_short_completed_claim(text):
    with pytest.raises(ValueError,match='来源仅说该井将封堵/弃置'):
        _check_future_well_action_not_completed({'analysis':{'summary':text}},context(),{})


@pytest.mark.parametrize('text',[
    '[N0]该井将被永久弃置。', '[N0]计划弃井，尚无完成更新。',
    '[N0]该井尚未被弃置，原报道保留未来动作。',
    '[N0]不能证明井已弃置。', '[N0]并非该井已经弃置。',
    '[N0]不能声称该井已被弃置。', '[N0]如果井已弃置，仍须核对气田开发安排。',
    '[N0]井已弃置与否仍待核实。', '[N0]永久弃置。',
])
def test_future_negated_conditional_or_bare_action_is_not_completed_assertion(text):
    _check_future_well_action_not_completed({'analysis':{'summary':text}},context(),{})


@pytest.mark.parametrize('source',[
    'The well has been permanently plugged and abandoned.',
    'The well will now be permanently plugged and abandoned. Update: the well was permanently plugged and abandoned.',
    '该井将永久封堵并弃井。独立完成更新确认该井已经弃置。',
])
def test_known_same_source_completion_update_supports_completed_fact(source):
    _check_future_well_action_not_completed({'analysis':{'summary':'[N0]该井已被弃置。'}},context(source),{})


def test_other_n_is_not_bound_to_current_future_action_or_used_to_confirm_it():
    ctx=context();prepared={'window_news':[{'id':'N1','title':'Other completed well',
        'facts':'The other well was permanently plugged and abandoned.',
        'time':'2026-10-20T10:30:00+00:00'}]}
    _check_future_well_action_not_completed({'analysis':{'summary':'[N1]另一口井已弃置。'}},ctx,prepared)
    with pytest.raises(ValueError,match='N0来源仅说'):
        _check_future_well_action_not_completed({'analysis':{'summary':'[N0]该井已弃置。'}},ctx,prepared)


def test_later_negation_does_not_qualify_prior_completed_fact():
    with pytest.raises(ValueError):
        _check_future_well_action_not_completed(
            {'analysis':{'summary':'[N0]该井已弃置，但不能声称已给气田开发时刻。'}},context(),{})
