"""Targeted CPU controls; every classification below is a constructed test."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from ttf_news.structured_analysis import prepare_structured_context
from ttf_news.schemas import AnalysisResult
from ttf_news.evidence import source_body
from ttf_news import structured_core as core

ROOT = Path(__file__).resolve().parents[1]


def small(body="The field has resumed gas production.\n  U.S. prices fell 1.5%; demand rose.\n", title="An engineering contract"):
    raw = dict(id="article-1", version=2, source="test", title=title, summary=body,
               published_at="2026-10-21T09:00:00Z", first_seen_at="2026-10-21T09:01:00Z")
    other = dict(raw, id="article-2", title="Another source", summary="A distinct source paragraph.")
    context = dict(current_news=raw, window_news=[other])
    cards = [dict(id=sid, title=r["title"], source=r["source"], version=r["version"],
                  time=r["published_at"], facts="A compressed prepared excerpt") for sid, r in (("N0", raw), ("N1", other))]
    prepared = dict(as_of="2026-10-21T10:00:00Z", session={"window_start":"2026-10-20T15:15:00Z", "window_end":"2026-10-21T15:15:00Z"},
                    current_news=cards[0], window_news=[cards[1]], historical_events=[], price_reference={},
                    source_index=[dict(alias=c["id"], id=r["id"], version=r["version"], source=r["source"],
                                       title=r["title"], url="https://example.test/"+r["id"]) for c, r in zip(cards, (raw, other))])
    return prepared, context


def choice(prepared, sid="N0", typ="measured_supply_increase", field="body", realization="observed_as_reported", region="europe", role="upstream"):
    evidence = next(e for e in core._evidence_cards(prepared).values() if e["source_id"] == sid and e["source_field"] == field)
    return dict(source_id=sid, evidence_id=evidence["evidence_id"], fact_type=typ, region_scope=region,
                realization=realization, supply_chain_role=role, confidence=.8)


def catalog(prepared, first=None):
    rows = [first or choice(prepared), choice(prepared, "N1", "other", role="other")]
    return core.build_catalog(prepared, {"classifications": rows})


def test_complete_title_and_paragraph_bytes_not_compressed_facts():
    prepared, context = small()
    before = copy.deepcopy((prepared, context))
    p = core.attach_source_documents(prepared, context)
    assert (prepared, context) == before
    cards = list(core._evidence_cards(p).values())
    n0 = [e for e in cards if e["source_id"] == "N0"]
    assert [e["text"] for e in n0] == [context["current_news"]["title"], *source_body(context["current_news"]).splitlines(keepends=True)]
    assert all(e["text_sha256"] == hashlib.sha256(e["text"].encode()).hexdigest() for e in cards)
    assert "U.S. prices fell 1.5%; demand rose." in n0[2]["text"]


def test_frozen_p12_keeps_full_n0_and_all_canonical_aliases():
    for name in ("manual_brief", "current_live"):
        context = json.loads((ROOT / f"reports/repair_2026-10-20/production_twelfth_{name}_actual_v2_3.json").read_text())["input_snapshot"]
        prepared, _ = prepare_structured_context(context)
        p = core.attach_source_documents(prepared, context)
        sid = next(iter(core._ordinary(p)))
        classified = choice(p, sid, "future_project_approval", field="title", realization="future")
        c = core.build_catalog(p, {"classifications":[classified]})
        assert len(core._cards(p)) == 4 and len(c.facts) == 4 and len(c.mechanisms) == 6
        assert "N0" in core._cards(p)
        assert {f["source_id"] for f in c.facts} == set(core._cards(p))


def test_model_contract_contains_only_evidence_choice_not_quote_or_entity():
    prepared, context = small()
    p = core.attach_source_documents(prepared, context)
    requests = core.classification_requests(p)
    assert len(requests) == 2
    system, data, schema = requests[0]["request"]
    fields = schema["properties"]["classifications"]["items"]["oneOf"][0]["properties"]
    assert "evidence_id" in fields and "quote" not in fields and "entity" not in fields
    expected = {e["evidence_id"] for e in core._evidence_cards(p).values() if e["source_id"] == requests[0]["source_id"]}
    assert set(fields["evidence_id"]["enum"]) == expected
    assert len(data["evidence"]) == len(expected)
    assert "Read both title and body" in system


def test_catalog_restores_exact_quote_and_immutable_source_provenance():
    prepared, context = small()
    context["current_news"]["entities"] = ["Original source entity"]
    p = core.attach_source_documents(prepared, context)
    c = catalog(p)
    fact = c.facts[0]
    evidence = core._evidence_cards(p)[fact["evidence_id"]]
    assert fact["quote"] == evidence["text"]
    assert fact["original_source_id"] == "article-1" and fact["source_version"] == 2
    assert fact["entity"] == "Original source entity"
    assert c.mechanisms[0]["group"] == "article-1:measured_supply_increase"
    assert fact["semantic_entailment_programmatically_verified"] is False


def test_wrong_source_unknown_id_and_duplicate_classification_rejected():
    prepared, context = small()
    p = core.attach_source_documents(prepared, context)
    good = choice(p)
    for kind in ("foreign", "missing", "rewritten"):
        bad = copy.deepcopy(good)
        if kind == "foreign":
            bad["evidence_id"] = choice(p, "N1", "other", role="other")["evidence_id"]
        elif kind == "missing":
            bad["evidence_id"] = "fabricated-evidence"
        else:
            bad["quote"] = "A model-rewritten quotation"
        with pytest.raises(ValueError):
            catalog(p, bad)
    with pytest.raises(ValueError):
        core.build_catalog(p, {"classifications":[good, good, choice(p,"N1","other",role="other")]})


def test_missing_ambiguous_future_or_wrong_version_documents_never_guessed():
    prepared, context = small()
    for kind in ("missing_index", "wrong_version", "future", "ambiguous"):
        p, c = copy.deepcopy(prepared), copy.deepcopy(context)
        if kind == "missing_index":
            p["source_index"] = []
        elif kind == "wrong_version":
            p["source_index"][0]["version"] = 99
        elif kind == "future":
            c["current_news"]["version_seen_at"] = "2026-10-22T10:00:00Z"
        else:
            c["window_news"].append(dict(c["current_news"], summary="Conflicting text under the same version"))
        with pytest.raises(ValueError):
            core.attach_source_documents(p, c)


def test_tampered_body_or_identity_rejected_and_record_order_does_not_bind():
    prepared, context = small()
    p = core.attach_source_documents(prepared, context)
    reordered = copy.deepcopy(context)
    reordered["window_news"] = [reordered["current_news"], *reordered["window_news"]]
    assert core.attach_source_documents(prepared, reordered)["source_documents"] == p["source_documents"]
    for key in ("body", "source_identity_sha256"):
        bad = copy.deepcopy(p)
        bad["source_documents"]["N0"][key] += "changed"
        with pytest.raises(ValueError):
            core.classification_requests(bad)


def test_import_facility_alone_is_background_not_incremental_lng_demand():
    prepared, context = small("A survey is planned for a proposed Alaska LNG import terminal.")
    p = core.attach_source_documents(prepared, context)
    c = catalog(p, choice(p, typ="future_import_capacity", realization="future", role="lng_import", region="region_only"))
    assert c.mechanisms[0]["kind"] == "background" and c.mechanisms[0]["sign"] == 0
    assert c.mechanisms[0]["allowed_scopes"] == ["background"]


def test_european_receiving_relief_and_explicit_procurement_are_distinct_signs():
    for typ, text, region, sign in (
        ("future_european_receiving_capacity", "The planned European FSRU will relieve a receiving bottleneck.", "europe", -1),
        ("future_additional_lng_consumption", "The planned industrial expansion will add final gas consumption and compete for additional LNG.", "global_lng", 1),
    ):
        prepared, context = small(text)
        p = core.attach_source_documents(prepared, context)
        c = catalog(p, choice(p, typ=typ, realization="conditional", role="lng_import", region=region))
        assert c.mechanisms[0]["sign"] == sign
        assert "current_physical" not in c.mechanisms[0]["allowed_scopes"]
        if typ == "future_european_receiving_capacity":
            bad = choice(p, typ=typ, realization="future", role="lng_import", region="region_only")
            with pytest.raises(ValueError):
                catalog(p, bad)


def test_new_contract_still_renders_shared_decisions_and_original_quote_metadata():
    prepared, context = small()
    prepared["price_reference"] = {"settlement_anchor":{"available":False}}
    p = core.attach_source_documents(prepared, context)
    c = catalog(p)
    uses = [dict(mechanism_id=m["id"],weight=.5 if m["sign"] else 0,confidence=.8,scope=m["allowed_scopes"][0],importance_reason="low_window_materiality") for m in c.mechanisms]
    scenarios = {key:[x["id"] for x in c.conditions if (x["sign"]>0 if key=="bullish" else x["sign"]<0 if key=="bearish" else x["sign"]==0)][:1] for key in ("bullish","base","bearish")}
    w = core.validate_weights(dict(uses=uses,scenarios=scenarios,history=[]), c)
    plan = core.validate_closing(dict(case="insufficient_anchor",driver_ids=[],confidence=.6), w, c)
    outputs = [core.render_plan(plan,c,locale,True) for locale in ("zh","en")]
    for out in outputs:
        AnalysisResult.model_validate({k:v for k,v in out.items() if k!="_meta"})
        assert out["_meta"]["source_quotes"][0]["quote"] == c.facts[0]["quote"]
        assert out["_meta"]["source_quotes"][0]["evidence_id"] == c.facts[0]["evidence_id"]
    assert outputs[0]["_meta"]["derived"] == outputs[1]["_meta"]["derived"]
    assert outputs[0]["references"] == outputs[1]["references"]


def test_semantic_model_inputs_exclude_legacy_inference_fields():
    prepared, context = small()
    prepared["current_news"].update(mechanism_candidates=["LEGACY_INFERENCE"], evidence_scope="LEGACY_INFERENCE",
                                    source_reported_facts={"claim":"LEGACY_INFERENCE"})
    p = core.attach_source_documents(prepared, context)
    system, data, _ = core.classification_requests(p)[0]["request"]
    assert "LEGACY_INFERENCE" not in json.dumps(data)
    assert "compressed prepared excerpt" not in json.dumps(data).lower()
    assert "Technology validation" in system
    assert "European company participating does not establish" in system
    c = catalog(p)
    _, weight_data, _ = core.weight_request(c)
    assert "LEGACY_INFERENCE" not in json.dumps(weight_data)
    assert "mechanism_candidates" not in json.dumps(weight_data)
    assert "source_documents" not in weight_data and "news" not in weight_data
    assert c.versions["prepared"]["current_news"]["evidence_scope"] == "LEGACY_INFERENCE"


def test_long_history_title_stays_in_reference_and_detail_not_brief_line():
    prepared, context = small()
    prepared["price_reference"] = {"settlement_anchor":{"available":False}}
    title = "Complete historical source title " * 30
    prepared["historical_events"] = [dict(id="E1",title=title,date="2026-10-19",status="notice_dismissed",
        outcomes={"1d":dict(change=-2.9780000000000086,price_type="reference",contract="Nov26",end_date="2026-10-20",available_at="2026-10-20T23:27:37Z")})]
    prepared["source_index"].append(dict(alias="E1",id="historic-1",url="https://example.test/history",title=title))
    p = core.attach_source_documents(prepared, context)
    c = catalog(p)
    uses = [dict(mechanism_id=m["id"],weight=.5 if m["sign"] else 0,confidence=.8,scope=m["allowed_scopes"][0],importance_reason="low_window_materiality") for m in c.mechanisms]
    scenarios = {key:[x["id"] for x in c.conditions if (x["sign"]>0 if key=="bullish" else x["sign"]<0 if key=="bearish" else x["sign"]==0)][:1] for key in ("bullish","base","bearish")}
    w = core.validate_weights(dict(uses=uses,scenarios=scenarios,history=[dict(event_id="E1",relation="contrast")]),c)
    plan = core.validate_closing(dict(case="insufficient_anchor",driver_ids=[],confidence=.6),w,c)
    for locale in ("zh","en"):
        out = core.render_plan(plan,c,locale,True)
        AnalysisResult.model_validate({k:v for k,v in out.items() if k!="_meta"})
        line = out["analysis"]["historical_comparisons"][0]
        assert len(line) <= 450 and title not in line
        assert "-2.9780000000000086" in line and "2026-10-20T23:27:37Z" in line
        assert title in out["detail"]["historical_comparisons"][0]
        assert next(r for r in out["references"] if r["id"]=="E1")["title"] == title


def test_per_source_requests_validate_independently_then_merge_without_source_loss():
    prepared, context = small()
    p = core.attach_source_documents(prepared, context)
    merged = []
    for request in core.classification_requests(p):
        sid = request["source_id"]
        _, data, schema = request["request"]
        assert set(data["sources"]) == {sid}
        assert {e["source_id"] for e in data["evidence"]} == {sid}
        assert all(len(e["evidence_id"]) < 20 for e in data["evidence"])
        assert all(set(e) == {"evidence_id","source_id","source_field","ordinal","text"} for e in data["evidence"])
        assert schema["properties"]["classifications"]["maxItems"] == 3
        value = choice(p,sid,"measured_supply_increase" if sid=="N0" else "other",role="upstream" if sid=="N0" else "other")
        checked = core.build_catalog(request["validation_context"], {"classifications":[value]})
        assert set(core._ordinary(request["validation_context"])) == {sid}
        assert len(checked.facts) == 1
        merged.append(value)
    assert {f["source_id"] for f in core.build_catalog(p,{"classifications":merged}).facts} == {"N0","N1"}
    with pytest.raises(ValueError):
        core.build_catalog(p,{"classifications":merged[:1]})


def test_native_union_and_validator_share_the_same_type_state_role_contract():
    prepared, context = small("A long-term LNG sale and purchase agreement was signed.")
    p = core.attach_source_documents(prepared,context)
    request = core.classification_requests(p)[0]
    branches = request["request"][2]["properties"]["classifications"]["items"]["oneOf"]
    assert len(branches) == len(core.TYPE_CONTRACTS)
    for branch in branches:
        assert branch["type"] == "object" and branch["additionalProperties"] is False
        props = branch["properties"];typ = props["fact_type"]["const"]
        real,roles,regions = core.TYPE_CONTRACTS[typ]
        assert props["realization"]["enum"] == list(real)
        assert props["supply_chain_role"]["enum"] == list(roles)
        assert props["region_scope"]["enum"] == list(regions)
    contract = choice(p,typ="corporate_contract",realization="observed_as_reported",role="lng_export",region="global_lng")
    assert core.build_catalog(request["validation_context"],{"classifications":[contract]}).mechanisms[0]["sign"] == 0
    wrong = dict(contract,fact_type="future_additional_lng_consumption",realization="future")
    matching = [b for b in branches if b["properties"]["fact_type"]["const"] == wrong["fact_type"]
                and wrong["supply_chain_role"] in b["properties"]["supply_chain_role"]["enum"]]
    assert matching == []
    with pytest.raises(ValueError):
        core.build_catalog(request["validation_context"],{"classifications":[wrong]})
    assert "future_lng_procurement_demand" not in core.TYPES
