"""Frozen v2.1 anchor controls retain source facts and original v2.0 evidence."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from ttf_news.analysis import prepare_context
from ttf_news.calendar import parse_time


ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'tests/fixtures/manual_stale_previous_settlement_6601.json'
ORIGINAL=ROOT/'tests/fixtures/frozen_comparison_cases_v2_0.json'
REPORT_CASES=ROOT/'reports/repair_2026-10-20/comparison_cases.json'
CASES=ROOT/'reports/repair_2026-10-20/anchor_cases_v2_1.json'
SOURCE_SHA='bd6cb7a52aa0307f8ec124bf5ab6574d2e492434ec58a26b0c556e372b272011'
ORIGINAL_SHA='8d6f9202d21a3d27ac0f6714802d8da2eda76d321e4c820c0d4f73aae41eacd5'


def cases():return json.loads(CASES.read_text())


def test_anchor_cases_are_reproducible_without_modifying_source_or_original_fifteen():
    source_bytes=SOURCE.read_bytes();original_bytes=ORIGINAL.read_bytes()
    assert hashlib.sha256(source_bytes).hexdigest()==SOURCE_SHA
    assert hashlib.sha256(original_bytes).hexdigest()==ORIGINAL_SHA
    spec=importlib.util.spec_from_file_location('anchor_fixture_builder',ROOT/'scripts/validate-news-logic.py')
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    frozen=json.loads(source_bytes);legal=next(row for row in json.loads(original_bytes) if row['id']=='weak_news_observed_market_up')
    before=copy.deepcopy((frozen,legal))
    assert cli.anchor_cases_from_frozen(frozen,legal,SOURCE_SHA)==cases()
    assert (frozen,legal)==before
    assert SOURCE.read_bytes()==source_bytes and ORIGINAL.read_bytes()==original_bytes


def test_report_fixture_remains_semantically_associated_with_original_frozen_inputs():
    # Publication intentionally re-formats sanitized reports.  The separately
    # checked public test fixture retains the exact original byte freeze.
    assert json.loads(REPORT_CASES.read_text())==json.loads(ORIGINAL.read_text())


def test_real_replay_keeps_entire_canonical_input_with_explicit_no_outlook_contract():
    row=cases()[0];frozen=json.loads(SOURCE.read_text())
    assert row['candidate_context']==frozen['input_snapshot']
    assert not row['synthetic'] and row['source_fixture_sha256']==SOURCE_SHA
    assert row['expected_news']=='flat' and row['expected_close'] is None
    assert not row['requires_original_comparison']


@pytest.mark.parametrize('position,news_direction,verb',[(1,'up','减少40'),(2,'down','增加40')])
def test_gap_supply_controls_replace_all_old_project_facts_in_current_window(position,news_direction,verb):
    row=cases()[position];ctx=row['candidate_context'];news=ctx['current_news']
    assert row['synthetic'] and news['synthetic_fixture'] and news['id'].startswith('synthetic-')
    assert news['source']=='synthetic_anchor_control'
    assert verb in news['summary'] and '独立出口计量' in news['summary']
    serialized=json.dumps(ctx,ensure_ascii=False)
    assert all(term not in serialized for term in ('EPCM','Coastal GasLink','Técnicas'))
    assert ctx['window_news']==[news] and ctx['previous_sessions']==[] and ctx['historical_events']==[]
    when=parse_time(news['published_at'])
    assert parse_time(ctx['session']['window_start'])<=when<parse_time(ctx['session']['window_end'])
    assert row['expected_news']==news_direction and 'expected_close' not in row and 'expected_close_sign' not in row
    assert row['close_contract']=='source_supported_conditional_forecast_or_insufficient_evidence'


@pytest.mark.parametrize('position',[0,1,2])
def test_gap_price_controls_keep_absolute_quote_but_do_not_supply_old_yesterday_delta(position):
    row=cases()[position];ctx=row['candidate_context'];original=copy.deepcopy(ctx)
    assert ctx['session']['trade_date']=='2026-10-21'
    assert ctx['price_reference']['price_date']=='2026-10-19'
    prepared,_=prepare_context(ctx);price=prepared['price_reference']
    assert not price['settlement_anchor']['available']
    assert price['settlement_anchor']['expected_previous_trade_date']=='2026-10-20'
    assert price['intraday']['value']==86.585
    assert 'previous_price' not in price and '88.04' not in json.dumps(price)
    assert not prepared['evidence_ledger']['pricing_observation']['forecast_use']['same_contract_anchor_available']
    assert ctx==original


def test_legal_previous_day_control_preserves_observed_up_baseline():
    row=cases()[3];ctx=row['candidate_context'];prepared,_=prepare_context(ctx)
    assert row['synthetic'] and row['expected_news']=='flat' and row['expected_close']=='up'
    assert ctx['session']['trade_date']=='2026-10-20' and ctx['price_reference']['price_date']=='2026-10-19'
    assert prepared['price_reference']['settlement_anchor']['available']
    assert prepared['price_reference']['market_baseline']['available']
    assert prepared['price_reference']['market_baseline']['observed_direction']=='up'
    assert prepared['evidence_ledger']['pricing_observation']['forecast_use']['same_contract_anchor_available']
