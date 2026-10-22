"""CPU-only contracts for the shared 2.4.1 source/decision/rendering flow."""
from __future__ import annotations

import copy
import json
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path

import pytest

from ttf_news import structured_core as core
from ttf_news.evidence import source_body
from ttf_news.schemas import AnalysisResult
from ttf_news.structured_analysis import prepare_structured_context, render_saved_result


ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=2)
def _capture(role="current_live"):
    name = "manual_brief" if role == "manual" else "current_live"
    path = ROOT / "reports/repair_2026-10-20" / f"production_twelfth_{name}_actual_v2_3.json"
    return json.loads(path.read_text())["input_snapshot"]


def _prepared(role="current_live"):
    context = copy.deepcopy(_capture(role))
    prepared, _ = prepare_structured_context(context)
    return core.attach_source_documents(prepared, context)


def _catalog(prepared=None):
    prepared = _prepared() if prepared is None else prepared
    classifications = []
    for sid, card in core._ordinary(prepared).items():
        assert "Jackdaw" in card["title"]
        evidence = next(e for e in core._evidence_cards(prepared).values()
                        if e["source_id"] == sid and e["source_field"] == "title")
        classifications.append(dict(source_id=sid, fact_type="future_project_approval",
                                    evidence_id=evidence["evidence_id"], region_scope="europe",
                                    realization="future", supply_chain_role="upstream", confidence=.8))
    return core.build_catalog(prepared, {"classifications": classifications})


def _weights(catalog, values=None):
    values = values or {}
    uses = [dict(mechanism_id=m["id"], weight=values.get(m["kind"], 0), confidence=1,
                 scope=m["allowed_scopes"][0], importance_reason="low_window_materiality")
            for m in catalog.mechanisms]
    scenarios = {key: [c["id"] for c in catalog.conditions if (c["sign"] > 0 if key == "bullish"
                 else c["sign"] < 0 if key == "bearish" else c["sign"] == 0)][:1]
                 for key in ("bullish", "base", "bearish")}
    return {"uses": uses, "scenarios": scenarios, "history": []}


def _closed(catalog, weights, case="baseline_persists", drivers=()):
    weighted = core.validate_weights(weights, catalog)
    return core.validate_closing(dict(case=case, driver_ids=list(drivers), confidence=.65), weighted, catalog)


def test_frozen_mixed_context_preserves_focus_and_all_canonical_news():
    for role in ("manual", "current_live"):
        prepared = _prepared(role)
        catalog = _catalog(prepared)
        cards = core._cards(prepared)
        assert len(cards) == 4
        assert set(cards) == {f["source_id"] for f in catalog.facts}
        assert len(catalog.mechanisms) == 6
        assert "N0" in cards
        assert any("Jackdaw" in card["title"] for card in cards.values())
        # A full N0 with a reciprocal alias must survive; only pointer cards disappear.
        assert prepared["current_news"].get("same_as_window_news_id")
        if role == "current_live":
            assert "Jackdaw" in cards["N0"]["title"]


def test_exact_manual_source_projection_preserves_revision_and_original_input():
    context = copy.deepcopy(_capture("manual"))
    before = copy.deepcopy(context)
    current = context["current_news"]
    donor = next(row for row in context["window_news"] if current["title"] == row.get("title")
                 and source_body(current) == source_body(row))
    prepared, metadata = prepare_structured_context(context)
    prepared = core.attach_source_documents(prepared, context)
    assert context == before
    assert metadata["manual_source_identity"]["source_id"] == donor["id"]
    assert metadata["manual_source_identity"]["original_input_unchanged"] is True
    assert prepared["current_news"]["revision_delta"] == donor["revision_delta"]
    tightening = [m for m in _catalog(prepared).mechanisms if m["source_id"] == "N0"
                  and m["kind"] == "revision_tightening"]
    assert len(tightening) == 1
    assert float(tightening[0]["attributes"]["before"]) == 7.6
    assert float(tightening[0]["attributes"]["after"]) == 13.6


def test_manual_projection_does_not_borrow_from_other_source_body_or_future_version():
    for mutation in ("source", "body", "future"):
        context = copy.deepcopy(_capture("manual"))
        if mutation == "source":
            context["current_news"]["source"] = "different_operator"
        elif mutation == "body":
            context["current_news"]["summary"] += " A distinct source fact."
        else:
            for row in context["window_news"]:
                if row["title"] == context["current_news"]["title"]:
                    row["version_seen_at"] = "2026-10-22T10:00:00Z"
        before = copy.deepcopy(context)
        prepared, metadata = prepare_structured_context(context)
        assert "manual_source_identity" not in metadata
        assert "manual_source_identity" not in prepared
        assert context == before


def test_fixed_mechanism_signs_distinguish_supply_and_future_approval():
    for fact_type, realization, role, sign in (
        ("measured_supply_increase", "observed_as_reported", "upstream", -1),
        ("measured_supply_decrease", "observed_as_reported", "upstream", 1),
        ("future_project_approval", "future", "upstream", -1),
        ("future_import_capacity", "future", "lng_import", 0),
    ):
        p = dict(as_of="2026-10-21T10:00:00Z", session={"window_start": "2026-10-20T15:15:00Z",
                 "window_end": "2026-10-21T15:15:00Z"}, current_news=dict(
                 id="N0", title="A source-reported fact", facts="A source-reported fact", source="eia",
                 time="2026-10-21T09:00:00Z"), window_news=[], price_reference={})
        raw = dict(id="source-0", version=1, source="eia", title=p["current_news"]["title"],
                   summary=p["current_news"]["facts"], published_at=p["current_news"]["time"])
        p["source_index"] = [dict(alias="N0", id=raw["id"], source=raw["source"], version=1, title=raw["title"])]
        p = core.attach_source_documents(p, dict(current_news=raw, window_news=[]))
        evidence = next(e for e in core._evidence_cards(p).values() if e["source_field"] == "body")
        classification = dict(source_id="N0", fact_type=fact_type, evidence_id=evidence["evidence_id"],
                              region_scope="europe", realization=realization,
                              supply_chain_role=role, confidence=.7)
        catalog = core.build_catalog(p, {"classifications": [classification]})
        assert catalog.mechanisms[0]["sign"] == sign
        if realization == "future":
            assert "current_physical" not in catalog.mechanisms[0]["allowed_scopes"]


def test_related_announcements_use_max_and_active_tie_without_counting_notices():
    catalog = _catalog()
    weighted = core.validate_weights(_weights(catalog, {"announced_constraint": .8}), catalog)
    risk = next(g for g in weighted["_derived"]["group_contributions"] if g["sign"] == 1)
    assert len(risk["mechanism_ids"]) >= 3
    assert risk["value"] == .8
    assert weighted["_derived"]["news_score"] == .8
    chosen = next(m for m in catalog.mechanisms if m["id"] == risk["mechanism_id"])
    assert next(f for f in catalog.facts if f["id"] == chosen["fact_id"])["state"] == "active_notice"


def test_balanced_news_cannot_reverse_an_observed_baseline():
    catalog = _catalog()
    weights = _weights(catalog, {"announced_constraint": .8, "revision_relief": .4, "future_supply": .4})
    weighted = core.validate_weights(weights, catalog)
    assert weighted["_derived"]["news_direction"] == "flat"
    cases = core.closing_request(catalog, weighted)[2]["properties"]["case"]["enum"]
    assert not {"news_dominates", "retrace_to_flat", "reverse"} & set(cases)
    assert _closed(catalog, weights)["_derived"]["close_direction"] == "up"
    with pytest.raises(ValueError):
        _closed(catalog, weights, "reverse")


def test_missing_anchor_requires_null_even_with_directional_news():
    prepared = _prepared()
    prepared["price_reference"]["settlement_anchor"]["available"] = False
    catalog = _catalog(prepared)
    weights = _weights(catalog, {"announced_constraint": 1})
    weighted = core.validate_weights(weights, catalog)
    assert core.closing_request(catalog, weighted)[2]["properties"]["case"]["enum"] == ["insufficient_anchor"]
    checked = _closed(catalog, weights, "insufficient_anchor")
    assert checked["_derived"]["close_direction"] is None
    with pytest.raises(ValueError):
        _closed(catalog, weights, "baseline_persists")


def test_no_intraday_and_no_directional_signal_is_null_not_flat():
    prepared = _prepared()
    prepared["price_reference"]["market_baseline"]["available"] = False
    catalog = _catalog(prepared)
    weighted = core.validate_weights(_weights(catalog), catalog)
    assert core.closing_request(catalog, weighted)[2]["properties"]["case"]["enum"] == ["no_directional_signal"]
    checked = _closed(catalog, _weights(catalog), "no_directional_signal")
    assert checked["_derived"]["close_direction"] is None
    assert checked["_derived"]["evidence_status"] == "insufficient_evidence"


def test_saved_plan_english_and_detail_preserve_decisions_numbers_and_references():
    catalog = _catalog()
    checked = _closed(catalog, _weights(catalog, {"announced_constraint": .8}))
    original = core.render_plan(checked, catalog)
    original["_meta"]["structured"] = {"catalog": asdict(catalog), "plan": checked}
    frozen = copy.deepcopy(original)
    for locale, detail in (("zh", True), ("en", False), ("en", True)):
        output = render_saved_result(original, locale=locale, detail=detail)
        AnalysisResult.model_validate({k: v for k, v in output.items() if k != "_meta"})
        for section, fields in (("news_impact", ("direction", "contextual_news_score", "confidence", "signal_strength")),
                                ("close_forecast", ("direction", "confidence", "evidence_status"))):
            assert {k: output[section][k] for k in fields} == {k: original[section][k] for k in fields}
        assert output["references"] == original["references"]
        assert all(ref["url"] for ref in output["references"])
        assert output["_meta"]["structured"] == original["_meta"]["structured"]
        assert output["_meta"]["derived"] == original["_meta"]["derived"]
        assert output["_meta"]["model_calls"] == []
        assert output["_meta"]["additional_model_calls"] == 0
        if detail:
            assert output["detail"]["references"] == original["references"]
            assert len(output["detail"]["new_facts"]) == len(catalog.facts)
        if locale == "en":
            body = json.dumps({k: v for k, v in output.items() if k not in {"_meta", "references"}}, ensure_ascii=False)
            assert not any("\u4e00" <= char <= "\u9fff" for char in body)
    assert original == frozen


def test_zero_weight_keeps_focus_and_opposing_source_mechanisms_visible():
    catalog = _catalog()
    plan = _closed(catalog, _weights(catalog, {"announced_constraint": .8}))
    rendered = core.render_plan(plan, catalog, detail=True)
    assert "[N0]" in rendered["news_impact"]["summary"]
    assert any("[N0]" in evidence for evidence in rendered["analysis"]["evidence"])
    assert any("[N0]" in evidence for evidence in rendered["news_impact"]["bearish_evidence"])
    assert any("本窗口权重0" in evidence for evidence in rendered["news_impact"]["bearish_evidence"])
    assert len(rendered["detail"]["transmission"]) == len(catalog.mechanisms)


def test_weight_and_scenario_contract_requires_complete_grounded_choices():
    catalog = _catalog()
    good = _weights(catalog)
    for mutation in ("missing", "negative", "unknown_reason", "wrong_scenario"):
        bad = copy.deepcopy(good)
        if mutation == "missing":
            bad["uses"].pop()
        elif mutation == "negative":
            bad["uses"][0]["weight"] = -1
        elif mutation == "unknown_reason":
            bad["uses"][0]["importance_reason"] = "unknown_pricing"
        else:
            bad["scenarios"]["bullish"] = bad["scenarios"]["bearish"]
        with pytest.raises(ValueError):
            core.validate_weights(bad, catalog)
    schema = core.weight_request(catalog)[2]
    for field in ("bullish", "base", "bearish"):
        assert schema["properties"]["scenarios"]["properties"][field]["minItems"] == 1
