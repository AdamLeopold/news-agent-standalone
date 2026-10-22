"""The deployed 2.4 decision remains reproducible after the source-ID upgrade."""
from dataclasses import asdict
import hashlib
from pathlib import Path

from ttf_news import structured_core_v2_4_0 as legacy
from ttf_news import structured_core_v2_4_4 as previous
from ttf_news.schemas import AnalysisResult
from ttf_news.structured_analysis import render_saved_result


def test_original_2_4_renderer_source_is_retained_byte_for_byte():
    raw = Path(legacy.__file__).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "e85e97d0825b01320b15e33419a283bd7ba1666f48401e78b13f34bbb4e883ab")


def test_previous_2_4_4_renderer_is_preserved_for_saved_decisions():
    assert hashlib.sha256(Path(previous.__file__).read_bytes()).hexdigest() == (
        "0b15b314721e86afd9d32e191deaf939b1db30b58ec8565d0c017f38c3047c06")


def test_saved_2_4_decision_keeps_labels_and_sources_in_english_and_detail():
    prepared = {
        "as_of": "2026-10-22T12:00:00Z",
        "session": {"window_start": "2026-10-21T15:15:00Z",
                    "window_end": "2026-10-22T15:15:00Z"},
        "current_news": {"id": "N0", "source": "eia", "title": "Corporate update",
                         "facts": "A corporate update.", "time": "2026-10-22T10:00:00Z"},
        "window_news": [], "price_reference": {},
        "source_index": [{"alias": "N0", "id": "original-source", "version": 1,
                          "title": "Corporate update", "url": "https://www.eia.gov/"}],
    }
    catalog = legacy.build_catalog(prepared, {"classifications": [{
        "source_id": "N0", "quote": "A corporate update.", "entity": "",
        "fact_type": "other", "region_scope": "unspecified",
        "realization": "observed_as_reported", "supply_chain_role": "corporate",
        "confidence": .7,
    }]})
    weighted = legacy.validate_weights({
        "uses": [{"mechanism_id": "M0", "weight": 0, "confidence": .7,
                  "scope": "background", "importance_reason": "low_window_materiality"}],
        "scenarios": {"bullish": [], "base": [], "bearish": []}, "history": [],
    }, catalog)
    plan = legacy.validate_closing({"case": "insufficient_anchor", "driver_ids": [],
                                    "confidence": .4}, weighted, catalog)
    original = legacy.render_plan(plan, catalog)
    original["_meta"]["structured"] = {"catalog": asdict(catalog), "plan": plan}
    for locale, detail in (("en", False), ("zh", True), ("en", True)):
        rendered = render_saved_result(original, locale=locale, detail=detail)
        AnalysisResult.model_validate({k: v for k, v in rendered.items() if k != "_meta"})
        assert rendered["news_impact"]["direction"] == original["news_impact"]["direction"]
        assert rendered["close_forecast"]["direction"] is None
        assert rendered["references"] == original["references"]
        assert rendered["_meta"]["workflow_version"] == legacy.VERSION
        assert rendered["_meta"]["additional_model_calls"] == 0
        assert rendered["_meta"]["model_calls"] == []
