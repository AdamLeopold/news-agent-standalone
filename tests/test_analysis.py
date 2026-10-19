import copy
import json
from pathlib import Path
import sqlite3
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from ttf_news.analysis import (
    AnalysisError,
    ContextError,
    NewsAnalyst,
    _parse_result,
    count_tokens,
    prepare_context,
    response_schema,
)
from ttf_news.prompts import DETAIL_SHAPE, SHORT_SHAPE
from ttf_news.schemas import AnalysisResult


def context():
    return {
        "as_of": "2026-10-14T03:00:00Z",
        "analysis_kind": "manual",
        "session": {"session_date": "2026-10-14", "close_at": "2026-10-14T15:15:00Z"},
        "current_news": {
            "id": "manual-1",
            "title": "Norway gas outage",
            "body": "Supply disruption reduces European pipeline flows by 20 mcm/day. Restart is not confirmed.",
            "url": "https://example.test/outage",
            "published_at": "2026-10-14T02:00:00Z",
        },
        "window_news": [],
        "historical_events": [],
        "previous_sessions": [],
        "price_reference": {},
    }


def result(direction="up"):
    value = copy.deepcopy(SHORT_SHAPE)
    value["news_impact"]["direction"] = direction
    value["news_impact"]["contextual_news_score"] = 1.0 if "up" in direction else -1.0 if "down" in direction else 0.0
    value["news_impact"]["summary"] = "供应中断构成潜在看涨压力，净影响仍取决于需求和复产进度"
    value["close_forecast"].update(summary="当前仅凭新闻证据形成条件性结算预判", basis="前一交易日价与波动率缺失，需核对行情")
    value["analysis"]["summary"] = "供应中断消息及其欧洲传导需要进一步核实"
    value["analysis"]["priced_in_comment"] = "未提供公告前后行情，无法确认已计价程度"
    value["news_impact"]["bullish_evidence"] = ["[N0]供应下降 → TTF供应风险增加"]
    value["news_impact"]["bearish_evidence"] = []
    value["news_impact"]["neutralizing_evidence"] = []
    value["analysis"]["evidence"] = ["[N0]欧洲管道供应减少 → TTF偏看涨"]
    value["references"] = [{"id": "N0", "url": "", "title": "ignore model title"}]
    return value


def detailed_result():
    value = result()
    value["detail"] = {
        "new_facts": ["[N0]欧洲管道流量减少20mcm/day，重启未确认"],
        "transmission": ["[N0]欧洲供给下降 → TTF风险溢价可能增加"],
        "daily_balance": ["window_news未提供其他当日新闻，无法确认全天净影响"],
        "historical_comparisons": ["historical_events未提供相似事件，历史类比不足"],
        "priced_in_assessment": "price_reference未提供实时价格，无法确认已计价程度",
        "scenarios": {"bullish": "若[N0]中断扩大或持续 → TTF上涨压力增强", "base": "若[N0]中断维持且需求平稳 → 供应风险持续", "bearish": "若[N0]设施复产、供给恢复 → TTF上涨压力减弱"},
        "invalidation_conditions": ["[N0]重启得到确认后中断风险结论需重新评估"],
        "limitations": ["price_reference为空，不能核对官方日结算异常阈值"],
        "references": [{"id": "N0", "url": "", "title": "input news"}],
    }
    return value


class FakeAnalyst(NewsAnalyst):
    def __init__(self, outputs):
        super().__init__()
        self.outputs = list(outputs)
        self.calls = []

    async def _request(self, system, data, detail):
        self.calls.append((system, data, detail))
        response = self.outputs.pop(0)
        if isinstance(response, dict) and "message" in response:
            return response
        return {"message": {"content": response}, "prompt_eval_count": 1200, "eval_count": 550}


class SchemaTests(unittest.TestCase):
    def test_conditional_invalidation_can_resolve_current_facts_but_not_new_conflict(self):
        value = context()
        value["current_news"]["body"] += " LNG cargo arrivals increased."
        _, meta = prepare_context(value, True)
        output = detailed_result()
        output["detail"]["invalidation_conditions"] = ["若停产持续时间延长或LNG到港增加被削弱，可能导致价格上涨"]
        output["detail"]["limitations"] = ["缺乏实时供应量与LNG到港量数据，无法量化影响"]
        parsed = _parse_result(json.dumps(output), True, meta["source_index"], context=value)
        self.assertTrue(parsed["detail"]["invalidation_conditions"][0].startswith("[N0]"))
        output["detail"]["invalidation_conditions"] = ["若战争引发制裁，价格可能上涨"]
        with self.assertRaisesRegex(ValueError, r"invalidation_conditions\[0\]"):
            _parse_result(json.dumps(output), True, meta["source_index"], context=value)

    def test_missing_data_explanation_is_checked_against_supplied_context(self):
        value = context()
        _, meta = prepare_context(value, True)
        output = detailed_result()
        output["detail"]["daily_balance"] = ["本结算窗口暂无其他新闻，无法判断累计冲击"]
        _parse_result(json.dumps(output), True, meta["source_index"], context=value)
        value["window_news"] = [{"id":"known", "title":"Gas supply update"}]
        with self.assertRaisesRegex(ValueError, r"daily_balance\[0\]"):
            _parse_result(json.dumps(output), True, meta["source_index"], context=value)

    def test_structured_decoder_requires_detail_and_nested_references(self):
        schema = response_schema(True)
        self.assertIn("detail", schema["required"])
        self.assertEqual(schema["properties"]["detail"], {"$ref": "#/$defs/DetailAnalysis"})
        self.assertIn("references", schema["$defs"]["DetailAnalysis"]["required"])
        self.assertNotIn("detail", response_schema(False)["properties"])

    def test_all_five_directions_are_valid(self):
        for direction in ("extreme_up", "up", "flat", "down", "extreme_down"):
            with self.subTest(direction=direction):
                parsed = AnalysisResult.model_validate(result(direction))
                self.assertEqual(parsed.news_impact.direction, direction)

    def test_null_and_out_of_range_fail_strict_validation(self):
        for path, invalid in (("confidence", None), ("confidence", 1.1), ("contextual_news_score", "1"), ("contextual_news_score", True)):
            value = result()
            value["news_impact"][path] = invalid
            with self.subTest(path=path, invalid=invalid), self.assertRaises(ValueError):
                AnalysisResult.model_validate(value)
        value = result()
        value["analysis"]["negation_or_reversal"] = None
        with self.assertRaises(ValueError):
            AnalysisResult.model_validate(value)

    def test_no_up_when_score_is_negative(self):
        value = result()
        value["news_impact"]["contextual_news_score"] = -1.0
        with self.assertRaises(ValueError):
            AnalysisResult.model_validate(value)

    def test_citations_resolve_only_from_input(self):
        index = [{"alias": "N0", "id": "manual-1", "url": "https://example.test/outage", "title": "Norway gas outage"}]
        parsed = _parse_result(json.dumps(result()), False, index)
        self.assertEqual(parsed["references"][0], {"id": "manual-1", "url": "https://example.test/outage", "title": "Norway gas outage"})
        value = result()
        value["references"][0]["id"] = "N404"
        with self.assertRaises(ValueError):
            _parse_result(json.dumps(value), False, index)

    def test_bare_evidence_ids_are_rejected(self):
        index = [{"alias": "N0", "id": "manual-1", "url": "", "title": "Norway gas outage"}]
        value = result()
        value["analysis"]["evidence"] = ["N0"]
        with self.assertRaisesRegex(ValueError, "事实与TTF机制"):
            _parse_result(json.dumps(value), False, index)

    def test_empty_detail_and_placeholder_analysis_are_rejected(self):
        index = [{"alias": "N0", "id": "manual-1", "url": "", "title": "Norway gas outage"}]
        value = detailed_result()
        value["detail"]["new_facts"] = []
        with self.assertRaises(ValueError):
            _parse_result(json.dumps(value), True, index)
        value = detailed_result()
        value["detail"]["priced_in_assessment"] = "区分事实、推断及缺失"
        with self.assertRaisesRegex(ValueError, "占位句"):
            _parse_result(json.dumps(value), True, index)
        value = detailed_result()
        value["detail"]["scenarios"]["bearish"] = "若欧洲出现极端天气导致需求上升"
        with self.assertRaisesRegex(ValueError, "下跌机制"):
            _parse_result(json.dumps(value), True, index)

    def test_english_analysis_is_rejected_but_english_names_are_allowed(self):
        index = [{"alias": "N0", "id": "manual-1", "url": "", "title": "Norway gas outage"}]
        value = result()
        value["news_impact"]["summary"] = "The unexpected outage should reduce European gas supply and support TTF prices."
        with self.assertRaisesRegex(ValueError, "必须为中文"):
            _parse_result(json.dumps(value), False, index)
        value = result()
        value["news_impact"]["summary"] = "Equinor的Norway设施发生停产，可能削减欧洲可用气量并支撑TTF价格"
        self.assertIn("Norway", _parse_result(json.dumps(value), False, index)["news_impact"]["summary"])
        value = result()
        value["references"][0]["url"] = "https://invented.test/price"
        with self.assertRaises(ValueError):
            _parse_result(json.dumps(value), False, index)


class ContextTests(unittest.TestCase):
    def test_date_only_publication_is_compared_in_source_local_calendar(self):
        value = context()
        value["as_of"] = "2026-10-13T22:30:00Z"  # Brussels 14 Oct; New York 13 Oct.
        for source, day, admitted in (("eu_energy","2026-10-14",True),("eia","2026-10-14",False),("eia","2026-10-13",True)):
            with self.subTest(source=source, day=day):
                value["current_news"].update(source=source, published_at=None, published_date=day, timestamp_precision="date", first_seen_at="2026-10-13T22:25:00Z")
                if admitted:
                    data, _ = prepare_context(value)
                    self.assertEqual(data["current_news"]["time"], day)
                    self.assertEqual(data["current_news"]["time_precision"], "date")
                else:
                    with self.assertRaises(ContextError):
                        prepare_context(value)

    def test_rich_known_outcomes_detail_reserves_total_context_budget(self):
        value = context()
        value["historical_events"] = [{"id":f"history-{i}","title":"Norway historic supply disruption","event_date":"2026-09-01",
            "outcomes":{h:{"change":1.2,"price_type":"daily_reference_unverified","contract":"Dutch TTF gas October 2026","end_date":"2026-09-10","available_at":"2026-10-13T23:00:00Z","raw_observations":"full archive "*90} for h in ("1d","3d","5d","10d")}}
            for i in range(6)]
        data, meta = prepare_context(value, True)
        for event in data["historical_events"]:
            self.assertEqual(list(event["outcomes"]), ["1d","3d"])
            self.assertNotIn("raw_observations", event["outcomes"]["1d"])
        self.assertLessEqual(meta["input_token_ceiling"]+meta["output_token_limit"],8192)
        self.assertIn("完整1/3/5/10d",data["compression"]["method"])

    def test_expired_notice_time_is_derived_with_timezone_without_claiming_recovery(self):
        value = context()
        value["as_of"] = "2026-10-14T04:06:00Z"
        value["current_news"].update(source="gassco", event_end="2026-10-14T06:00+0200")
        data, _ = prepare_context(value)
        self.assertTrue(data["current_news"]["operational"]["announced_end_passed"])
        self.assertIn("待核实", data["current_news"]["operational"]["actual_flow_recovery"])

    def test_precise_asof_required(self):
        value = context()
        value["as_of"] = "2026-10-14T03:00:00"
        with self.assertRaises(ContextError):
            prepare_context(value)

    def test_future_current_news_is_rejected(self):
        value = context()
        value["current_news"]["published_at"] = "2026-10-15T00:00:00Z"
        with self.assertRaises(ContextError):
            prepare_context(value)

    def test_future_event_and_unavailable_outcome_are_filtered(self):
        value = context()
        value["historical_events"] = [
            {"id": "old", "title": "Old Norway outage", "start_date": "2026-10-01", "label": "extreme_up", "outcome_available_at": None},
            {"id": "known", "title": "LNG restart", "start_date": "2026-10-02", "label": "down", "outcome_available_at": "2026-10-03T17:00:00Z"},
            {"id": "later", "title": "Future outage", "start_date": "2026-10-15", "label": "extreme_down"},
        ]
        value["window_news"] = [{"id": "late", "title": "Late discovery", "published_at": "2026-10-13T00:00:00Z", "first_seen_at": "2026-10-15T00:00:00Z"}]
        value["price_reference"] = {"observations": [
            {"date": "2026-10-13", "value": 30.0},
            {"date": "2026-10-14", "value": 45.0},
            {"date": "2026-10-15", "value": 99.0},
            {"value": 999.0},
        ], "target_close": 90.0}
        data, meta = prepare_context(value)
        self.assertEqual(meta["filtered_future_event_count"], 1)
        self.assertEqual(meta["filtered_future_news_count"], 1)
        self.assertNotIn("label", data["historical_events"][0])
        self.assertEqual(data["historical_events"][1]["label"], "down")
        self.assertEqual(data["price_reference"]["observations"], [{"date": "2026-10-13", "value": 30.0}])
        self.assertNotIn("target_close", data["price_reference"])

    def test_compression_keeps_all_news_ids_and_current_negation(self):
        value = context()
        value["window_news"] = [
            {"id": f"id-{index}", "title": "LNG supply update", "summary": "LNG cargo delivery increased 5%. Restart is not confirmed. " + "Background context. " * 80, "published_at": "2026-10-14T01:00:00Z"}
            for index in range(40)
        ]
        data, meta = prepare_context(value)
        self.assertEqual(len(meta["source_index"]), 41)
        self.assertEqual(meta["window_news_count"], 40)
        self.assertTrue(data["compression"]["every_news_id_retained"])
        wire = json.dumps(data)
        for index in range(1, 41):
            self.assertIn(f'"N{index}"', wire)
        self.assertIn("not confirmed", data["current_news"]["facts"])
        self.assertLessEqual(meta["input_token_estimate"], meta["input_token_ceiling"])

    def test_minimum_evidence_index_overflow_is_explicit(self):
        value = context()
        value["window_news"] = [{"id": f"row-{index}", "title": "gas"} for index in range(4000)]
        with self.assertRaisesRegex(ContextError, "禁止静默丢弃新闻"):
            prepare_context(value)

    def test_current_manual_facts_survive_heavy_background_compression(self):
        value = context()
        text = "测试情景：挪威天然气处理设施发生意外停产，运营方预计持续三天；与此同时欧洲天气温和，LNG到港增加。请判断供应中断与需求缓解对TTF价格的净影响，不把这个手动情景视为已核实新闻。"
        value["current_news"].update(title=text, body=text)
        value["historical_events"] = [{"id": f"event-{i}", "title": "Historic LNG supply headlines " * 80, "summary": "Natural gas supply increased 10%. Weather was mild. " * 80, "event_date": "2022-10-10", "source_quality": "daily_aggregate_no_original_url"} for i in range(6)]
        value["coverage"] = {"sources": [{"id": f"source-{i}", "name": "Long source name", "status": "ok", "last_success_at": "2026-10-14T01:00:00Z"} for i in range(7)]}
        data, meta = prepare_context(value)
        self.assertIn("意外停产", data["current_news"]["facts"])
        self.assertIn("持续三天", data["current_news"]["facts"])
        self.assertIn("天气温和", data["current_news"]["facts"])
        self.assertIn("LNG到港增加", data["current_news"]["facts"])
        self.assertLessEqual(meta["input_token_estimate"], meta["input_token_ceiling"])

    def test_outcomes_require_per_horizon_availability_and_completed_end_date(self):
        value = context()
        value["historical_events"] = [{"id": "outage", "title": "Historic gas outage", "event_date": "2026-10-01", "outcome_available_at": "2026-10-14T01:00:00Z", "outcomes": {
            "1d": {"end_date": "2026-10-02", "available_at": "2026-10-03T00:00:00Z", "change": 2.0},
            "3d": {"end_date": "2026-10-15", "available_at": "2026-10-14T01:00:00Z", "change": 99.0},
            "5d": {"end_date": "2026-10-08", "available_at": "2026-10-15T00:00:00Z", "change": 45.0},
        }}]
        data, _ = prepare_context(value)
        self.assertEqual(list(data["historical_events"][0]["outcomes"]), ["1d"])


class InferenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_notice_capacity_and_previous_window_facts_are_grounded(self):
        value=context();value["as_of"]="2026-10-14T05:31:00Z"
        value["session"]={"trade_date":"2026-10-14","window_start":"2026-10-13T15:15:00Z","window_end":"2026-10-14T15:15:00Z"}
        value["current_news"].update(source="gassco",title="Kårstø: Unplanned Gas treatment plant unavailability",published_at="2026-10-13T11:20:16Z",event_end="2026-10-14T06:00+0200",body="Technical capacity: 97.6 mcm/d; available: 87.0; unavailable: 10.6.")
        value["historical_events"]=[{"id":"old","event_date":"2026-10-01","title":"Old supply constraint"}]
        raw=result("up")
        raw["news_impact"]["summary"]="Kårstø可用容量下降约10.8 mcm/d，可能形成供应压力"
        raw["analysis"]["summary"]="Kårstø是本窗口唯一新增供应冲击，缺口约10.8 mcm/d"
        raw["analysis"]["evidence"]=["[N0]可用容量下降约10.8 mcm/d → TTF供应风险", "[E1]历史假设损失10.8 mcm/d → 需要核对类比条件"]
        raw["news_impact"]["bullish_evidence"]=["[N0]容量下降到87.0 mcm/d → TTF供应风险"]
        analyst=FakeAnalyst([json.dumps(raw)])
        parsed=await analyst.analyze(value)
        self.assertIn("10.6",parsed["news_impact"]["summary"])
        self.assertNotIn("10.8",parsed["news_impact"]["summary"])
        self.assertIn("10.8",parsed["analysis"]["evidence"][1])
        self.assertEqual(parsed["news_impact"]["bullish_evidence"],raw["news_impact"]["bullish_evidence"])
        self.assertIn("属于2026-10-13结算窗口",parsed["analysis"]["summary"])
        self.assertNotIn("本窗口唯一新增供应冲击",parsed["analysis"]["summary"])
        self.assertEqual(parsed["news_impact"]["direction"],raw["news_impact"]["direction"])
        self.assertEqual(parsed["news_impact"]["confidence"],raw["news_impact"]["confidence"])
        self.assertIn("current_notice_fact_corrections",parsed["_meta"])

    def test_real_article_detail_snapshot_fits_budget_with_all_window_ids(self):
        root=Path(__file__).resolve().parents[1];db=root/"data"/"ttf_news.sqlite3"
        if not db.exists():self.skipTest("部署快照未提供")
        conn=sqlite3.connect(f"file:{db}?mode=ro",uri=True)
        try:row=conn.execute("SELECT input_snapshot FROM analyses WHERE id=?",("a63a4f8d-effe-450c-83d4-7562be31282f",)).fetchone()
        finally:conn.close()
        if row is None:self.skipTest("真实article快照未提供")
        original=json.loads(row[0]);data,meta=prepare_context(original,True,128)
        self.assertLessEqual(meta["input_token_estimate"],meta["input_token_limit"])
        self.assertEqual(len(data["previous_sessions"]),5)
        self.assertEqual(meta["window_news_count"],len(original["window_news"]))
        self.assertTrue(data["compression"]["every_news_id_retained"])
        self.assertIn("97.6",data["current_news"]["facts"])
        self.assertIn("10.6",data["current_news"]["facts"])
        self.assertLessEqual(meta["input_token_ceiling"]+meta["output_token_limit"],8192)

    async def test_supply_shortfall_and_warm_heating_conditions_cannot_be_reversed(self):
        value=context();value["current_news"]["body"]+=" 欧洲天气温和，LNG到港增加。"
        output=detailed_result()
        output["detail"]["scenarios"]["bearish"]="若停机导致流量恢复不足，供应过剩导致TTF价格下跌。"
        output["detail"]["invalidation_conditions"]=["[N0]天气变热导致需求上升"]
        before=copy.deepcopy(output["news_impact"])
        analyst=FakeAnalyst([json.dumps(output)])
        parsed=await analyst.analyze(value,True)
        self.assertEqual(parsed["news_impact"],before)
        self.assertIn("实际供气流量恢复且供应缺口缓解",parsed["detail"]["scenarios"]["bearish"])
        self.assertIn("天气转冷、取暖需求增强",parsed["detail"]["invalidation_conditions"][0])
        self.assertEqual(len(parsed["_meta"]["condition_fact_corrections"]),2)
        self.assertFalse(parsed["_meta"]["schema_repair_used"])

    async def test_supplied_cooling_mechanism_can_support_warm_demand_condition(self):
        value=context();value["current_news"]["body"]+=" Warm weather drives air conditioning cooling gas demand."
        output=detailed_result();output["detail"]["invalidation_conditions"]=["[N0]天气变热导致需求上升"]
        analyst=FakeAnalyst([json.dumps(output)])
        parsed=await analyst.analyze(value,True)
        self.assertEqual(parsed["detail"]["invalidation_conditions"],output["detail"]["invalidation_conditions"])
        self.assertNotIn("condition_fact_corrections",parsed["_meta"])

    async def test_unplanned_notice_is_not_reclassified_as_planned_maintenance(self):
        value=context();value["as_of"]="2026-10-14T04:06:00Z"
        value["current_news"].update(source="gassco",title="Gassco Kårstø: Unplanned Gas treatment plant unavailability",event_end="2026-10-14T06:00+0200")
        output=result("flat")
        output["news_impact"]["summary"]="计划停机公告时段已结束，实际流量恢复待核实"
        output["analysis"]["summary"]="计划检修公告时段已结束，但尚未确认实际恢复"
        output["analysis"]["evidence"]=["[N0]计划停机公告时段结束 → TTF残余压力取决于实际流量恢复"]
        direction,score,confidence=output["news_impact"]["direction"],output["news_impact"]["contextual_news_score"],output["news_impact"]["confidence"]
        analyst=FakeAnalyst([json.dumps(output)])
        parsed=await analyst.analyze(value)
        self.assertEqual(parsed["news_impact"]["direction"],direction)
        self.assertEqual(parsed["news_impact"]["contextual_news_score"],score)
        self.assertEqual(parsed["news_impact"]["confidence"],confidence)
        self.assertIn("非计划停机",parsed["news_impact"]["summary"])
        self.assertIn("非计划设施不可用",parsed["analysis"]["summary"])
        self.assertEqual(len(parsed["_meta"]["source_attribute_corrections"]),3)
        self.assertIn("计划检修",parsed["_meta"]["source_attribute_corrections"][1]["original"])
        self.assertFalse(parsed["_meta"]["schema_repair_used"])

    async def test_unplanned_notice_does_not_double_negate_already_negated_attribute(self):
        value=context();value["as_of"]="2026-10-14T04:06:00Z"
        value["current_news"].update(source="gassco",title="Gassco Kårstø: Unplanned Gas treatment plant unavailability",event_end="2026-10-14T06:00+0200")
        output=result("flat")
        output["news_impact"]["summary"]="公告时段已结束，实际流量恢复待核实"
        output["analysis"]["summary"]="Gassco Kårstø未计划停机，公告时段已结束，实际流量恢复待核实"
        output["analysis"]["evidence"]=["[N0]非计划停机公告时段结束 → TTF压力取决于实际流量恢复"]
        analyst=FakeAnalyst([json.dumps(output)])
        parsed=await analyst.analyze(value)
        self.assertEqual(parsed["analysis"]["summary"],output["analysis"]["summary"])
        self.assertNotIn("未非计划",json.dumps(parsed,ensure_ascii=False))
        self.assertNotIn("非非计划",json.dumps(parsed,ensure_ascii=False))

    async def test_bounded_evidence_repair_never_repairs_null_core_conclusion(self):
        value=context()
        value["historical_events"]=[{"id":"old","event_date":"2022-12-21","summary":"Russian supply remains stable despite a pipeline blast."}]
        first=detailed_result();first["detail"]["historical_comparisons"]=["[E1]管道爆炸未影响价格"];first["news_impact"]["confidence"]=None
        second=detailed_result();second["news_impact"]["confidence"]=None
        analyst=FakeAnalyst([json.dumps(first),json.dumps(second)])
        with self.assertRaisesRegex(AnalysisError,"未生成方向"):
            await analyst.analyze(value,True)
        self.assertEqual(len(analyst.calls),2)

    async def test_missing_pricing_evidence_cannot_be_claimed_as_priced_in(self):
        bad=detailed_result();bad["detail"]["priced_in_assessment"]="事件已计入价格，上涨空间受限"
        good=detailed_result();good["detail"]["priced_in_assessment"]="没有盘中报价和公告前后反应，无法确认已计价程度"
        analyst=FakeAnalyst([json.dumps(bad),json.dumps(good)])
        output=await analyst.analyze(context(),True)
        self.assertTrue(output["_meta"]["evidence_grounding_used"])
        self.assertFalse(output["_meta"]["schema_repair_used"])
        self.assertIn("盘中报价",output["detail"]["priced_in_assessment"])
        self.assertEqual(output["news_impact"]["direction"],good["news_impact"]["direction"])
        self.assertIn("detail.priced_in_assessment",output["_meta"]["grounded_evidence_fields"])
        self.assertEqual(output["_meta"]["raw_generated_evidence"]["detail.priced_in_assessment"],bad["detail"]["priced_in_assessment"])
        data=copy.deepcopy(analyst.calls[0][1]);data["compression"]["constrain_priced_in"]=True
        constrained=response_schema(True,data)
        self.assertIn("enum",constrained["$defs"]["DetailAnalysis"]["properties"]["priced_in_assessment"])

    async def test_historical_supply_stability_is_not_unobserved_price_stability(self):
        value=context()
        value["historical_events"]=[{"id":"legacy","event_date":"2022-12-21","summary":"Russian supply remains stable despite a pipeline blast.","source_quality":"daily_aggregate_no_original_url"}]
        bad=detailed_result()
        bad["detail"]["historical_comparisons"]=["[E1]管道爆炸后未影响价格，说明供应中断未必导致价格上行"]
        good=detailed_result()
        good["detail"]["historical_comparisons"]=["[E1]管道事故后供气稳定，与手动停产情景存在差异；历史价格反应无法量化"]
        analyst=FakeAnalyst([json.dumps(bad),json.dumps(good)])
        output=await analyst.analyze(value,True)
        self.assertFalse(output["_meta"]["schema_repair_used"])
        self.assertTrue(analyst.calls[0][1]["compression"]["ground_history"])
        self.assertTrue(output["_meta"]["evidence_grounding_used"])
        self.assertIn("无法量化",output["detail"]["historical_comparisons"][0])
        data=copy.deepcopy(analyst.calls[0][1]);data["compression"]["constrain_history"]=True
        constrained=response_schema(True,data)
        choices=constrained["$defs"]["DetailAnalysis"]["properties"]["historical_comparisons"]["items"]["enum"]
        self.assertIn("无法量化",choices[0])
        self.assertNotIn("未影响价格",choices[0])
        self.assertIn("legacy",[ref["id"] for ref in output["detail"]["references"]])
        self.assertIn("legacy",[ref["id"] for ref in output["references"]])

    async def test_known_reference_outcomes_cannot_become_fabricated_amplitude_or_cause(self):
        value=context()
        value["historical_events"]=[{"id":"reference","event_date":"2026-10-01","summary":"Norway gas outage", "outcomes":{"1d":{"change":1.2,"end_date":"2026-10-02","available_at":"2026-10-03T16:00:00Z","contract":"2026-11","price_type":"daily_reference"}}}]
        valid=detailed_result()
        valid["detail"]["historical_comparisons"]=["[E1]同合约一日参考价格上涨1.2欧元/MWh，同期变化不能证明事件因果"]
        for wrong in ("[E1]历史参考价格上涨9.9欧元/MWh", "[E1]历史停产导致TTF价格上涨1.2欧元/MWh", "[E1]停产导致1d涨1.2欧元", "[E1]停产导致1d涨1.2", "[E1]该事件已定价", "[E1]该事件未出现LNG到港与温和天气并存"):
            with self.subTest(wrong=wrong):
                invalid=detailed_result();invalid["detail"]["historical_comparisons"]=[wrong]
                analyst=FakeAnalyst([json.dumps(invalid),json.dumps(valid)])
                output=await analyst.analyze(value,True)
                self.assertFalse(output["_meta"]["schema_repair_used"])
                self.assertTrue(output["_meta"]["evidence_grounding_used"])
                self.assertIn("+1.20欧元/MWh",output["detail"]["historical_comparisons"][0])
                self.assertEqual(output["_meta"]["raw_generated_evidence"]["detail.historical_comparisons"],[wrong])

    async def test_uncalibrated_price_magnitude_is_repaired(self):
        bad = result()
        bad["close_forecast"]["summary"] = "预计TTF价格上行约0.5–1.0 €/MWh"
        analyst = FakeAnalyst([json.dumps(bad), json.dumps(result())])
        output = await analyst.analyze(context())
        self.assertTrue(output["_meta"]["schema_repair_used"])

    async def test_expired_umm_requires_end_notice_and_unverified_actual_flow(self):
        value = context()
        value["as_of"] = "2026-10-14T04:06:00Z"
        value["current_news"].update(source="gassco", event_end="2026-10-14T06:00+0200")
        good = result("flat")
        good["news_impact"]["summary"] = "公告所列时段已结束，实际流量恢复待核实，当前残余压力不明"
        good["news_impact"]["bullish_evidence"] = ["[N0]公告容量受限 → 若流量未恢复，TTF仍有残余压力"]
        analyst = FakeAnalyst([json.dumps(result()), json.dumps(good)])
        output = await analyst.analyze(value)
        self.assertFalse(output["_meta"]["schema_repair_used"])
        self.assertEqual(output["news_impact"]["direction"],"up")
        self.assertEqual(output["news_impact"]["contextual_news_score"],1.0)
        self.assertIn("[N0]公告预计时段已结束（结束时刻2026-10-14T04:00:00+00:00）",output["news_impact"]["summary"])
        self.assertIn("实际流量恢复待核实",output["news_impact"]["summary"])
        self.assertEqual(output["_meta"]["notice_timing_corrections"][0]["original"],result()["news_impact"]["summary"])
        self.assertIn("actual_flow_recovery_verification", output["missing_fields"])

    async def test_detail_notice_timing_is_grounded_without_changing_prediction_scalars(self):
        value=context();value["as_of"]="2026-10-14T05:22:00Z"
        value["current_news"].update(source="gassco",title="Gassco Kårstø: Unplanned Gas treatment plant unavailability",event_end="2026-10-14T06:00+0200")
        initial=detailed_result()
        analyst=FakeAnalyst([json.dumps(initial)])
        output=await analyst.analyze(value,True)
        self.assertEqual(output["news_impact"]["direction"],initial["news_impact"]["direction"])
        self.assertEqual(output["news_impact"]["confidence"],initial["news_impact"]["confidence"])
        self.assertEqual(output["close_forecast"]["direction"],initial["close_forecast"]["direction"])
        self.assertIn("[N0]公告预计时段已结束（结束时刻2026-10-14T04:00:00+00:00）",output["detail"]["new_facts"][0])
        self.assertEqual(len(output["_meta"]["notice_timing_corrections"]),2)
        self.assertFalse(output["_meta"]["schema_repair_used"])

    async def test_bad_json_repair_once_success(self):
        analyst = FakeAnalyst(["not JSON", json.dumps(result())])
        value = await analyst.analyze(context())
        self.assertTrue(value["_meta"]["schema_repair_used"])
        self.assertEqual(len(analyst.calls), 2)
        self.assertEqual(len(value["_meta"]["system_prompt_sha256"]),64)
        self.assertEqual(len(value["_meta"]["response_schema_sha256"]),64)
        self.assertNotEqual(value["_meta"]["model_calls"][0]["system_prompt_sha256"],value["_meta"]["model_calls"][1]["system_prompt_sha256"])

    def test_fresh_production_event_reactions_fit_brief_and_detail_read_only(self):
        from ttf_news.store import Store
        from ttf_news.context import build_context
        root = Path(__file__).resolve().parents[1]
        database = root / "data" / "ttf_news.sqlite3"
        if not database.exists():
            self.skipTest("部署机器生产数据库不可用")
        class ReadOnlyStore(Store):
            def __init__(self, path): self.path=Path(path)
            @contextmanager
            def connection(self):
                conn=sqlite3.connect(f"file:{self.path}?mode=ro",uri=True)
                conn.row_factory=sqlite3.Row
                try: yield conn
                finally: conn.close()
        store=ReadOnlyStore(database)
        with store.connection() as conn:
            row=conn.execute("SELECT input_snapshot FROM analyses WHERE id=?",("9c30b3a1-10d2-4e77-9e07-6d25d4314cbe",)).fetchone()
        if row is None:
            self.skipTest("真实手动回归快照未提供")
        fresh=build_context(store,json.loads(row[0])["current_news"],kind="manual")
        for detailed in (False,True):
            with self.subTest(detail=detailed):
                data, meta=prepare_context(fresh,detailed)
                self.assertLessEqual(meta["input_token_estimate"],meta["input_token_ceiling"])
                self.assertLessEqual(meta["input_token_ceiling"]+meta["output_token_limit"],meta["context_window"])
                self.assertIn("意外停产",data["current_news"]["facts"])
                self.assertIn("持续三天",data["current_news"]["facts"])
                self.assertEqual(meta["window_news_count"],len(fresh["window_news"]))
                for event in data["historical_events"]:
                    if event.get("outcomes"):
                        self.assertTrue(set(event["outcomes"]).issubset({"1d","3d"}))
    def test_real_failed_production_snapshots_fit_and_keep_current_facts(self):
        root = Path(__file__).resolve().parents[1]
        ids_path = root / "reports" / "final_model_validation_ids.json"
        database = root / "data" / "ttf_news.sqlite3"
        if not ids_path.exists() or not database.exists():
            self.skipTest("真实生产快照未随该checkout提供；需在部署机器运行本测试")
        ids = json.loads(ids_path.read_text())
        conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            for kind, identifier in ids.items():
                row = conn.execute("SELECT input_snapshot FROM analyses WHERE id=?", (identifier,)).fetchone()
                self.assertIsNotNone(row)
                original = json.loads(row[0])
                data, meta = prepare_context(original)
                with self.subTest(kind=kind):
                    self.assertLessEqual(meta["input_token_estimate"], meta["input_token_ceiling"])
                    self.assertIn(meta["context_window"], (4096, 8192))
                    self.assertTrue(data["compression"]["every_news_id_retained"])
                    self.assertEqual(meta["window_news_count"], len(original["window_news"]))
                    self.assertEqual(len(meta["previous_source_index"]), min(5, len(original["previous_sessions"])))
                    for raw_day, indexed in zip(original["previous_sessions"][-5:], meta["previous_source_index"]):
                        self.assertEqual(set(indexed["news_ids"]), set(raw_day.get("news_ids", [])))
                    if kind == "manual":
                        self.assertIn("意外停产", data["current_news"]["facts"])
                        self.assertIn("持续三天", data["current_news"]["facts"])
                        self.assertIn("天气温和", data["current_news"]["facts"])
                        self.assertIn("LNG到港增加", data["current_news"]["facts"])
        finally:
            conn.close()

    async def test_null_then_repair_success(self):
        bad = result()
        bad["news_impact"] = None
        analyst = FakeAnalyst([json.dumps(bad), json.dumps(result())])
        value = await analyst.analyze(context())
        self.assertEqual(value["news_impact"]["direction"], "up")

    async def test_both_fail_never_defaults_direction(self):
        analyst = FakeAnalyst(["{}", "null"])
        with self.assertRaisesRegex(AnalysisError, "未生成方向"):
            await analyst.analyze(context())
        self.assertEqual(len(analyst.calls), 2)

    async def test_actual_ollama_input_overflow_rejected(self):
        analyst = FakeAnalyst([{"message": {"content": json.dumps(result())}, "prompt_eval_count": 2500}])
        with self.assertRaisesRegex(AnalysisError, "超过预算"):
            await analyst.analyze(context())

    async def test_detail_required_and_validated(self):
        output = detailed_result()
        analyst = FakeAnalyst([json.dumps(output)])
        value = await analyst.analyze(context(), detail=True)
        self.assertIn("scenarios", value["detail"])
        self.assertTrue(value["_meta"]["detail"])

    async def test_missing_data_risks_are_added_even_if_model_omits_them(self):
        value = context()
        value["current_news"].update(published_at=None, timestamp_precision="unknown", url=None)
        analyst = FakeAnalyst([json.dumps(result())])
        output = await analyst.analyze(value)
        self.assertIn("previous_contract_price", output["missing_fields"])
        self.assertIn("sigma60", output["missing_fields"])
        self.assertIn("confidence_calibration", output["missing_fields"])
        self.assertIn("current_news.published_at", output["missing_fields"])
        self.assertIn("independent_news_verification", output["missing_fields"])
        self.assertTrue(output["risk_flags"])

    async def test_omitted_supply_shock_requires_repair_without_forced_direction(self):
        bad = result("flat")
        bad["news_impact"]["bullish_evidence"] = []
        valid = result("flat")
        analyst = FakeAnalyst([json.dumps(bad), json.dumps(valid)])
        output = await analyst.analyze(context())
        self.assertTrue(output["_meta"]["schema_repair_used"])
        self.assertEqual(output["news_impact"]["direction"], "flat")

    async def test_independent_weather_lng_facts_cannot_become_invented_causality(self):
        value = context()
        value["current_news"].update(title="条件情景", body="挪威设施发生意外停产；欧洲天气温和，同时LNG到港增加。")
        bad = result("flat")
        bad["analysis"]["evidence"].append("[N0]欧洲天气温和导致LNG到港增加 → TTF下行压力")
        good = result("flat")
        good["analysis"]["evidence"].extend(["[N0]欧洲天气温和 → 取暖需求下降，TTF下行压力", "[N0]LNG到港增加 → 供应改善，TTF下行压力"])
        analyst = FakeAnalyst([json.dumps(bad), json.dumps(good)])
        output = await analyst.analyze(value)
        self.assertTrue(output["_meta"]["schema_repair_used"])
        self.assertFalse(any("天气温和导致LNG" in item for item in output["analysis"]["evidence"]))

    async def test_english_output_triggers_one_translation_repair(self):
        english = result()
        english["close_forecast"]["basis"] = "A gas outage may reduce supply but there is no recent settlement data."
        analyst = FakeAnalyst([json.dumps(english), json.dumps(result())])
        output = await analyst.analyze(context())
        self.assertTrue(output["_meta"]["schema_repair_used"])
        self.assertEqual(len(analyst.calls), 2)


if __name__ == "__main__":
    unittest.main()
