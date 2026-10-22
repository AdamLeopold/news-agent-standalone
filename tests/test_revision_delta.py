"""Observed revision differences describe changed constraints, not a new outage."""
import copy
import json
from unittest.mock import patch

from ttf_news.context import build_context, enrich_news_revision
from ttf_news.store import Store


def notice(*, unavailable="20", available="59.8", publication="2026-10-20T09:00:00+00:00", **changes):
    return {"id":"same-notice", "source":"gassco", "url":"https://source.test/notice/1",
            "title":"Gassco Nyhamna: Unplanned unavailability (Active)",
            "summary":f"Message ID: 123_1. Publication: {publication}. Event: 2026-10-20T08:00:00Z to 2026-10-20T14:00:00Z. Technical capacity: 79.8 MSm3/d; available: {available}; unavailable: {unavailable}. Reason: Process problems. Remarks: Flow is not independently measured.",
            "published_at":"2026-10-20T09:00:00+00:00", "published_date":"2026-10-20",
            "event_start":"2026-10-20T08:00:00+00:00", "event_end":"2026-10-20T14:00:00+00:00",
            "event_status":"Active", "source_message_id":"123_1", "source_revision":"a",
            "entities":["Gassco","Nyhamna"], **changes}


def save(db, item, clock):
    with patch("ttf_news.store.iso", return_value=clock):
        return db.upsert_news(item)


def rows(db):
    with db.connection() as conn:
        return {table:[tuple(row) for row in conn.execute("SELECT * FROM "+table+" ORDER BY rowid")]
                for table in ("news", "news_versions", "analyses", "daily_snapshots", "metrics", "stream_events")}


def versions(tmp_path):
    db=Store(tmp_path/"revisions.sqlite3")
    first=save(db,notice(),"2026-10-20T09:01:00+00:00")
    second=save(db,notice(unavailable="19.8",available="60",source_revision="b",updated_at="2026-10-20T10:00:00+00:00"),"2026-10-20T10:01:00+00:00")
    assert second["version"]==2
    return db,first,second


def test_capacity_reduction_is_delta_minus_point_two_not_new_loss(tmp_path):
    db,first,second=versions(tmp_path)
    enriched=enrich_news_revision(db,second,"2026-10-20T11:00:00+00:00")
    delta=enriched["revision_delta"]
    assert delta["previous_version"]==1 and delta["previous_known_at"]==first["version_seen_at"]
    assert delta["changed_fields"]["unavailable_capacity"]=={"before":"20","after":"19.8"}
    change=delta["capacity_changes"]["unavailable_capacity"]
    assert change["delta"]=="-0.2" and change["constraint_change"]=="decreased"
    assert change["unit"]=="MSm3/d" and change["interpretation"]=="announced_capacity_change_not_actual_flow"
    assert delta["capacity_changes"]["available_capacity"]["delta"]=="0.2"
    assert delta["substantive_change"] is True
    assert not any(field in delta for field in ("direction","actual_flow_loss","actual_flow_recovery"))


def test_metadata_only_revision_has_no_substantive_change(tmp_path):
    db=Store(tmp_path/"metadata.sqlite3")
    save(db,notice(),"2026-10-20T09:01:00+00:00")
    latest=save(db,notice(publication="2026-10-20T10:00:00+00:00",source_revision="newhash",source_message_id="123_2",updated_at="2026-10-20T10:00:00+00:00"),"2026-10-20T10:01:00+00:00")
    delta=enrich_news_revision(db,latest,"2026-10-20T11:00:00+00:00")["revision_delta"]
    assert latest["version"]==2
    assert delta["changed_fields"]=={} and delta["substantive_change"] is False
    assert "source_revision" in delta["metadata_changed_fields"]
    assert "updated_at" in delta["metadata_changed_fields"]
    assert "capacity_changes" not in delta


def test_future_versions_cannot_enter_delta(tmp_path):
    db,first,second=versions(tmp_path)
    future=save(db,notice(unavailable="0",available="79.8",source_revision="future",updated_at="2026-10-20T12:00:00+00:00"),"2026-10-20T12:01:00+00:00")
    delta=enrich_news_revision(db,second,"2026-10-20T11:00:00+00:00")["revision_delta"]
    assert delta["previous_version"]==1
    assert delta["changed_fields"]["unavailable_capacity"]["after"]=="19.8"
    prior=db.previous_news_version(first["id"],future["version"],"2026-10-20T19:00:00+08:00")
    assert prior["version"]==2 and prior["data"]["source_revision"]=="b"


def test_current_future_version_does_not_receive_past_comparison(tmp_path):
    db,_,second=versions(tmp_path)
    delta=enrich_news_revision(db,second,"2026-10-20T09:30:00+00:00")["revision_delta"]
    assert delta["comparison_status"]=="current_version_not_known_as_of"
    assert delta["previous_version"] is None and delta["changed_fields"]=={}


def test_revision_helper_is_pure_and_leaves_old_records_unchanged(tmp_path):
    db,_,second=versions(tmp_path)
    original=copy.deepcopy(second); before=rows(db)
    enriched=enrich_news_revision(db,second,"2026-10-20T11:00:00+00:00")
    assert enriched is not second and second==original and rows(db)==before
    assert "revision_delta" not in db.get_news(second["id"])


def test_build_context_reuses_one_enrichment_for_current_and_window(tmp_path):
    db,_,second=versions(tmp_path)
    original=db.previous_news_version
    with patch.object(db,"previous_news_version",wraps=original) as read:
        context=build_context(db,second,as_of="2026-10-20T11:00:00+00:00")
    assert read.call_count==1
    assert len(context["window_news"])==1
    assert context["current_news"]["revision_delta"]==context["window_news"][0]["revision_delta"]
    assert "revision_delta" not in second


def test_missing_prior_version_is_unknown_not_unchanged(tmp_path):
    db=Store(tmp_path/"missing.sqlite3")
    item={**notice(),"version":2,"version_seen_at":"2026-10-20T10:01:00+00:00"}
    delta=enrich_news_revision(db,item,"2026-10-20T11:00:00+00:00")["revision_delta"]
    assert delta["comparison_status"]=="previous_version_unavailable"
    assert delta["substantive_change"] is None and delta["changed_fields"]=={}


def test_nearest_lower_version_not_earliest_version_is_compared(tmp_path):
    db,_,second=versions(tmp_path)
    third=save(db,notice(unavailable="19.7",available="60.1",source_revision="c",updated_at="2026-10-20T10:30:00+00:00"),"2026-10-20T10:31:00+00:00")
    delta=enrich_news_revision(db,third,"2026-10-20T11:00:00+00:00")["revision_delta"]
    assert delta["previous_version"]==2 and delta["previous_known_at"]==second["version_seen_at"]
    assert delta["capacity_changes"]["unavailable_capacity"]["delta"]=="-0.1"


def test_operational_timing_and_important_source_message_are_preserved(tmp_path):
    db=Store(tmp_path/"messages.sqlite3")
    first=notice(reason="Process problems",remarks="No independent flow measurement.")
    save(db,first,"2026-10-20T09:01:00+00:00")
    second=save(db,notice(reason="Process problems",remarks="Restart postponed, actual flow remains unverified.",event_end="2026-10-21T14:00:00+00:00",event_status="Dismissed",source_revision="b"),"2026-10-20T10:01:00+00:00")
    changes=enrich_news_revision(db,second,"2026-10-20T11:00:00+00:00")["revision_delta"]["changed_fields"]
    assert changes["event_end"]["before"]==first["event_end"]
    assert changes["event_status"]=={"before":"Active","after":"Dismissed"}
    assert "remains unverified" in changes["remarks"]["after"]


def test_equivalent_units_values_and_event_time_offsets_are_not_new_facts(tmp_path):
    db=Store(tmp_path/"equivalent.sqlite3")
    save(db,notice(unavailable_capacity="20.0",capacity_unit="MSm3/d"),"2026-10-20T09:01:00+00:00")
    second=save(db,notice(unavailable_capacity=20,capacity_unit="MSm3/d",event_start="2026-10-20T10:00:00+02:00",event_status="active",source_revision="b"),"2026-10-20T10:01:00+00:00")
    delta=enrich_news_revision(db,second,"2026-10-20T11:00:00+00:00")["revision_delta"]
    assert delta["changed_fields"]=={} and delta["substantive_change"] is False
