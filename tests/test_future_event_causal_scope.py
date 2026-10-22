"""A not-yet-started event has no physical causal effect yet, not global flow data."""
import copy
import json
from pathlib import Path

import pytest

from ttf_news.analysis import _validate_notice_source_facts, prepare_context
from ttf_news.semantics import _check_gassco_physical_state, _check_unknown_as_fact


def fixture():
    return json.loads((Path(__file__).parent/'fixtures/live_v2_2_manual_f961_failure.json').read_text())


def test_actual_manual_second_negates_only_the_future_events_physical_effect():
    record=fixture();context=record['input_snapshot'];prepared,_=prepare_context(context)
    result=copy.deepcopy(record['raw_results'][1]);before=copy.deepcopy(result)
    _check_gassco_physical_state(result,context,prepared)
    _check_unknown_as_fact(result,context,prepared)
    assert result==before


@pytest.mark.parametrize('claim',[
    'Troll气田计划维护公告未导致实际供气损失。',
    'Troll气田维护尚未造成当前实际流量减少。',
    '[N2]这项维护没有造成即时物理供气损失。',
    '本次未来维护未导致实际供气损失，但价格反应未知。',
])
def test_future_event_subject_negated_cause_is_source_time_fact(claim):
    context=fixture()['input_snapshot'];prepared,_=prepare_context(context)
    output={'analysis':{'summary':claim,'evidence':['[N1]未来维护容量风险','[N2]未来维护容量风险']}}
    _check_gassco_physical_state(output,context,prepared)
    _check_unknown_as_fact(output,context,prepared)


@pytest.mark.parametrize('claim',[
    'Troll当前没有实际供气损失。',
    'Troll气田实际流量保持不变。',
    'Troll整体计划维护未导致实际供气损失。',
    'Troll气田维护已经造成实际供气损失。',
])
def test_global_zero_or_affirmed_loss_is_not_licensed_by_future_event(claim):
    context=fixture()['input_snapshot'];prepared,_=prepare_context(context)
    output={'analysis':{'summary':claim,'evidence':['[N1]未来维护容量风险','[N2]未来维护容量风险']}}
    with pytest.raises(ValueError):
        _check_gassco_physical_state(output,context,prepared)
        _check_unknown_as_fact(output,context,prepared)


def test_active_notice_cannot_prove_causally_no_loss_without_flow_measurement():
    context=fixture()['input_snapshot'];context['as_of']='2026-10-22T05:00:00Z';prepared,_=prepare_context(context)
    output={'analysis':{'summary':'Troll气田计划维护公告未导致实际供气损失。','evidence':['[N1]维护容量风险']}}
    with pytest.raises(ValueError):
        _check_gassco_physical_state(output,context,prepared)
        _check_unknown_as_fact(output,context,prepared)


def test_actual_single_day_summary_uses_that_days_notice_not_current_card():
    record=fixture();context=record['input_snapshot'];prepared,_=prepare_context(context)
    claim='2026-10-21窗口内，Troll气田计划维护导致可用容量下降13.0mcm/d，形成未来容量约束。'
    _validate_notice_source_facts({'analysis':{'summary':claim}},context,prepared)
    for wrong in [claim.replace('13.0','7.6'),claim.replace('2026-10-21','2027-10-21'),claim.replace('可用容量下降','可用容量为'), '[N1]'+claim]:
        with pytest.raises(ValueError):
            _validate_notice_source_facts({'analysis':{'summary':wrong}},context,prepared)
    good='[N2]'+claim
    _validate_notice_source_facts({'analysis':{'summary':good}},context,prepared)


def test_actual_first_prices_reaction_claim_still_rejected():
    record=fixture();context=record['input_snapshot'];prepared,_=prepare_context(context)
    # Capacity binding is no longer a false error, but the definite price
    # effect assertion still lacks observed announcement reaction evidence.
    _validate_notice_source_facts(record['raw_results'][0],context,prepared)
    with pytest.raises(ValueError,match='价格反应'):
        _check_gassco_physical_state(record['raw_results'][0],context,prepared)
