"""Precise event retrieval without changing or scoring the production archive."""
import copy
from unittest.mock import patch

from ttf_news.calendar import parse_time
from ttf_news.retrieval import features, rank_events
from ttf_news.store import Store


AS_OF="2026-10-20T12:00:00+00:00"


def event(identifier, title="Norway Troll gas supply outage", **fields):
    return {"id":identifier,"title":title,"summary":title,"state":"historical",
            "known_at":"2026-10-01T00:00:00+00:00","event_date":"2026-09-30",
            "source":"operator","url":"https://example.test/"+identifier,**fields}


def retrieve(events,query="挪威 Troll 发生天然气供应中断",**kwargs):
    return rank_events(events,query,parse_time(AS_OF),**kwargs)


def test_current_event_cannot_be_historical_analogue_and_is_counted_once():
    current=event("current",state="active",news_ids=["window-1"],event_start="2026-10-20T08:00:00+00:00",event_end="2026-10-21T08:00:00+00:00")
    previous=event("previous")
    result=retrieve([current,previous],exclude_news_ids={"window-1"})
    by_id={e["id"]:e for e in result}
    assert by_id["previous"]["retrieval_role"]=="historical_analogue"
    assert by_id["current"]["retrieval_role"]=="ongoing_background"
    assert by_id["current"]["current_window_overlap"] is True
    assert by_id["current"]["counted_in_window"] is True
    assert by_id["current"]["prior_publication_is_not_priced_in_evidence"] is True
    assert "不是已计价证据" in by_id["current"]["retrieval_note"]


def test_explicit_current_event_id_excludes_resolved_self_analogue():
    current=event("event-gassco-123",state="resolved")
    assert retrieve([current],current_event_ids={"event-gassco-123"})==[]


def test_future_operational_start_cannot_be_past_analogue_even_if_cancelled_or_inactive():
    from ttf_news.retrieval import operational_state
    for status in ("Active","Dismissed","Inactive"):
        row=event("future-"+status,source="gassco",state="resolved",event_status=status,
                  event_start="2027-08-12T04:00:00+00:00",event_end="2027-08-14T04:00:00+00:00")
        saved=copy.deepcopy(row)
        assert operational_state(row,parse_time(AS_OF))=="scheduled_future"
        assert retrieve([row])==[]
        assert row==saved


def test_bilingual_supply_relief_matches_recovery_not_new_outage():
    relief=event("restore","Norway actual gas flows restored; supply increased after plant restart")
    outage=event("loss","Norway production shutdown reduces gas supply")
    result=retrieve([outage,relief],"挪威复产，天然气供应恢复并增加，流量回升")
    assert result[0]["id"]=="restore"
    assert result[0]["retrieval_basis"]["mechanism_alignment"]=="aligned"
    assert "supply_relief" in result[0]["retrieval_basis"]["shared_mechanisms"]


def test_negated_restart_remains_supply_constraint_feature():
    f=features("Norway actual gas flows were not restored. Plant restart has not occurred.")
    assert "supply_loss" in f["mechanisms"]
    assert "supply_relief" not in f["mechanisms"]
    assert "warm_demand" not in features("European weather is not mild")["mechanisms"]


def test_nuclear_restart_is_not_gas_supply_restoration():
    text="Nuclear reactor restart in Japan will likely displace natural gas electricity generation. Japan restarted its nuclear reactor."
    f=features(text)
    assert "supply_relief" not in f["mechanisms"]
    assert "weak_demand" in f["mechanisms"]
    assert retrieve([event("nuclear",text)],"挪威复产，天然气供应恢复并增加")==[]


def test_linepack_remark_is_not_verified_gas_supply_restoration():
    row=event("linepack","Gassco Troll unplanned unavailability",summary="Technical capacity unavailable. Remarks: Restore of linepack.")
    assert "supply_relief" not in features(row["summary"])["mechanisms"]


def test_restart_hold_up_is_a_remaining_constraint_not_supply_relief():
    f=features("Restart hold-up frustrates Norway gas operator. The plant restart was delayed.")
    assert "supply_loss" in f["mechanisms"]
    assert "supply_relief" not in f["mechanisms"]
    chinese=features("挪威气厂复产推迟，供气仍受约束")
    assert "supply_loss" in chinese["mechanisms"]
    assert "supply_relief" not in chinese["mechanisms"]


def test_anticipated_restart_is_explicitly_conditional_history():
    row=event("planned-restart","Norway gas plant expects restart next week")
    result=retrieve([row],"挪威复产，供应恢复增加")
    assert result[0]["mechanism_evidence_status"]=="conditional_announcement"
    assert "不能将预计恢复当作已经核实" in result[0]["retrieval_note"]


def test_mixed_asian_export_shift_does_not_replace_restoration_analogue():
    mixed=event("mixed","Russia exports to Europe decreased while increasing exports to Asia")
    proper=event("proper","Norway gas production restarted and supply increased")
    result=retrieve([mixed,proper],"挪威复产，欧洲供气恢复并增加")
    assert result[0]["id"]=="proper"
    assert all(e["id"]!="mixed" for e in result)


def test_late_query_entities_are_not_lost_after_twenty_four_words():
    prefix="Eni opens an international partnership programme to improve long term corporate cooperation and research development across dozens of technology management investment finance logistics project areas and other activities. "
    result=retrieve([event("troll")],prefix+"Norway Troll compressor outage reduces European gas supply.")
    assert [e["id"] for e in result]==["troll"]
    assert "troll" in result[0]["retrieval_basis"]["shared_entities"]


def test_daily_legacy_conference_and_ordinary_corporate_noise_are_not_analogues():
    rows=[event("legacy",source="legacy_daily",title="Norway Troll outage and unrelated conference headlines"),
          event("conference",title="Norway gas conference examines supply disruptions"),
          event("corporate",title="Norway company announces annual corporate partnership"),
          event("precise")]
    result=retrieve(rows)
    assert [e["id"] for e in result]==["precise"]
    assert rows[0]["source"]=="legacy_daily"  # Retrieval does not mutate/archive-delete.


def test_zero_unavailable_capacity_cannot_be_ongoing_gassco_risk():
    row=event("zero","Gassco Troll: Unplanned unavailability (Active)",source="gassco",state="active",
              summary="Technical capacity: 68.0 mcm/d; available: 68.0; unavailable: 0.0.",
              event_start="2026-03-01T00:00:00+00:00",event_end="2029-01-01T00:00:00+00:00")
    assert retrieve([row])==[]


def test_announced_end_and_verified_recovery_have_distinct_states():
    ended=event("ended",source="gassco",state="resolved",event_end="2026-10-20T06:00:00+00:00",
                resolution_basis="announced_window_elapsed_unverified_flow")
    restored=event("restored",source="gassco",state="active",actual_flow_recovery_verified=True)
    result=retrieve([ended,restored])
    by_id={e["id"]:e for e in result}
    assert by_id["ended"]["retrieval_role"]=="ongoing_background"
    assert by_id["ended"]["operational_state_at_as_of"]=="announced_window_elapsed_unverified_flow"
    assert "不能据此认定仍停供或已经复产" in by_id["ended"]["retrieval_note"]
    assert by_id["restored"]["retrieval_role"]=="historical_analogue"
    assert by_id["restored"]["operational_state_at_as_of"]=="verified_flow_restored"


def test_old_expired_gassco_does_not_remain_an_ongoing_risk_forever():
    row=event("old",source="gassco",state="active",event_end="2026-10-01T06:00:00+00:00")
    result=retrieve([row])
    assert result[0]["retrieval_role"]=="historical_analogue"
    assert result[0]["operational_state_at_as_of"]=="announced_window_elapsed_unverified_flow"


def test_future_scheduled_maintenance_is_not_historical_or_current_outage():
    row=event("future",state="active",event_start="2027-05-01T06:00:00+0200",event_end="2027-05-04T06:00:00+0200")
    assert retrieve([row])==[]


def test_no_irrelevant_background_slot_is_filled():
    row=event("troll",state="active")
    assert retrieve([row],"欧洲天气温和，取暖需求下降") == []


def test_only_known_complete_outcomes_survive_and_never_change_ranking():
    row=event("with-outcomes",outcomes={
        "1d":{"available_at":"2026-10-14T00:00:00+00:00","end_date":"2026-10-13","change":-1.0},
        "3d":{"available_at":"2026-10-21T00:00:00+00:00","end_date":"2026-10-20","change":9999.0},
        "5d":{"available_at":"2026-10-19T00:00:00+00:00","end_date":"2026-10-21","change":-9999.0}},
        legacy_label="extreme_up",legacy_outcomes={"future_delta":999},label="up")
    plain=event("without-outcomes")
    result=retrieve([row,plain])
    matched=next(e for e in result if e["id"]=="with-outcomes")
    assert set(matched["outcomes"])=={"1d"}
    assert not any(k in matched for k in ["label","legacy_label","legacy_outcomes"])
    assert matched["retrieval_basis"]["price_outcomes_used_for_ranking"] is False
    changed=copy.deepcopy(row);changed["outcomes"]["1d"]["change"]=7000
    assert [(e["id"],e["retrieval_score"]) for e in result]==[(e["id"],e["retrieval_score"]) for e in retrieve([changed,plain])]
    assert set(row["outcomes"])=={"1d","3d","5d"}


def test_asof_event_versions_do_not_leak_later_restoration_or_outcome(tmp_path):
    db=Store(tmp_path/"events.sqlite3")
    old=event("revision",state="active",known_at="2026-10-14T02:00:00+00:00")
    with patch("ttf_news.store.iso",return_value="2026-10-14T02:00:00+00:00"):
        db.upsert_event(old)
    new={**old,"title":"Norway Troll actual gas flows restored","summary":"Actual gas flows have restored; supply increased", "state":"resolved",
         "outcome_available_at":"2026-10-14T04:00:00+00:00","label":"down"}
    with patch("ttf_news.store.iso",return_value="2026-10-14T04:00:00+00:00"):
        db.upsert_event(new)
    early=db.retrieve_events("Norway Troll outage","2026-10-14T11:00:00+08:00")
    assert early[0]["title"]==old["title"]
    assert "label" not in early[0]
    later=db.retrieve_events("Norway Troll supply restored","2026-10-14T05:00:00+00:00")
    assert later[0]["title"]==new["title"]
    assert later[0]["retrieval_role"]=="historical_analogue"


def test_archive_legacy_rows_are_preserved_despite_exclusion(tmp_path):
    db=Store(tmp_path/"events.sqlite3")
    row=event("legacy",source="legacy_daily",legacy_label="up",legacy_outcomes={"future_delta":900})
    with patch("ttf_news.store.iso",return_value="2026-10-01T00:00:00+00:00"):
        db.upsert_event(row)
    assert db.retrieve_events("Norway Troll outage",AS_OF)==[]
    assert db.get_event("legacy")["legacy_outcomes"]=={"future_delta":900}


def test_limit_is_respected_with_analogues_and_backgrounds():
    rows=[event("historic-"+str(i)) for i in range(10)]+[event("active-"+str(i),state="active") for i in range(5)]
    assert len(retrieve(rows,limit=3))==3
    assert sum(e["retrieval_role"]=="ongoing_background" for e in retrieve(rows,limit=3))==2
    assert retrieve(rows,limit=0)==[]
