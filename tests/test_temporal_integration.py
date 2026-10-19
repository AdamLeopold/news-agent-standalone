"""Cross-module checks for evidence time, settlement freezing and evaluation.

All databases are temporary; no production data or model requests are used.
"""

from datetime import date
import json
import statistics
from unittest.mock import patch

from ttf_news.analysis import prepare_context
from ttf_news.calendar import parse_time, previous_day, session_at
from ttf_news.context import build_context, price_context, update_event_memory
from ttf_news.evaluation import enrich_outcomes, evaluate_frozen
from ttf_news.runtime import freeze_due
from ttf_news.store import Store


def store(tmp_path):
    return Store(tmp_path / "temporal.sqlite3")


def add_news(db, *, observed, summary, updated=None):
    item = {"source": "test_source", "url": "https://example.test/outage", "title": "Norway supply outage",
            "summary": summary, "published_at": "2026-10-14T01:00:00+00:00", "updated_at": updated}
    with patch("ttf_news.store.iso", return_value=observed):
        return db.upsert_news(item)


def add_event(db, *, observed, title, known=None):
    with patch("ttf_news.store.iso", return_value=observed):
        db.upsert_event({"id": "norway-event", "title": title, "summary": title, "event_date": "2026-10-13",
                         "known_at": known or observed, "source": "test_source", "state": "active",
                         "outcome_available_at": None})


def finish_analysis(db, *, created, completed, kind="live", ref=None, direction="up"):
    ctx = {"as_of": created, "session": session_at(created), "price_reference": ref or {}}
    with patch("ttf_news.store.iso", return_value=created):
        record = db.create_analysis(ctx, kind=kind)
    job = db.claim_job()
    assert job["analysis_id"] == record["id"]
    with patch("ttf_news.store.iso", return_value=completed):
        db.finish_job(job, result={"close_forecast": {"direction": direction}})
    return record["id"]


def test_news_revision_and_late_discovery_use_asof_version(tmp_path):
    db = store(tmp_path)
    old = add_news(db, observed="2026-10-14T02:00:00+00:00", summary="Unplanned outage removes 20 mcm/day.")
    new = add_news(db, observed="2026-10-14T04:00:00+00:00", summary="Supply restored, earlier outage ended.", updated="2026-10-14T04:00:00+00:00")
    assert new["version"] == 2
    assert new["first_seen_at"] == old["first_seen_at"]
    assert db.window_news("2026-10-14", "2026-10-14T03:00:00+00:00")[0]["summary"] == old["summary"]
    assert db.window_news("2026-10-14", "2026-10-14T05:00:00+00:00")[0]["summary"] == new["summary"]
    assert db.window_news("2026-10-14", "2026-10-14T01:59:59+00:00") == []


def test_news_asof_is_independent_of_offset_representation(tmp_path):
    db = store(tmp_path)
    old = add_news(db, observed="2026-10-14T02:00:00+00:00", summary="Original outage")
    add_news(db, observed="2026-10-14T04:00:00+00:00", summary="Later restoration")
    # 11:00 Singapore is 03:00 UTC, before the 04:00 revision.
    assert db.window_news("2026-10-14", "2026-10-14T11:00:00+08:00")[0]["summary"] == old["summary"]


def test_event_version_updated_at_matches_asof_and_remains_available_to_model(tmp_path):
    db = store(tmp_path)
    add_event(db, observed="2026-10-14T02:00:00+00:00", title="Norway outage initial")
    add_event(db, observed="2026-10-14T04:00:00+00:00", title="Norway outage restored", known="2026-10-14T02:00:00+00:00")
    events = db.retrieve_events("Norway outage", "2026-10-14T03:00:00+00:00")
    assert len(events) == 1 and events[0]["title"] == "Norway outage initial"
    ctx = build_context(db, {"id": "manual", "title": "Norway outage test", "summary": "European gas supply loss"}, as_of="2026-10-14T03:00:00+00:00", kind="manual")
    model_input, meta = prepare_context(ctx)
    assert len(model_input["historical_events"]) == 1
    assert meta["filtered_future_event_count"] == 0


def test_event_asof_is_independent_of_offset_representation(tmp_path):
    db = store(tmp_path)
    add_event(db, observed="2026-10-14T02:00:00+00:00", title="Norway outage initial")
    add_event(db, observed="2026-10-14T04:00:00+00:00", title="Norway outage restored", known="2026-10-14T02:00:00+00:00")
    assert db.retrieve_events("Norway outage", "2026-10-14T11:00:00+08:00")[0]["title"] == "Norway outage initial"


def test_legacy_outcomes_are_archived_but_not_model_evidence(tmp_path):
    db = store(tmp_path)
    with patch("ttf_news.store.iso", return_value="2026-10-14T02:00:00+00:00"):
        db.upsert_event({"id": "legacy-event", "title": "Norway supply outage", "summary": "Historic daily aggregate", "event_date": "2024-01-01", "known_at": "2026-10-14T02:00:00+00:00", "source": "legacy_daily",
                         "legacy_label": "extreme_up", "legacy_outcomes": {"future_delta": 900, "Explanation": "Test forbidden label"}, "outcome_available_at": None})
    ctx = build_context(db, {"id": "manual", "title": "Norway supply outage", "summary": "Supply fell"}, as_of="2026-10-14T03:00:00+00:00", kind="manual")
    assert "legacy_outcomes" not in ctx["historical_events"][0]
    assert "legacy_label" not in ctx["historical_events"][0]
    assert "legacy_outcomes" in db.events()[0]
    model_input, _ = prepare_context(ctx)
    assert "forbidden" not in json.dumps(model_input)


def test_freeze_latest_completed_live_prediction_excludes_manual_and_late_result(tmp_path):
    db = store(tmp_path)
    first = finish_analysis(db, created="2026-10-14T02:00:00+00:00", completed="2026-10-14T14:00:00+00:00")
    last = finish_analysis(db, created="2026-10-14T03:00:00+00:00", completed="2026-10-14T15:10:00+00:00", direction="down")
    manual = finish_analysis(db, created="2026-10-14T04:00:00+00:00", completed="2026-10-14T15:14:00+00:00", kind="manual", direction="extreme_up")
    late = finish_analysis(db, created="2026-10-14T05:00:00+00:00", completed="2026-10-14T15:16:00+00:00", direction="extreme_down")
    freeze_due(db, parse_time("2026-10-14T15:17:00+00:00"))
    with db.connection() as conn:
        snapshot = dict(conn.execute("SELECT * FROM daily_snapshots WHERE trade_date='2026-10-14'").fetchone())
    assert snapshot["analysis_id"] == last
    assert db.analysis(last)["frozen"] == 1
    assert all(db.analysis(identifier)["frozen"] == 0 for identifier in (first, manual, late))
    freeze_due(db, parse_time("2026-10-15T16:00:00+00:00"))
    with db.connection() as conn:
        assert conn.execute("SELECT analysis_id FROM daily_snapshots WHERE trade_date='2026-10-14'").fetchone()[0] == last
    assert db.counts()["news"] == 0 and db.counts()["events"] == 0


def test_same_contract_threshold_excludes_target_day_and_different_contract(tmp_path):
    db = store(tmp_path)
    days = []
    day = date(2026, 10, 14)
    for _ in range(65):
        day = previous_day(day)
        days.append(day.isoformat())
    history = []
    for index, day in enumerate(sorted(days)):
        value = 40 + (index % 4) * 0.10
        history.append(value)
        db.upsert_price({"contract": "A", "delivery_month": "2026-11-01", "day": day, "value": value, "price_type": "settlement", "verified": True})
        db.upsert_price({"contract": "B", "delivery_month": "2026-12-01", "day": day, "value": 1000 * index, "price_type": "settlement", "verified": True})
    db.upsert_price({"contract": "A", "delivery_month": "2026-11-01", "day": "2026-10-14", "value": 9999, "price_type": "settlement", "verified": True})
    ref = price_context(db, "2026-10-14T03:00:00+00:00")
    expected_sigma = statistics.stdev([b-a for a,b in zip(history, history[1:])][-60:])
    assert ref["contract"] == "A" and ref["previous_price"] == history[-1]
    assert ref["price_date"] == "2026-10-13"
    assert abs(ref["sigma60"] - expected_sigma) < 1e-10
    assert abs(ref["extreme_threshold"] - max(0.30, 2*expected_sigma)) < 1e-10


def test_stale_previous_price_must_not_be_scored_as_daily_settlement_change(tmp_path):
    db = store(tmp_path)
    ref = {"contract": "A", "verified": True, "price_type": "settlement", "previous_price": 40,
           "price_date": "2026-10-09", "extreme_threshold": 2.0, "reference_source": "verified test fixture"}
    finish_analysis(db, created="2026-10-14T03:00:00+00:00", completed="2026-10-14T15:00:00+00:00", ref=ref)
    freeze_due(db, parse_time("2026-10-14T15:16:00+00:00"))
    db.upsert_price({"contract": "A", "day": "2026-10-14", "value": 41, "price_type": "settlement", "verified": True})
    evaluate_frozen(db)
    with db.connection() as conn:
        evaluation = dict(conn.execute("SELECT * FROM evaluations WHERE trade_date='2026-10-14'").fetchone())
    assert evaluation["status"] != "evaluated"
    assert evaluation["correct"] is None


def test_future_price_observations_must_not_create_available_event_outcomes(tmp_path):
    db = store(tmp_path)
    add_event(db, observed="2026-10-13T02:00:00+00:00", title="Norway outage")
    for day, value in (("2026-10-13", 40), ("2026-10-15", 99)):
        db.upsert_price({"contract": "A", "day": day, "value": value, "price_type": "reference", "verified": False})
    with patch("ttf_news.evaluation.iso", return_value="2026-10-14T03:00:00+00:00"), patch("ttf_news.store.iso", return_value="2026-10-14T03:00:00+00:00"):
        enrich_outcomes(db)
    assert not db.events()[0].get("outcomes")


def operational_news(identifier="news-1", source_message_id="12345_1", **values):
    return {"id": identifier, "title": "Gassco Kollsnes maintenance", "summary": "Technical capacity unavailable during planned maintenance; actual lost flow is not reported.",
            "source": "gassco", "url": "https://example.test/umm/"+identifier, "source_message_id": source_message_id,
            "published_at": "2026-10-14T01:00:00+00:00", "published_date": "2026-10-14",
            "first_seen_at": "2026-10-14T02:00:00+00:00", "version_seen_at": "2026-10-14T02:00:00+00:00",
            "ingestion_kind": "live", "event_start": "2026-10-14T01:00:00+00:00", "event_end": "2026-10-14T06:00:00+00:00", "event_status": "active", **values}


def event_update_at(db, news, moment="2026-10-14T03:00:00+00:00"):
    with patch("ttf_news.context.iso", return_value=moment), patch("ttf_news.store.iso", return_value=moment):
        update_event_memory(db, news)


def test_gassco_revisions_merge_and_late_old_revision_does_not_resurrect(tmp_path):
    db = store(tmp_path)
    event_update_at(db, operational_news())
    event_update_at(db, operational_news("news-2", "12345_2", event_status="dismissed", version_seen_at="2026-10-14T04:00:00+00:00"), "2026-10-14T04:00:00+00:00")
    event_update_at(db, operational_news("news-old", "12345_1", version_seen_at="2026-10-14T05:00:00+00:00"), "2026-10-14T05:00:00+00:00")
    events = db.events()
    assert len(events) == 1
    assert events[0]["id"] == "event-gassco-12345"
    assert events[0]["state"] == "resolved"
    assert events[0]["source_revision"] == 2
    assert set(events[0]["news_ids"]) == {"news-1", "news-2", "news-old"}
    assert db.retrieve_events("Kollsnes", "2026-10-14T03:30:00+00:00")[0]["state"] == "active"


def test_gassco_unchanged_replay_does_not_create_a_new_event_version(tmp_path):
    db = store(tmp_path)
    event_update_at(db, operational_news())
    event_update_at(db, operational_news(), "2026-10-14T04:00:00+00:00")
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM event_versions").fetchone()[0] == 1


def test_gassco_scheduled_and_finished_windows_are_time_aware(tmp_path):
    db = store(tmp_path)
    event_update_at(db, operational_news(event_start="2027-01-01T00:00:00+00:00", event_end="2027-01-02T00:00:00+00:00"))
    assert db.events()[0]["state"] == "scheduled"
    event_update_at(db, operational_news("news-2", "12345_2", event_start="2026-10-14T01:00:00+00:00", event_end="2026-10-14T02:00:00+00:00"), "2026-10-14T04:00:00+00:00")
    assert db.events()[0]["state"] == "resolved"


def test_future_start_without_known_end_is_not_currently_active(tmp_path):
    db = store(tmp_path)
    event_update_at(db, operational_news(event_start="2027-01-01T00:00:00+00:00", event_end=None))
    assert db.events()[0]["state"] == "scheduled"


def test_pending_restart_is_active_and_confirmed_full_restart_is_resolved(tmp_path):
    db = store(tmp_path)
    news = operational_news(source="offshore_energy", source_message_id=None, event_start=None, event_end=None,
                            title="LNG terminal shutdown", summary="Restart is not confirmed; the operator expects to restart next month.")
    event_update_at(db, news)
    assert db.events()[0]["state"] == "active"
    event_update_at(db, {**news, "summary": "The LNG terminal has fully restarted and resumed operations.", "version_seen_at": "2026-10-14T04:00:00+00:00"}, "2026-10-14T04:00:00+00:00")
    assert db.events()[0]["state"] == "resolved"


def test_partial_restart_does_not_end_the_supply_event(tmp_path):
    db = store(tmp_path)
    news = operational_news(source="offshore_energy", source_message_id=None, event_start=None, event_end=None,
                            title="LNG terminal shutdown", summary="The facility has restarted at 20% capacity; full output is not yet available.")
    event_update_at(db, news)
    assert db.events()[0]["state"] == "active"
