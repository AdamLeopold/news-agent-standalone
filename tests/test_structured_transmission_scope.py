"""Geography is metadata; declared transmission and model importance differ."""
from ttf_news import structured_core as core


def _catalog(typ, role, region, realization="future", reported="2026-10-21T09:00:00Z"):
    title="A source-reported activity"
    raw=dict(id="source-1",version=1,source="test",title=title,summary="The source reports this activity.",published_at=reported)
    p=dict(as_of="2026-10-21T10:00:00Z",session={"window_start":"2026-10-20T15:15:00Z","window_end":"2026-10-21T15:15:00Z"},
           current_news=dict(id="N0",source="test",version=1,title=title,facts=raw["summary"],time=reported),window_news=[],price_reference={},
           source_index=[dict(alias="N0",id="source-1",version=1,source="test",title=title)])
    p=core.attach_source_documents(p,dict(current_news=raw,window_news=[]))
    eid=next(e["evidence_id"]for e in core._evidence_cards(p).values()if e["source_field"]=="body")
    return core.build_catalog(p,{"classifications":[dict(source_id="N0",evidence_id=eid,fact_type=typ,
        region_scope=region,realization=realization,supply_chain_role=role,confidence=.8)]})


def _weights(c, value, reason):
    m=c.mechanisms[0]
    return dict(uses=[dict(mechanism_id=m["id"],weight=value,confidence=.8,scope=m["allowed_scopes"][0],importance_reason=reason)],
        scenarios={key:[x["id"]for x in c.conditions if(x["sign"]>0 if key=="bullish" else x["sign"]<0 if key=="bearish" else x["sign"]==0)][:1]
                   for key in("bullish","base","bearish")},history=[])


def test_unknown_or_regional_export_can_transmit_expectations_but_weight_zero_remains_legal():
    for region in ("unspecified","region_only"):
        c=_catalog("future_project_development","lng_export",region)
        m=c.mechanisms[0]
        assert m["allowed_scopes"]==["expectation","longer_term"]
        assert m["channel"]=="global_lng_export_expectations" and m["sign"]==-1
        assert "direct_european_channel" not in core._importance_choices(c,m)
        assert core.validate_weights(_weights(c,0,"low_window_materiality"),c)["_derived"]["news_score"]==0
        assert core.validate_weights(_weights(c,.5,"future_expectation"),c)["_derived"]["news_score"]<0
        _,data,schema=core.weight_request(c)
        assert data["catalog"]["mechanisms"][0]["channel"]==m["channel"]
        assert data["catalog"]["mechanisms"][0]["weight_max"]==3
        assert "仅地域背景" not in core._fact_text(c.facts[0],"zh")
        assert "regional background only" not in core._fact_text(c.facts[0],"en")
    unknown=_catalog("measured_supply_increase","upstream","unspecified","observed_as_reported")
    assert unknown.mechanisms[0]["allowed_scopes"]==["expectation","longer_term"]
    assert unknown.mechanisms[0]["channel"]=="unresolved_geography_expectations"


def test_regional_end_use_historical_or_non_causal_activity_stays_background():
    cases=[("demand_increase","end_use","region_only","observed_as_reported","2026-10-21T09:00:00Z"),
           ("demand_increase","end_use","region_only","historical_as_reported","2026-08-21T09:00:00Z"),
           ("future_import_capacity","lng_import","region_only","future","2026-10-21T09:00:00Z"),
           ("corporate_contract","lng_export","unspecified","observed_as_reported","2026-10-21T09:00:00Z"),
           ("technology_test","technology","europe","observed_as_reported","2026-10-21T09:00:00Z"),
           ("measured_supply_increase","lng_export","unspecified","historical_as_reported","2026-08-21T09:00:00Z")]
    for case in cases:
        c=_catalog(*case);m=c.mechanisms[0]
        assert m["allowed_scopes"]==["background"]
        assert "direct_european_channel" not in core._importance_choices(c,m)
        assert core.validate_weights(_weights(c,0,"low_window_materiality"),c)["_derived"]["news_score"]==0
        assert core.weight_request(c)[1]["catalog"]["mechanisms"][0]["weight_max"]==0


def test_uncertain_future_supply_role_does_not_determine_materiality_or_claim_european_flow():
    for region in ("region_only", "unspecified"):
        c = _catalog("future_project_development", "upstream", region)
        m = c.mechanisms[0]
        assert m["allowed_scopes"] == ["expectation", "longer_term"]
        assert m["channel"] == "conditional_regional_future_supply_expectations"
        assert "direct_european_channel" not in core._importance_choices(c, m)
        assert "current_physical" not in m["allowed_scopes"]
        assert core.validate_weights(_weights(c, 0, "low_window_materiality"), c)["_derived"]["news_score"] == 0
        assert core.validate_weights(_weights(c, .5, "weak_indirect_link"), c)["_derived"]["news_score"] < 0
        assert c.conditions[0]["kind"] == "regional_link_required"
        assert "requires assessment" in core._fact_text(c.facts[0], "en")
        assert m["channel"] in core.weight_request(c)[0]
    regional_observed = _catalog("measured_supply_increase", "upstream", "region_only", "observed_as_reported")
    assert regional_observed.mechanisms[0]["allowed_scopes"] == ["background"]
