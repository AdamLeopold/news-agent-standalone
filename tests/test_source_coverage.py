import copy
from datetime import datetime, timedelta, timezone

import pytest

from ttf_news.context import build_context
from ttf_news.coverage import news_source_health, source_coverage_gaps
from ttf_news.store import Store


NOW = datetime(2026, 10, 20, 21, 33, 54, tzinfo=timezone.utc)


def stamp(seconds=0):
    return (NOW + timedelta(seconds=seconds)).isoformat()


def state(identifier="equinor", **changes):
    return {"id": identifier, "status": "running", "last_success_at": stamp(-39),
            "last_attempt_at": stamp(-10), "last_finished_at": stamp(-39),
            "last_error": None, **changes}


def health(row):
    return news_source_health([row], stamp())[0]


def test_active_refresh_with_recent_success_is_not_failed_or_missing():
    source = state()
    original = copy.deepcopy(source)
    row = health(source)
    assert row["coverage_status"] == "refreshing"
    assert row["coverage_gap"] is False
    assert row["gap_reasons"] == []
    assert row["coverage_note"] == ""
    for key in ("last_success_at", "last_attempt_at", "last_finished_at"):
        assert row[key] == source[key]
    assert source == original
    assert source_coverage_gaps({"sources": [source]}, stamp()) == []


def test_only_registered_news_feeds_participate_in_news_coverage():
    ids = ["offshore_energy", "eia", "eu_energy", "equinor", "cheniere", "gassco", "gdelt"]
    rows = [state(identifier) for identifier in ids]
    rows += [state(identifier, status="failed", last_error="timeout") for identifier in
             ("dbnomics_ice", "ice_chart", "ice_intraday", "capital_candles", "eia_summary_repair")]
    assert [row["id"] for row in news_source_health(rows, stamp())] == ids
    assert source_coverage_gaps({"sources": rows}, stamp()) == []


@pytest.mark.parametrize("changes,expected", [
    ({"status": "failed", "last_error": "ReadTimeout"}, "last_poll_failed"),
    ({"status": "error"}, "last_poll_failed"),
    ({"status": "partial"}, "last_poll_partial"),
    ({"status": "partial_failure"}, "last_poll_partial"),
    ({"status": "backoff"}, "retry_backoff"),
    ({"last_error": "ReadError", "last_finished_at": stamp(-46)}, "last_poll_error"),
    ({"last_success_at": stamp(-301)}, "last_success_stale"),
    ({"last_attempt_at": stamp(-181)}, "poll_timed_out"),
    ({"last_success_at": None}, "first_success_unavailable"),
])
def test_real_failures_partial_backoff_stale_and_timeout_remain(changes, expected):
    row = health(state(**changes))
    assert row["coverage_gap"] is True
    assert expected in row["gap_reasons"]
    assert row["coverage_note"].startswith("来源equinor：")
    assert "可能" not in row["coverage_note"]


def test_gdelt_429_remains_a_real_gap_while_waiting_for_retry():
    row = health(state("gdelt", status="failed", last_success_at=None,
                       last_error="429 Too Many Requests", next_poll_at=stamp(79)))
    assert row["coverage_status"] == "backoff"
    assert "retry_backoff" in row["gap_reasons"]
    assert row["last_error"] == "429 Too Many Requests"
    assert row["next_poll_at"] == stamp(79)


def test_exact_freshness_and_timeout_boundaries_are_not_premature_failures():
    row = health(state(last_success_at=stamp(-300), last_attempt_at=stamp(-180)))
    assert row["coverage_gap"] is False
    assert row["coverage_status"] == "refreshing"


def test_old_success_without_clocks_is_not_invented_to_be_stale():
    row = health({"id": "gassco", "status": "ok"})
    assert row["coverage_status"] == "healthy"
    assert not row["coverage_gap"]


def test_future_poll_status_cannot_retroactively_fail_a_recent_success():
    row = health(state(status="failed", last_attempt_at=stamp(1),
                       last_error="failure after analysis as_of"))
    assert row["state_clock_after_as_of"] is True
    assert row["coverage_status"] == "healthy"
    assert not row["coverage_gap"]


def test_future_success_does_not_satisfy_first_success_requirement():
    row = health(state(last_success_at=stamp(1)))
    assert row["coverage_gap"] is True
    assert "first_success_unavailable" in row["gap_reasons"]


def test_known_past_failure_is_not_erased_by_next_attempt_after_as_of():
    row = health(state(last_success_at=stamp(-50), last_attempt_at=stamp(1),
                       last_finished_at=stamp(-20), last_error_at=stamp(-20),
                       last_error="ReadError"))
    assert row["state_clock_after_as_of"] is True
    assert row["coverage_gap"] is True
    assert row["coverage_status"] == "failed"
    assert row["gap_reasons"] == ["last_poll_error"]
    assert row["last_error_at"] == stamp(-20)


@pytest.mark.parametrize("attempt", [-5, 1])
def test_explicit_old_error_superseded_by_later_known_success_is_not_current_gap(attempt):
    row = health(state(last_success_at=stamp(-10), last_attempt_at=stamp(attempt),
                       last_error_at=stamp(-20), last_error="residual ReadError"))
    assert row["coverage_gap"] is False
    assert row["coverage_status"] in {"healthy", "refreshing"}
    assert row["last_error"] == "residual ReadError"


def test_explicit_future_error_is_not_an_already_known_failure():
    row = health(state(last_attempt_at=stamp(1), last_error_at=stamp(2),
                       last_error="future ReadError"))
    assert not row["coverage_gap"]
    assert row["coverage_status"] == "healthy"


def test_future_attempt_and_success_cannot_establish_healthy_at_earlier_as_of():
    row = health(state(status="ok", last_attempt_at=stamp(1), last_success_at=stamp(2)))
    assert row["coverage_status"] == "unknown"
    assert not row["coverage_gap"]
    assert row["state_clock_after_as_of"] is True


def test_future_poll_with_known_past_fresh_success_still_has_healthy_known_coverage():
    row = health(state(status="ok", last_attempt_at=stamp(1), last_success_at=stamp(-20)))
    assert row["coverage_status"] == "healthy"
    assert not row["coverage_gap"]


def test_rechecking_compact_snapshot_does_not_trust_stale_gap_metadata():
    row = state(coverage_gap=True, coverage_status="failed", gap_reasons=["last_poll_failed"])
    assert source_coverage_gaps({"sources": [row]}, stamp()) == []
    compact = news_source_health([row], stamp())
    assert news_source_health(compact, stamp()) == compact


def test_context_build_filters_price_and_repair_states_without_mutating_store(tmp_path):
    store = Store(tmp_path / "coverage.sqlite3")
    store.set_source_state("equinor", state())
    store.set_source_state("dbnomics_ice", state("dbnomics_ice", last_error="updating prices"))
    store.set_source_state("eia_summary_repair", {"status": "failed", "last_error": "old repair"})
    before = store.sources()
    context = build_context(store, as_of=stamp())
    assert [row["id"] for row in context["coverage"]["sources"]] == ["equinor"]
    assert context["coverage"]["sources"][0]["coverage_status"] == "refreshing"
    assert context["coverage"]["sources"][0]["coverage_gap"] is False
    assert store.sources() == before


def test_disabled_source_is_not_claimed_to_be_failing():
    row = health(state(enabled=False, status="failed", last_error="old failure"))
    assert row["coverage_status"] == "disabled"
    assert not row["coverage_gap"]


def test_analysis_compression_and_limitations_share_source_health_at_all_levels():
    from ttf_news.analysis import _add_known_limitations, _coverage, _detail_limitation_options
    rows=[state('equinor'),state('gassco',status='failed',last_error='ReadError'),state('gdelt',status='backoff',last_error='429 Too Many Requests'),state('dbnomics_ice',status='failed',last_error='timeout')]
    value={'sources':rows};original=copy.deepcopy(value)
    for level in range(4):
        compact=_coverage(value,level,stamp());by_id={row['id']:row for row in compact['sources']}
        assert set(by_id)=={'equinor','gassco','gdelt'}
        assert by_id['equinor']['coverage_status']=='refreshing'
        assert by_id['equinor']['coverage_gap'] is False
        for key in ('last_success_at','last_attempt_at','last_finished_at'):
            assert by_id['equinor'][key]==rows[0][key]
        gaps={row['id'] for row in source_coverage_gaps(compact,stamp())}
        assert gaps=={'gassco','gdelt'}
        options=_detail_limitation_options({'coverage':compact,'as_of':stamp()})
        coverage_options=[line for line in options if line.startswith('coverage:')]
        assert len(coverage_options)==2
        assert not any('equinor' in line or 'dbnomics' in line or '可能不完整' in line for line in coverage_options)
    result={'risk_flags':[],'missing_fields':[]};meta={}
    context={'as_of':stamp(),'coverage':value,'current_news':{}}
    _add_known_limitations(result,context,meta)
    assert {'source_coverage:gassco','source_coverage:gdelt'}<=set(result['missing_fields'])
    assert 'source_coverage:equinor' not in result['missing_fields']
    assert 'source_coverage:dbnomics_ice' not in result['missing_fields']
    assert not any('可能不完整' in line for line in result['risk_flags'])
    assert value==original
