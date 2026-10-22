import copy
import asyncio
import hashlib
import json
import re
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
    _json,
    semantic_high_scope_eligible,
)
from ttf_news.prompts import DETAIL_SHAPE, SHORT_SHAPE, system_prompt
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
    if direction in ('down','extreme_down'):
        value['news_impact']['bullish_evidence']=[]
        value['news_impact']['bearish_evidence']=['[N0]供应增加 → TTF下行压力增强']
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
        "scenarios": {"bullish": "若[N0]中断扩大或持续 → TTF上涨压力增强", "base": "若[N0]中断维持且需求平稳 → 供应风险持续", "bearish": "若[N0]设施复产、供给恢复 → TTF下行压力增强"},
        "invalidation_conditions": ["[N0]重启得到确认后中断风险结论需重新评估"],
        "limitations": ["price_reference为空，不能核对官方日结算异常阈值"],
        "references": [{"id": "N0", "url": "", "title": "input news"}],
    }
    return value


def without_closing_anchor(value):
    """A neutral-news fixture with no previous-day price has no close label."""
    value['close_forecast'].update(
        direction=None, evidence_status='insufficient_evidence',
        summary='缺少上一交易日同合约价格，暂无法形成收盘判断',
        basis='新闻净压力无明确方向，且缺少上一交易日同合约价格锚点')
    return value


class FakeAnalyst(NewsAnalyst):
    def __init__(self, outputs, **kwargs):
        super().__init__(**kwargs)
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
        self.assertEqual(schema["$defs"]["Analysis"]["properties"]["evidence"]["minItems"], 1)
        self.assertNotIn("detail", response_schema(False)["properties"])

    def test_generation_schema_uses_native_bounds_without_changing_machine_fields(self):
        schema=response_schema(True)
        evidence=schema['$defs']['Analysis']['properties']['evidence']['items']
        self.assertNotIn('pattern',evidence)
        self.assertEqual(evidence['maxLength'],450)
        self.assertEqual(schema['$defs']['CloseForecast']['properties']['basis']['maxLength'],600)
        self.assertNotIn('pattern',schema['$defs']['CloseForecast']['properties']['basis'])
        self.assertNotIn('pattern',schema['$defs']['Reference']['properties']['id'])
        self.assertNotIn('pattern',schema['$defs']['Reference']['properties']['url'])
        self.assertNotIn('pattern',schema['$defs']['Analysis']['properties']['matched_terms']['items'])
        self.assertNotIn('pattern',schema['properties']['missing_fields']['items'])
        self.assertEqual(schema['$defs']['NewsImpact']['properties']['direction']['enum'],['extreme_up','up','flat','down','extreme_down'])
        self.assertNotIn('pattern',AnalysisResult.model_json_schema()['$defs']['Analysis']['properties']['evidence']['items'])
        optional=schema['$defs']['CloseForecast']['properties']['extreme_move_risk']['anyOf'][0]
        self.assertNotIn('pattern',optional)
        self.assertEqual(optional['maxLength'],300)

    def test_operator_fact_copy_is_first_const_without_restricting_direction(self):
        value=context()
        value['current_news'].update(source='gassco',title='Gassco Åsgard: Unplanned unavailability',
            summary='Reason: Yearly maintenance.',event_end='2026-10-13T04:00:00Z',unavailable_capacity='5.2')
        data,_=prepare_context(value)
        schema=response_schema(False,data)
        self.assertEqual(next(iter(schema['properties'])),'source_fact_checks')
        self.assertIn('source_fact_checks',schema['required'])
        checks=schema['properties']['source_fact_checks']['const']
        self.assertEqual(checks['operator_notices'][0]['id'],data['evidence_ledger']['notice_facts'][0]['id'])
        self.assertEqual(checks['operator_notices'][0]['unavailable_capacity'],'5.2')
        self.assertEqual(checks['operator_notices'][0]['notice_type'],'非计划')
        self.assertIn('已结束',checks['operator_notices'][0]['state_description'])
        self.assertFalse(checks['pricing_observation']['reaction_evidence_available'])
        self.assertNotIn('event_end_utc',checks['operator_notices'][0])
        self.assertEqual(schema['$defs']['NewsImpact']['properties']['direction']['enum'],['extreme_up','up','flat','down','extreme_down'])
        legacy=result()
        self.assertIsNone(AnalysisResult.model_validate(legacy).source_fact_checks)
        self.assertNotIn('source_fact_checks',response_schema(False)['properties'])

    def test_known_history_is_grounded_from_first_generation(self):
        data,_=prepare_context(context())
        data['historical_events']=[{'id':'E1','date':'2026-10-01','category':'supply','facts':'Gas outage','outcomes':{'1d':{'change':2.58,'end_date':'2026-10-02'}}}]
        for detailed in (False,True):
            schema=response_schema(detailed,data)
            options=schema['$defs']['Analysis']['properties']['historical_comparisons']['items']['enum']
            self.assertTrue(any('2.58' in item and '不能归因' in item for item in options))

    def test_generation_source_prefix_pattern_is_bounded_and_cannot_consume_json_syntax(self):
        schema=response_schema(True,{'window_news':[{'id':'N3'}]})
        patterns=[schema['$defs']['DetailAnalysis']['properties'][field]['items']['pattern']
                  for field in ('new_facts','transmission','daily_balance','invalidation_conditions')]
        for pattern in patterns:
            with self.subTest(pattern=pattern):
                self.assertEqual(pattern.count('^'),pattern.count('[^')+1)
                self.assertEqual(pattern.count('$'),1)
                self.assertNotIn('^$|^',pattern)
                self.assertNotIn(r'[^\n]',pattern)
                self.assertNotIn('*',pattern)
                self.assertIsNotNone(re.search(pattern,'[N3] Åsgard不可用容量5.2 mcm/d → TTF供给约束'))
                for unsafe in ['"','\\','\n','\r','\t','\x00','\x1f','\x7f']:
                    self.assertIsNone(re.fullmatch(pattern,'[N3]供应约束容量下降形成上行压力'+unsafe))

    def test_scenarios_keep_bounded_condition_prefix_and_post_generation_condition_check(self):
        schema=response_schema(True)
        source=context();_,meta=prepare_context(source,True)
        for field in ('bullish','base','bearish'):
            node=schema['$defs']['Scenarios']['properties'][field]
            self.assertIsNotNone(re.fullmatch(node['pattern'],'若欧洲供气恢复持续，供需情景待验证'))
            self.assertIsNone(re.fullmatch(node['pattern'],'无供应缺口，需求稳定持续市场'))
            self.assertEqual((node['minLength'],node['maxLength']),(12,600))
            output=detailed_result();output['detail']['scenarios'][field]='无供应缺口，需求稳定持续市场'
            with self.assertRaisesRegex(ValueError,'条件情景'):
                _parse_result(json.dumps(output),True,meta['source_index'],source)

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
        with self.assertRaises(ValueError):
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
    def test_notice_fact_ledger_separates_source_type_from_exact_cause(self):
        import copy
        value=context()
        value['current_news'].update(source='gassco',title='Gassco Nyhamna: Planned Other unavailability (Active)',
            summary='Reason: Yearly maintenance.',event_start='2026-10-13T04:00:00Z',event_end='2026-10-20T04:00:00Z',
            technical_capacity='79.8',available_capacity='60.0',unavailable_capacity='19.8',capacity_unit='mcm/d')
        value['window_news']=[copy.deepcopy(value['current_news']),{'id':'asg','source':'gassco',
            'title':'Gassco Åsgard: Unplanned field unavailability (Active)',
            'summary':'Reason: Yearly maintenance. Remarks: Planned maintenance prolonged.',
            'published_at':'2026-10-14T02:00:00Z','event_start':'2026-10-13T04:00:00Z','event_end':'2026-10-20T04:00:00Z',
            'unavailable_capacity':'5.2','capacity_unit':'mcm/d'}]
        before=copy.deepcopy(value)
        data,_=prepare_context(value)
        facts=data['evidence_ledger']['notice_facts']
        self.assertEqual([row['id'] for row in facts],['N0','N2'])
        self.assertEqual([row['notice_type'] for row in facts],['计划','非计划'])
        self.assertEqual([row['cause_original'] for row in facts],['Yearly maintenance','Yearly maintenance'])
        self.assertEqual(facts[1]['unavailable_capacity'],'5.2')
        self.assertEqual(facts[1]['event_start_utc'],'2026-10-13T04:00:00+00:00')
        self.assertEqual(data['window_news'][1]['operational']['event_start_utc'],'2026-10-13T04:00:00+00:00')
        self.assertIn('未提供',facts[1]['actual_flow_observation'])
        self.assertNotRegex(json.dumps(facts,ensure_ascii=False),r'看涨|看跌|上涨|下跌|forecast|direction')
        self.assertEqual(value,before)

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
        self.assertLessEqual(meta["input_token_ceiling"]+meta["output_token_limit"],meta["context_window"])
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
    async def test_all_scope_bounded_semantic_high_retry_can_be_enabled_or_disabled(self):
        self.assertFalse(NewsAnalyst().semantic_high_retry)
        self.assertEqual(NewsAnalyst().semantic_high_scope,'operator_notice')
        bad=result('down')
        bad['news_impact']['bearish_evidence']=['[N0]欧洲供应减少 → TTF下行压力']
        for enabled in (False,True):
            analyst=FakeAnalyst([json.dumps(bad),json.dumps(result())],semantic_high_retry=enabled,semantic_high_scope='all')
            output=await analyst.analyze(context())
            calls=output['_meta']['model_calls']
            self.assertEqual([call['think'] for call in calls],['medium','high' if enabled else 'medium'])
            self.assertEqual(calls[0]['validation_stage'],'fact_or_mechanism')
            self.assertEqual(analyst.reasoning,'medium')
            self.assertEqual(output['_meta']['configured_reasoning'],'medium')
            self.assertEqual(output['_meta']['reasoning'],'high' if enabled else 'medium')
            self.assertEqual(output['_meta']['semantic_high_retry_used'],enabled)
            self.assertEqual(json.loads(calls[0]['raw_content']),bad)
            if enabled:
                self.assertEqual((calls[1]['num_ctx'],calls[1]['num_predict'],calls[1]['input_token_ceiling']),(16384,12288,3072))
                self.assertLessEqual(calls[1]['input_token_estimate'],3072)
                self.assertEqual(analyst.calls[1][0],system_prompt(False))
                self.assertTrue(calls[1]['semantic_independent_regeneration'])
                self.assertIn('validation_error',calls[0])
                self.assertNotIn(calls[0]['validation_error'],analyst.calls[1][0])
                self.assertEqual(analyst.calls[0][1]['current_news'],analyst.calls[1][1]['current_news'])
            for call,(_,data,_) in zip(calls,analyst.calls):
                self.assertEqual(call['context_sha256'],hashlib.sha256(_json(data).encode()).hexdigest())
                self.assertEqual(call['think'],data['compression']['request_reasoning'])

    async def test_explicit_operator_notice_semantic_repair_uses_bounded_high(self):
        bad=result('down');bad['news_impact']['bearish_evidence']=['[N0]欧洲供应减少 → TTF下行压力']
        analyst=FakeAnalyst([json.dumps(bad),json.dumps(result())],semantic_high_retry=True)
        value=context();value['current_news'].update(source='gassco',title='Gassco Troll: Unplanned unavailability (Active)',event_status='Active')
        output=await analyst.analyze(value)
        self.assertEqual([call['think'] for call in output['_meta']['model_calls']],['medium','high'])
        self.assertTrue(output['_meta']['semantic_high_retry_used'])
        self.assertTrue(output['_meta']['semantic_high_scope_eligible'])
        self.assertEqual(output['_meta']['semantic_high_scope'],'operator_notice')

    async def test_production_default_operator_semantic_repair_uses_medium_with_specific_feedback(self):
        bad=result('down');bad['news_impact']['bearish_evidence']=['[N0]欧洲供应减少 → TTF下行压力']
        analyst=FakeAnalyst([json.dumps(bad),json.dumps(result())])
        value=context();value['current_news'].update(source='gassco',title='Gassco Troll: Unplanned unavailability (Active)',event_status='Active')
        output=await analyst.analyze(value)
        calls=output['_meta']['model_calls']
        self.assertEqual([call['think'] for call in calls],['medium','medium'])
        self.assertFalse(output['_meta']['semantic_high_retry_enabled'])
        self.assertFalse(output['_meta']['semantic_high_retry_used'])
        self.assertTrue(output['_meta']['semantic_high_scope_eligible'])
        self.assertEqual(calls[1]['retry_policy'],'disabled')
        self.assertFalse(calls[1]['semantic_independent_regeneration'])
        self.assertIn('REPAIR_FEEDBACK',analyst.calls[1][1])
        self.assertIn('供需机制相反',_json(analyst.calls[1][1]['REPAIR_FEEDBACK']))
        self.assertEqual(json.loads(calls[0]['raw_content']),bad)

    async def test_production_default_double_semantic_failure_keeps_two_medium_attempts(self):
        bad=result('down');bad['news_impact']['bearish_evidence']=['[N0]欧洲供应减少 → TTF下行压力']
        analyst=FakeAnalyst([json.dumps(bad),json.dumps(bad)])
        value=context();value['current_news'].update(source='gassco',title='Gassco Troll: Unplanned unavailability (Active)',event_status='Active')
        with self.assertRaises(AnalysisError) as raised:
            await analyst.analyze(value)
        calls=raised.exception.audit['model_calls']
        self.assertEqual(len(calls),2)
        self.assertEqual([call['think'] for call in calls],['medium','medium'])
        self.assertFalse(raised.exception.audit['semantic_high_retry_enabled'])
        self.assertFalse(raised.exception.audit['semantic_independent_regeneration'])
        self.assertEqual(json.loads(calls[0]['raw_content']),bad)
        self.assertEqual(json.loads(calls[1]['raw_content']),bad)

    async def test_default_non_operator_semantic_repair_stays_medium(self):
        bad=result('down');bad['news_impact']['bearish_evidence']=['[N0]欧洲供应减少 → TTF下行压力']
        analyst=FakeAnalyst([json.dumps(bad),json.dumps(result())])
        output=await analyst.analyze(context())
        self.assertEqual([call['think'] for call in output['_meta']['model_calls']],['medium','medium'])
        self.assertFalse(output['_meta']['semantic_high_scope_eligible'])
        self.assertFalse(output['_meta']['semantic_high_retry_used'])
        self.assertEqual(output['_meta']['model_calls'][1]['retry_policy'],'disabled')
        self.assertIn('REPAIR_FEEDBACK',analyst.calls[1][1])
        self.assertFalse(semantic_high_scope_eligible({'current_news':{'source':'offshore_energy'}},context()))
        self.assertTrue(semantic_high_scope_eligible({'evidence_ledger':{'notice_facts':[{'id':'N0'}]}},context()))
        self.assertTrue(semantic_high_scope_eligible({},context(),'all'))
        self.assertFalse(semantic_high_scope_eligible({'evidence_ledger':{'notice_facts':[{'id':'N3'}]}},context()))
        with self.assertRaises(ValueError):
            NewsAnalyst(semantic_high_scope='up_only')

    async def test_json_language_and_truncation_repairs_do_not_trigger_semantic_high(self):
        english=result();english['news_impact']['summary']='Supply disruption causes upward pressure for gas prices.'
        failures=['not JSON',json.dumps(english),{'message':{'content':json.dumps(result())},'done_reason':'length'}]
        for first in failures:
            with self.subTest(first=first):
                analyst=FakeAnalyst([first,json.dumps(result())],semantic_high_retry=True,semantic_high_scope='all')
                output=await analyst.analyze(context())
                self.assertEqual([c['think'] for c in output['_meta']['model_calls']],['medium','medium'])
                self.assertFalse(output['_meta']['semantic_high_retry_used'])
                self.assertIn('前次输出无效',analyst.calls[1][0])
                self.assertIn(output['_meta']['model_calls'][0]['validation_error'],analyst.calls[1][0])
                self.assertFalse(output['_meta']['model_calls'][1]['semantic_independent_regeneration'])

    async def test_oversized_semantic_repair_stays_medium_without_dropping_news(self):
        value=context()
        value['window_news']=[{'id':f'new-{i}','title':f'European gas development number{i}',
            'summary':'欧洲天然气设施持续停机，运营方公布维护期；容量受限的影响取决于后续实际供气状态，尚无独立流量核实。',
            'published_at':'2026-10-14T01:00:00Z'} for i in range(65)]
        bad=result('down')
        bad['news_impact']['bearish_evidence']=['[N0]欧洲供应减少 → TTF下行压力']
        analyst=FakeAnalyst([json.dumps(bad),json.dumps(result())],semantic_high_retry=True,semantic_high_scope='all')
        output=await analyst.analyze(value)
        second=output['_meta']['model_calls'][1]
        self.assertEqual(second['think'],'medium')
        self.assertEqual(second['retry_policy'],'semantic_repair_input_exceeds_7168')
        self.assertIn('REPAIR_FEEDBACK',analyst.calls[1][1])
        self.assertGreater(second['input_token_estimate'],7168)
        self.assertEqual(len(analyst.calls[1][1]['window_news']),65)
        self.assertTrue(analyst.calls[1][1]['compression']['every_news_id_retained'])

    async def test_semantic_high_tiers_choose_largest_fitting_output_without_shortening_input(self):
        for count,expected in ((20,(10240,5120)),(45,(8192,7168))):
            with self.subTest(news_count=count):
                value=context()
                value['window_news']=[{'id':f'new-{i}','title':f'European gas development number{i}',
                    'summary':'欧洲天然气设施持续停机，运营方公布维护期；容量受限的影响取决于后续实际供气状态，尚无独立流量核实。',
                    'published_at':'2026-10-14T01:00:00Z'} for i in range(count)]
                bad=result('down');bad['news_impact']['bearish_evidence']=['[N0]欧洲供应减少 → TTF下行压力']
                analyst=FakeAnalyst([json.dumps(bad),json.dumps(result())],semantic_high_retry=True,semantic_high_scope='all')
                output=await analyst.analyze(value)
                call=output['_meta']['model_calls'][1]
                self.assertEqual(call['think'],'high')
                self.assertEqual((call['num_predict'],call['input_token_ceiling']),expected)
                self.assertEqual(call['num_ctx']-call['num_predict']-call['input_token_ceiling'],1024)
                self.assertLessEqual(call['input_token_estimate'],call['input_token_ceiling'])
                self.assertEqual(len(analyst.calls[1][1]['window_news']),count)

    async def test_bounded_semantic_failure_audit_keeps_both_attempts_and_settings(self):
        bad=result('down');bad['news_impact']['bearish_evidence']=['[N0]欧洲供应减少 → TTF下行压力']
        analyst=FakeAnalyst([json.dumps(bad),json.dumps(bad)],semantic_high_retry=True,semantic_high_scope='all')
        with self.assertRaises(AnalysisError) as raised:
            await analyst.analyze(context())
        audit=raised.exception.audit
        self.assertEqual(audit['configured_reasoning'],'medium')
        self.assertEqual(audit['reasoning'],'high')
        self.assertTrue(audit['semantic_independent_regeneration'])
        self.assertEqual(audit['semantic_high_scope'],'all')
        self.assertEqual(len(audit['model_calls']),2)
        self.assertEqual(audit['model_calls'][1]['num_predict'],12288)
        self.assertEqual(json.loads(audit['model_calls'][0]['raw_content']),bad)
        self.assertEqual(analyst.reasoning,'medium')

    async def test_shared_analyst_request_reasoning_is_call_local(self):
        requests=[]
        class Response:
            def raise_for_status(self):pass
            def json(self):return {'message':{'content':'{}'}}
        class Client:
            def __init__(self,*args,**kwargs):pass
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            async def post(self,url,json):
                requests.append(json)
                await asyncio.sleep(0)
                return Response()
        analyst=NewsAnalyst(semantic_high_retry=True,semantic_high_scope='all')
        medium,_=prepare_context(context())
        medium['compression']['request_reasoning']='medium'
        high=copy.deepcopy(medium)
        high['compression'].update(request_reasoning='high',context_window=16384,output_budget=12288,input_budget=3072)
        with patch('ttf_news.analysis.httpx.AsyncClient',Client):
            await asyncio.gather(analyst._request('system',medium,False),analyst._request('system',high,False))
        self.assertEqual({r['think'] for r in requests},{'medium','high'})
        self.assertEqual(next(r for r in requests if r['think']=='high')['options']['num_predict'],12288)
        self.assertEqual(analyst.reasoning,'medium')

    def assert_raw_retry(self, output, analyst):
        self.assertTrue(output["_meta"]["schema_repair_used"])
        self.assertTrue(output["_meta"]["raw_evidence_validated"])
        self.assertEqual(len(analyst.calls),2)
        self.assertIn("validation_error",output["_meta"]["model_calls"][0])
        self.assertNotIn("validation_error",output["_meta"]["model_calls"][1])
        self.assertNotIn("evidence_grounding_used",output["_meta"])

    async def test_retry_receives_causal_contradiction_as_well_as_language_error(self):
        invalid=result('down')
        invalid['news_impact']['summary']='Supply disruption is bearish for gas prices.'
        invalid['news_impact']['bearish_evidence']=['[N0]欧洲供应减少 → TTF下行压力']
        analyst=FakeAnalyst([json.dumps(invalid),json.dumps(result())])
        output=await analyst.analyze(context())
        self.assert_raw_retry(output,analyst)
        errors=output['_meta']['model_calls'][0]['validation_errors']
        self.assertTrue(any('中文' in error for error in errors))
        self.assertTrue(any('bearish' in error or '看跌' in error for error in errors))
        self.assertIn('中文',analyst.calls[1][0])
        self.assertTrue('bearish' in analyst.calls[1][0] or '看跌' in analyst.calls[1][0])
        self.assertEqual(output['news_impact']['direction'],'up')

    async def test_medium_repair_receives_unknown_flow_and_reversed_supply_errors_together(self):
        value=context()
        value['current_news'].update(title='企业天然气项目合作MOU',source='offshore_energy',
            body='项目合作可能在未来扩大天然气生产，当前交付细节未提供。')
        value['window_news']=[{'id':'operator','source':'gassco','title':'Gassco Troll: Planned unavailability (Active)',
            'summary':'Technical capacity: 100 mcm/d; available: 80; unavailable: 20.',
            'event_status':'Active','published_at':'2026-10-14T01:00:00Z'}]
        valid=result('flat')
        valid['news_impact'].update(summary='企业合作属未来预期，容量公告仅提供潜在供给风险，当前压力需继续评估',
            bullish_evidence=[],bearish_evidence=[],neutralizing_evidence=[])
        valid['analysis'].update(summary='企业合作前瞻与运营容量公告分开分析，当前实际流量未核实',
            evidence=['[N1]公告不可用容量20mcm/d → TTF潜在供给风险'])
        bad=copy.deepcopy(valid)
        bad['news_impact']['bullish_evidence']=['[N0]该项目开启将增加供应 → TTF看涨机制']
        bad['news_impact']['neutralizing_evidence']=['[N1]供给约束未确认流量损失 → TTF中性机制']
        valid=without_closing_anchor(valid)
        analyst=FakeAnalyst([json.dumps(bad),json.dumps(valid)])
        output=await analyst.analyze(value)
        errors=output['_meta']['model_calls'][0]['validation_errors']
        self.assertTrue(any('不能作为TTF中性机制' in error for error in errors))
        self.assertTrue(any('供需机制相反' in error for error in errors))
        self.assertIn('不能作为TTF中性机制',_json(analyst.calls[1][1]['REPAIR_FEEDBACK']))
        self.assertIn('供需机制相反',_json(analyst.calls[1][1]['REPAIR_FEEDBACK']))
        self.assertEqual([c['think'] for c in output['_meta']['model_calls']],['medium','medium'])
        self.assertEqual(json.loads(output['_meta']['model_calls'][0]['raw_content']),bad)
        self.assertEqual(output['news_impact'],valid['news_impact'])

    async def test_current_notice_capacity_and_previous_window_facts_require_reanalysis(self):
        value=context();value["as_of"]="2026-10-14T05:31:00Z"
        value["session"]={"trade_date":"2026-10-14","window_start":"2026-10-13T15:15:00Z","window_end":"2026-10-14T15:15:00Z"}
        value["current_news"].update(source="gassco",title="Kårstø: Unplanned Gas treatment plant unavailability",published_at="2026-10-13T11:20:16Z",event_end="2026-10-14T06:00+0200",body="Technical capacity: 97.6 mcm/d; available: 87.0; unavailable: 10.6.")
        value["historical_events"]=[{"id":"old","event_date":"2026-10-01","title":"Old Norway gas outage","summary":"Reported unavailable capacity: 10.8 mcm/d."}]
        raw=result("up")
        raw["news_impact"]["summary"]="Kårstø可用容量下降约10.8 mcm/d，可能形成供应压力"
        raw["analysis"]["summary"]="Kårstø是本窗口唯一新增供应冲击，缺口约10.8 mcm/d"
        raw["analysis"]["evidence"]=["[N0]可用容量下降约10.8 mcm/d → TTF供应风险", "[E1]历史假设损失10.8 mcm/d → 需要核对类比条件"]
        raw["news_impact"]["bullish_evidence"]=["[N0]容量下降到87.0 mcm/d → TTF供应风险"]
        good=without_closing_anchor(result("flat"))
        good["news_impact"]["summary"]="Kårstø公告不可用容量10.6 mcm/d；公告时段已结束，实际流量恢复待核实"
        good["analysis"]["summary"]="公告在较早结算窗口发布，本次作为已知背景，不能称为本窗口新增冲击"
        good["analysis"]["evidence"]=["[N0]不可用容量10.6 mcm/d → 残余供应压力取决于实际流量", "[E1]历史公告不可用容量10.8 mcm/d → 容量风险可作条件比较"]
        good["news_impact"]["bullish_evidence"]=raw["news_impact"]["bullish_evidence"]
        analyst=FakeAnalyst([json.dumps(raw),json.dumps(good)])
        parsed=await analyst.analyze(value)
        self.assert_raw_retry(parsed,analyst)
        self.assertIn("10.6",parsed["news_impact"]["summary"])
        self.assertNotIn("10.8",parsed["news_impact"]["summary"])
        self.assertIn("10.8",parsed["analysis"]["evidence"][1])
        self.assertEqual(parsed["news_impact"]["bullish_evidence"],raw["news_impact"]["bullish_evidence"])
        self.assertIn("较早结算窗口",parsed["analysis"]["summary"])
        self.assertNotIn("本窗口唯一新增供应冲击",parsed["analysis"]["summary"])
        self.assertEqual(parsed["news_impact"]["direction"],good["news_impact"]["direction"])
        self.assertEqual(parsed["news_impact"]["confidence"],good["news_impact"]["confidence"])
        self.assertIn("10.8",parsed["_meta"]["model_calls"][0]["raw_content"])
        self.assertNotIn("current_notice_fact_corrections",parsed["_meta"])

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
        self.assertEqual(float(data["current_news"]["operational"]["technical_capacity"]),97.6)
        self.assertEqual(float(data["current_news"]["operational"]["unavailable_capacity"]),10.6)
        self.assertLessEqual(meta["input_token_ceiling"]+meta["output_token_limit"],meta["context_window"])

    async def test_supply_shortfall_and_warm_heating_conditions_cannot_be_reversed(self):
        value=context();value["current_news"]["body"]+=" 欧洲天气温和，LNG到港增加。"
        output=detailed_result()
        output["detail"]["scenarios"]["bearish"]="若停机导致流量恢复不足，供应过剩导致TTF价格下跌。"
        output["detail"]["invalidation_conditions"]=["[N0]天气变热导致需求上升"]
        valid=detailed_result()
        valid["detail"]["scenarios"]["bearish"]="若[N0]实际供气流量恢复且供应缺口缓解 → TTF下行压力增强"
        valid["detail"]["invalidation_conditions"]=["[N0]天气转冷、取暖需求增强，使温和天气的下行抵消减弱"]
        analyst=FakeAnalyst([json.dumps(output),json.dumps(valid)])
        parsed=await analyst.analyze(value,True)
        self.assert_raw_retry(parsed,analyst)
        self.assertEqual(parsed["news_impact"],valid["news_impact"])
        self.assertIn("实际供气流量恢复且供应缺口缓解",parsed["detail"]["scenarios"]["bearish"])
        self.assertIn("天气转冷、取暖需求增强",parsed["detail"]["invalidation_conditions"][0])
        self.assertNotIn("condition_fact_corrections",parsed["_meta"])

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
        valid=result("flat")
        valid["news_impact"]["summary"]="非计划停机公告时段已结束，实际流量恢复待核实"
        valid["analysis"]["summary"]="非计划设施不可用公告时段已结束，实际流量恢复待核实"
        valid["analysis"]["evidence"]=["[N0]非计划停机公告时段结束 → TTF残余压力取决于实际流量恢复"]
        valid=without_closing_anchor(valid)
        analyst=FakeAnalyst([json.dumps(output),json.dumps(valid)])
        parsed=await analyst.analyze(value)
        self.assert_raw_retry(parsed,analyst)
        self.assertEqual(parsed["news_impact"]["direction"],direction)
        self.assertEqual(parsed["news_impact"]["contextual_news_score"],score)
        self.assertEqual(parsed["news_impact"]["confidence"],confidence)
        self.assertIn("非计划停机",parsed["news_impact"]["summary"])
        self.assertIn("非计划设施不可用",parsed["analysis"]["summary"])
        self.assertIn("计划检修",json.loads(parsed["_meta"]["model_calls"][0]["raw_content"])["analysis"]["summary"])
        self.assertNotIn("source_attribute_corrections",parsed["_meta"])

    async def test_unplanned_notice_does_not_double_negate_already_negated_attribute(self):
        value=context();value["as_of"]="2026-10-14T04:06:00Z"
        value["current_news"].update(source="gassco",title="Gassco Kårstø: Unplanned Gas treatment plant unavailability",event_end="2026-10-14T06:00+0200")
        output=result("flat")
        output["news_impact"]["summary"]="公告时段已结束，实际流量恢复待核实"
        output["analysis"]["summary"]="Gassco Kårstø未计划停机，公告时段已结束，实际流量恢复待核实"
        output["analysis"]["evidence"]=["[N0]非计划停机公告时段结束 → TTF压力取决于实际流量恢复"]
        output=without_closing_anchor(output)
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
        self.assert_raw_retry(output,analyst)
        self.assertIn("盘中报价",output["detail"]["priced_in_assessment"])
        self.assertEqual(output["news_impact"]["direction"],good["news_impact"]["direction"])
        self.assertEqual(json.loads(output["_meta"]["model_calls"][0]["raw_content"])["detail"]["priced_in_assessment"],bad["detail"]["priced_in_assessment"])
        self.assertEqual(output["detail"]["priced_in_assessment"],good["detail"]["priced_in_assessment"])
        data=copy.deepcopy(analyst.calls[0][1]);data["compression"]["constrain_priced_in"]=True
        constrained=response_schema(True,data)
        self.assertIn("enum",constrained["$defs"]["DetailAnalysis"]["properties"]["priced_in_assessment"])

    async def test_historical_supply_stability_is_not_unobserved_price_stability(self):
        value=context()
        value["historical_events"]=[{"id":"historical-supply","event_date":"2022-12-21","title":"Russian pipeline disruption","summary":"Russian supply remains stable despite a pipeline blast.","source_quality":"source_announcement"}]
        bad=detailed_result()
        bad["detail"]["historical_comparisons"]=["[E1]管道爆炸后未影响价格，说明供应中断未必导致价格上行"]
        good=detailed_result()
        good["detail"]["historical_comparisons"]=["[E1]管道事故后供气稳定，与手动停产情景存在差异；历史价格反应无法量化"]
        analyst=FakeAnalyst([json.dumps(bad),json.dumps(good)])
        output=await analyst.analyze(value,True)
        self.assert_raw_retry(output,analyst)
        self.assertTrue(analyst.calls[0][1]["compression"]["validate_raw_evidence"])
        self.assertIn("无法量化",output["detail"]["historical_comparisons"][0])
        data=copy.deepcopy(analyst.calls[0][1]);data["compression"]["constrain_history"]=True
        constrained=response_schema(True,data)
        choices=constrained["$defs"]["DetailAnalysis"]["properties"]["historical_comparisons"]["items"]["enum"]
        self.assertIn("无法量化",choices[0])
        self.assertNotIn("未影响价格",choices[0])
        self.assertIn("historical-supply",[ref["id"] for ref in output["detail"]["references"]])
        self.assertIn("historical-supply",[ref["id"] for ref in output["references"]])

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
                self.assert_raw_retry(output,analyst)
                self.assertIn("1.2欧元/MWh",output["detail"]["historical_comparisons"][0])
                self.assertEqual(json.loads(output["_meta"]["model_calls"][0]["raw_content"])["detail"]["historical_comparisons"],[wrong])

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
        good = without_closing_anchor(result("flat"))
        good["news_impact"]["summary"] = "公告所列时段已结束，实际流量恢复待核实，当前残余压力不明"
        good["news_impact"]["bullish_evidence"] = ["[N0]公告容量受限 → 若流量未恢复，TTF仍有残余压力"]
        good["analysis"]["evidence"] = ["[N0]公告时段已结束 → 若实际供气未恢复，TTF仍可能承压上涨"]
        analyst = FakeAnalyst([json.dumps(result()), json.dumps(good)])
        output = await analyst.analyze(value)
        self.assert_raw_retry(output,analyst)
        self.assertEqual(output["news_impact"]["direction"],"flat")
        self.assertEqual(output["news_impact"]["contextual_news_score"],0.0)
        self.assertEqual(output["news_impact"]["summary"],good["news_impact"]["summary"])
        self.assertIn("实际流量恢复待核实",output["news_impact"]["summary"])
        self.assertNotIn("notice_timing_corrections",output["_meta"])
        self.assertIn("actual_flow_recovery_verification", output["missing_fields"])

    async def test_detail_notice_timing_requires_regeneration_including_conclusion(self):
        value=context();value["as_of"]="2026-10-14T05:22:00Z"
        value["current_news"].update(source="gassco",title="Gassco Kårstø: Unplanned Gas treatment plant unavailability",event_end="2026-10-14T06:00+0200")
        initial=detailed_result()
        good=detailed_result()
        good["news_impact"].update(direction="flat",contextual_news_score=0.0,summary="非计划停机公告时段已结束，实际流量恢复待核实，残余压力取决于实际供气")
        good["analysis"]["evidence"]=["[N0]公告时段已结束 → 若实际供气未恢复，TTF仍可能有残余涨压"]
        good["detail"]["new_facts"]=["[N0]非计划停机公告时段已结束，实际流量恢复待核实"]
        good=without_closing_anchor(good)
        analyst=FakeAnalyst([json.dumps(initial),json.dumps(good)])
        output=await analyst.analyze(value,True)
        self.assert_raw_retry(output,analyst)
        self.assertEqual(output["news_impact"],good["news_impact"])
        self.assertNotEqual(output["news_impact"]["direction"],initial["news_impact"]["direction"])
        self.assertEqual(output["close_forecast"]["direction"],good["close_forecast"]["direction"])
        self.assertEqual(output["detail"]["new_facts"],good["detail"]["new_facts"])
        self.assertNotIn("notice_timing_corrections",output["_meta"])

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
                    self.assertIn(meta["context_window"], (8192, 16384))
                    self.assertTrue(data["compression"]["every_news_id_retained"])
                    self.assertEqual(meta["window_news_count"], len(original["window_news"]))
                    self.assertEqual(len(meta["previous_source_index"]), min(5, len(original["previous_sessions"])))
                    by_date={str(p.get("trade_date",p.get("session_date",p.get("date")))):p for p in original["previous_sessions"][-5:]}
                    for indexed in meta["previous_source_index"]:
                        raw_day=by_date[str(indexed["date"])]
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
        analyst = FakeAnalyst([{"message": {"content": json.dumps(result())}, "prompt_eval_count": 20000}])
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

    async def test_directional_news_requires_matching_evidence_without_fallback(self):
        bad = result("up")
        bad["news_impact"]["bullish_evidence"] = []
        valid = result("up")
        analyst = FakeAnalyst([json.dumps(bad), json.dumps(valid)])
        output = await analyst.analyze(context())
        self.assertTrue(output["_meta"]["schema_repair_used"])
        self.assertEqual(output["news_impact"]["direction"], "up")

    async def test_independent_weather_lng_facts_cannot_become_invented_causality(self):
        value = context()
        value["current_news"].update(title="条件情景", body="挪威设施发生意外停产；欧洲天气温和，同时LNG到港增加。")
        bad = result("flat")
        bad["analysis"]["evidence"].append("[N0]欧洲天气温和导致LNG到港增加 → TTF下行压力")
        good = without_closing_anchor(result("flat"))
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
