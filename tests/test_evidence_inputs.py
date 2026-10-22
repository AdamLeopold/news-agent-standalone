"""Regression cases for source facts lost in the 2026-10-20 flat audit."""

import copy
import json
import unittest
from datetime import datetime, timezone

from ttf_news.analysis import ContextError, _digest, _news_card, prepare_context


AS_OF = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)


def notice(identifier="nyhamna", *, start="2026-09-28T06:00:00+0200", end="2026-11-26T06:00:00+0100", status="Active", version=3):
    return {
        "id": identifier, "source": "gassco", "url": "https://example.test/" + identifier,
        "title": f"Gassco Nyhamna: Planned Other unavailability ({status})",
        "summary": f"Message ID: 2026-93___________________005. Publication: 2026-10-19T13:41:00+0200. Event: {start} to {end}. Technical capacity: 79.8 mcm/d; available: 60.0; unavailable: 19.8. Reason: Yearly maintenance. Remarks: .",
        "event_status": status, "event_start": start, "event_end": end,
        "published_at": "2026-10-19T11:41:00+00:00", "first_seen_at": "2026-10-19T11:42:00+00:00",
        "timestamp_precision": "exact", "version": version,
    }


def context():
    return {"as_of": AS_OF.isoformat(), "analysis_kind": "live",
            "session": {"trade_date": "2026-10-19", "window_start": "2026-10-16T15:15:00+00:00", "window_end": "2026-10-19T15:15:00+00:00"},
            "current_news": notice(), "window_news": [notice()],
            "historical_events": [], "previous_sessions": [], "price_reference": {}}


class EvidenceInputsTests(unittest.TestCase):
    def test_numeric_notice_metadata_cannot_outrank_causal_facts(self):
        text = "Message ID: 2026-999999999999999999999999999. Publication: 2026-10-19T13:41:00+0200. Event: 2026-09-28T06:00:00+0200 to 2026-11-26T06:00:00+0100. Compressor failure reduces flows to Europe. Restart is not confirmed."
        value = _digest(text, 35)
        self.assertIn("Compressor failure", value)
        self.assertIn("not confirmed", value)
        self.assertNotIn("Message ID", value)
        self.assertNotIn("Publication", value)

    def test_low_narrative_budget_keeps_facility_capacity_and_timing(self):
        card = _news_card(notice(), "N1", 8, AS_OF)
        self.assertIn("Nyhamna", card["title"])
        self.assertIn("Yearly maintenance", card["facts"])
        self.assertEqual(card["operational"]["technical_capacity"], "79.8")
        self.assertEqual(card["operational"]["available_capacity"], "60.0")
        self.assertEqual(card["operational"]["unavailable_capacity"], "19.8")
        self.assertEqual(card["operational"]["capacity_unit"], "mcm/d")
        self.assertEqual(card["operational"]["event_start_utc"], "2026-09-28T04:00:00+00:00")
        self.assertEqual(card["operational"]["event_end_utc"], "2026-11-26T05:00:00+00:00")
        self.assertTrue(card["operational"]["capacity_is_not_lost_flow"])
        self.assertNotIn("lost_flow", card["operational"])
        self.assertEqual(card["change_status"], "revised")
        self.assertIn('Planned（计划）', card['operational']['source_notice_type'])
        self.assertNotIn('direction', card)

    def test_reading_an_old_article_does_not_turn_its_publication_into_a_new_shock(self):
        value=context()
        value['current_news']={'id':'old','source':'test','title':'Earlier report',
                               'summary':'A previously reported maintenance risk continues.',
                               'published_at':'2026-10-15T12:00:00Z',
                               'first_seen_at':'2026-10-19T11:00:00Z','timestamp_precision':'exact'}
        value['window_news']=[]
        original=copy.deepcopy(value)
        data,_=prepare_context(value)
        self.assertEqual(data['current_news']['publication_role'],'earlier_window_background')
        self.assertIn('不代表今天新发生冲击',data['current_news']['publication_scope'])
        self.assertEqual(value,original)

    def test_recovery_and_negation_are_preserved_as_complete_clauses(self):
        item = {"id": "flow", "title": "Norway actual export flow update", "summary": "Actual flows to Europe increased 40 mcm/d after an early restart. The facility is not shut down. " + "Background context. " * 100}
        card = _news_card(item, "N0", 8, AS_OF)
        self.assertIn("increased 40 mcm/d after an early restart", card["facts"])
        self.assertIn("not shut down", card["facts"])
        self.assertNotIn("Background context", card["facts"])
        self.assertNotIn("direction", card)

    def test_publisher_fact_excerpt_reaches_model_with_negation_and_no_duplicate_body(self):
        value = context()
        value["current_news"] = {"id": "publisher", "title": "Norway operational update", "summary": "The operator published a facility update.",
            "fact_excerpt": "The operator published a facility update. Actual gas export flows to Europe have not recovered. A compressor outage continues to reduce supply by 15 mcm/d."}
        value["window_news"] = []
        data, _ = prepare_context(value)
        facts = data["current_news"]["facts"]
        self.assertIn("have not recovered", facts)
        self.assertIn("15 mcm/d", facts)
        self.assertLessEqual(facts.count("The operator published"), 1)

    def test_future_revision_inside_previous_day_aggregate_is_filtered(self):
        value = context()
        value["previous_sessions"] = [{"trade_date": "2026-10-16", "article_count": 2, "news_ids": ["known", "future"],
            "groups": {"供应中断/复产": [
                {"id": "known", "title": "Troll compressor failure", "summary": "Export flows to Europe declined 5 mcm/d.", "published_at": "2026-10-16T12:00:00+00:00"},
                {"id": "future", "title": "Future confirmed full restoration", "summary": "Actual flows to Europe fully recovered.", "published_at": "2026-10-21T12:00:00+00:00"}]}}]
        data, meta = prepare_context(value)
        self.assertEqual(meta["filtered_future_previous_news_count"], 1)
        self.assertEqual(data["previous_sessions"][0]["article_count"], 1)
        self.assertNotIn("fully recovered", json.dumps(data["previous_sessions"]))

    def test_future_dismissed_and_elapsed_are_distinct_from_actual_recovery(self):
        cases = [
            (notice(start="2027-05-31T06:00:00+0200", end="2027-06-01T06:00:00+0200"), "scheduled_notice"),
            (notice(status="Dismissed"), "dismissed_notice"),
            (notice(end="2026-10-19T06:00:00+0200"), "announced_end_elapsed_unverified_flow"),
        ]
        for item, expected in cases:
            with self.subTest(expected=expected):
                card = _news_card(item, "N0", 8, AS_OF)
                self.assertEqual(card["temporal_state"], expected)
                self.assertIn("待核实", card["operational"]["actual_flow_recovery"])
                self.assertNotIn("actual_flow_recovered", card)
                self.assertNotIn("direction", card)

    def test_current_duplicate_is_an_alias_not_a_second_shock(self):
        data, meta = prepare_context(context())
        self.assertEqual(data["current_news"]["same_as_window_news_id"], "N1")
        self.assertEqual(data["window_news"][0]["same_as_current_news_id"], "N0")
        self.assertNotIn("facts", data["window_news"][0])
        self.assertEqual([r["alias"] for r in meta["source_index"]], ["N0", "N1"])
        self.assertTrue(data["evidence_ledger"]["count_each_source_fact_once"])

    def test_different_revision_of_same_id_is_not_silently_aliased(self):
        value = context()
        value["window_news"][0]["version"] = 4
        value["window_news"][0]["summary"] = value["window_news"][0]["summary"].replace("available: 60.0", "available: 65.0")
        data, _ = prepare_context(value)
        self.assertNotIn("same_as_window_news_id", data["current_news"])
        self.assertIn("facts", data["window_news"][0])
        self.assertEqual(data["window_news"][0]["operational"]["available_capacity"], "65.0")

    def test_equal_notice_strings_from_distinct_sources_remain_attributable(self):
        value = context()
        value["window_news"] = [notice("one"), notice("two"), notice("three")]
        data, meta = prepare_context(value)
        self.assertEqual([n["id"] for n in data["window_news"]], ["N1", "N2", "N3"])
        for card in data["window_news"]:
            self.assertIn("Nyhamna", card["title"])
            self.assertIn("operational", card)
        self.assertEqual(meta["window_news_count"], 3)

    def test_previous_five_days_keep_entities_and_causal_facts_in_date_order(self):
        value = context()
        for day in ["2026-10-16", "2026-10-15", "2026-10-14", "2026-10-13", "2026-10-12"]:
            value["previous_sessions"].append({"trade_date": day, "article_count": 1, "news_ids": ["historic-" + day],
                "groups": {"供应中断/复产": [{"id": "historic-" + day, "title": "Gassco Troll compressor outage",
                    "summary": "Message ID: 9999999999999999999999. Publication: " + day + "T09:00:00Z. Compressor failure reduced export flows to Europe. Restart was not confirmed."}]}})
        data, meta = prepare_context(value)
        self.assertEqual([r["date"] for r in data["previous_sessions"]], sorted(r["trade_date"] for r in value["previous_sessions"]))
        for row in data["previous_sessions"]:
            self.assertIn("Troll", row["facts"])
            self.assertIn("Compressor failure", row["facts"])
            self.assertNotIn("Message ID", row["facts"])
        self.assertEqual([r["date"] for r in meta["previous_source_index"]], [r["date"] for r in data["previous_sessions"]])

    def test_known_market_baseline_survives_without_future_prices(self):
        value = context()
        baseline = {"available": True, "contract": "same-contract", "previous_date": "2026-10-16",
                    "previous_price": 88.0, "latest_price": 89.5,
                    "observed_at": "2026-10-19T11:00:00+00:00", "available_at": "2026-10-19T11:15:00+00:00",
                    "reference_move": 1.5, "observed_direction": "up", "source": "ICE delayed"}
        value["price_reference"] = {"available": True, "contract": "same-contract", "previous_price": 88.0,
                                    "price_date": "2026-10-16", "price_known_at": "2026-10-16T16:00:00+00:00",
                                    "market_baseline": baseline, "current_close": 999.0}
        data, _ = prepare_context(value)
        self.assertEqual(data["price_reference"]["market_baseline"], baseline)
        self.assertNotIn("current_close", data["price_reference"])

    def test_large_window_expands_context_without_shredding_facts(self):
        value = context()
        value["window_news"] = [{"id": f"article-{i}", "source": "eia", "title": f"European LNG cargo update {i}",
            "summary": "LNG cargo delivery increased 5%. Restart is not confirmed. " + "Background context. " * 80,
            "published_at": "2026-10-19T01:00:00+00:00"} for i in range(40)]
        data, meta = prepare_context(value)
        self.assertEqual(len(data["window_news"]), 40)
        self.assertEqual(meta["context_window"], 16384)
        self.assertEqual(meta["output_token_limit"], 3072)
        self.assertLessEqual(meta["input_token_estimate"], meta["input_token_limit"])
        self.assertLessEqual(meta["input_token_ceiling"] + meta["output_token_limit"] + 256, meta["context_window"])
        for card in data["window_news"]:
            self.assertIn("increased 5%", card["facts"])
            self.assertIn("not confirmed", card["facts"])
            self.assertIn("European LNG", card["title"])
            self.assertNotIn("category", card)

    def test_unfit_minimum_fact_ledger_fails_instead_of_losing_articles(self):
        value = context()
        value["window_news"] = [{"id": str(i), "title": "European gas flow interruption", "summary": "Actual gas flows to Europe decreased 40 mcm/d. Restart is not confirmed."} for i in range(180)]
        with self.assertRaisesRegex(ContextError, "禁止静默丢弃新闻"):
            prepare_context(value)

    def test_temporal_filters_and_input_hash_remain_deterministic(self):
        value = context()
        value["window_news"].append({"id": "future", "title": "Tomorrow flow data", "published_at": "2026-10-20T01:00:00+00:00"})
        value["historical_events"] = [{"id": "old", "title": "Historic export outage", "event_date": "2026-10-01", "label": "up", "outcome_available_at": "2026-10-21T00:00:00+00:00"}]
        first, meta1 = prepare_context(copy.deepcopy(value))
        second, meta2 = prepare_context(copy.deepcopy(value))
        self.assertEqual(first, second)
        self.assertEqual(meta1["context_sha256"], meta2["context_sha256"])
        self.assertEqual(meta1["filtered_future_news_count"], 1)
        self.assertNotIn("label", first["historical_events"][0])

    def test_old_snapshot_legacy_bundles_are_excluded_without_renumbering_sources(self):
        value = context()
        value["historical_events"] = [
            {"id": "legacy", "title": "Gas conference and unrelated daily headlines", "source": "legacy_daily", "event_date": "2022-10-20"},
            {"id": "event-nyhamna", **notice(), "news_ids": ["nyhamna"], "state": "active", "known_at": "2026-10-19T11:42:00+00:00"},
            {"id": "past", "title": "Historic Norway actual export outage", "summary": "Compressor failure reduced export flows to Europe.", "event_date": "2026-10-01", "state": "historical"},
        ]
        saved = copy.deepcopy(value)
        data, meta = prepare_context(value)
        self.assertEqual([r["id"] for r in data["historical_events"]], ["E2", "E3"])
        background = data["historical_events"][0]
        self.assertEqual(background["retrieval_role"], "ongoing_background")
        self.assertTrue(background["counted_in_window"])
        self.assertEqual(background["same_as_news_ids"],["N1"])
        self.assertIn("只计一次",background["deduplication_note"])
        for field in ("facts","operational","outcomes"):
            self.assertNotIn(field,background)
        source=next(row for row in meta["source_index"] if row["alias"]=="E2")
        self.assertEqual(source["retrieval_role"],"ongoing_background")
        self.assertTrue(source["counted_in_window"])
        self.assertEqual(meta["excluded_historical_sources"][0]["alias"], "E1")
        self.assertEqual(meta["excluded_historical_sources"][0]["reason"], "not_an_atomic_comparable_event")
        self.assertEqual(value, saved)

    def test_future_known_notice_and_zero_capacity_are_not_historical_analogues(self):
        value = context()
        future = notice("future", start="2027-05-31T06:00:00+0200", end="2027-06-01T06:00:00+0200")
        zero = notice("zero")
        zero["summary"] = zero["summary"].replace("unavailable: 19.8", "unavailable: 0")
        value["historical_events"] = [future, zero]
        data, meta = prepare_context(value)
        self.assertEqual(data["historical_events"], [])
        self.assertEqual(meta["filtered_future_event_count"], 0)
        self.assertEqual({r["reason"] for r in meta["excluded_historical_sources"]}, {"scheduled_future", "no_unavailable_capacity_reported"})

    def test_future_dismissed_and_inactive_notices_stay_in_news_but_not_analogues(self):
        value=context()
        future=[]
        for status in ("Dismissed","Inactive"):
            row=notice("future-"+status,start="2027-08-12T06:00:00+0200",end="2027-08-14T06:00:00+0200",status=status)
            future.append(row)
            value["historical_events"].append({**row,"news_ids":[row["id"]],"state":"resolved"})
        value["window_news"].extend(future)
        saved=copy.deepcopy(value)
        data,meta=prepare_context(value)
        self.assertEqual(data["historical_events"],[])
        self.assertEqual(len(data["window_news"]),3)
        self.assertEqual({row["reason"] for row in meta["excluded_historical_sources"]},{"scheduled_future"})
        self.assertEqual([row["operational"]["event_status"] for row in data["window_news"][1:]],["Dismissed","Inactive"])
        self.assertEqual(value,saved)

    def test_history_expansion_is_bounded_with_complete_source_index_and_input(self):
        value=context()
        value["historical_events"]=[{"id":"past-"+str(i),"title":"Historic Norway gas outage "+str(i),
                                     "summary":"Compressor failure reduced export flows to Europe. Restart was not confirmed.",
                                     "event_date":"2026-10-01","state":"historical","url":"https://example.test/event/"+str(i)}
                                    for i in range(8)]
        saved=copy.deepcopy(value)
        for detail,limit in ((False,3),(True,6)):
            with self.subTest(detail=detail):
                data,meta=prepare_context(value,detail=detail)
                self.assertEqual([row["id"] for row in data["historical_events"]],["E"+str(i) for i in range(1,limit+1)])
                self.assertEqual([row["alias"] for row in meta["source_index"] if row["alias"].startswith("E")],["E"+str(i) for i in range(1,9)])
                self.assertEqual(meta["historical_compression"]["unexpanded_independent_count"],8-limit)
                self.assertEqual(meta["historical_compression"]["unexpanded_event_aliases"],["E"+str(i) for i in range(limit+1,9)])
                self.assertEqual(data["current_news"]["operational"]["unavailable_capacity"],"19.8")
        self.assertEqual(value,saved)

    def test_only_historical_operational_times_drop_duplicate_utc_representation(self):
        value=context()
        historical=notice("old",start="2026-09-01T06:00:00+0200",end="2026-09-02T06:00:00+0200",status="Inactive")
        value["historical_events"]=[{**historical,"state":"historical"}]
        expected_current=_news_card(value["current_news"],"N0",300,AS_OF)
        data,_=prepare_context(value)
        operational=data["historical_events"][0]["operational"]
        self.assertEqual(operational["event_start_utc"],"2026-09-01T04:00:00+00:00")
        self.assertNotIn("event_start",operational)
        self.assertNotIn("event_end",operational)
        self.assertEqual(data["current_news"],expected_current|{"same_as_window_news_id":"N1"})


if __name__ == "__main__":
    unittest.main()
