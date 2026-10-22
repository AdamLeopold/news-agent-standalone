import copy
import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from ttf_news.semantics import collect_analysis_semantic_errors, extract_mechanisms, prospective_supply_only, source_fact_projection, validate_analysis_semantics


def context(text, **extra):
    value = {"as_of": "2026-10-20T12:00:00Z", "analysis_kind": "manual",
             "current_news": {"id": "manual-1", "title": "条件情景", "summary": text},
             "window_news": [], "historical_events": [], "price_reference": {}}
    value.update(extra)
    return value


def analysis(direction="down", fact="欧洲实际供气流量增加40 mcm/d", mechanism="供应增加产生下行压力"):
    score = -2 if direction == "down" else 2 if direction == "up" else 0
    evidence = "[N0]" + fact + " → TTF" + mechanism
    return {"news_impact": {"direction": direction, "contextual_news_score": score,
                            "summary": "欧洲供需消息产生" + ("下行压力" if direction == "down" else "上涨压力" if direction == "up" else "有限价格压力"),
                            "bullish_evidence": [evidence] if direction == "up" else [],
                            "bearish_evidence": [evidence] if direction == "down" else [],
                            "neutralizing_evidence": []},
            "close_forecast": {"direction": direction, "summary": "依据本次新闻预判" + ("TTF下跌" if direction == "down" else "TTF上涨" if direction == "up" else "TTF基本维持"),
                               "basis": "[N0]" + mechanism},
            "analysis": {"summary": "[N0]" + fact, "evidence": [evidence],
                         "priced_in_comment": "未提供事件前后价格反应，无法确认是否已计价", "historical_comparisons": []},
            "risk_flags": [], "missing_fields": []}


class ExtractionTests(unittest.TestCase):
    def test_verb_before_supply_keeps_direction_and_negation(self):
        for verb in ['增加','提升','扩大','补充']:
            with self.subTest(verb=verb):
                signals=extract_mechanisms({'summary':f'项目未来将{verb}供应。'})
                self.assertEqual({row['pressure'] for row in signals},{'down'})
                self.assertEqual(extract_mechanisms({'summary':f'项目尚未{verb}供应。'}),[])
        self.assertEqual({row['pressure'] for row in extract_mechanisms({'summary':'The project could expand European supply.'})},{'down'})

    def test_resource_project_facts_are_prospective_without_delivery(self):
        for text in ["Vår Energi and Equinor have struck gas and condensate in Norwegian waters, packing up to 5.5 million barrels.",
                     "公司获得勘探许可证，预计未来投产。", "A discovery project signed a MOU; no actual flow measurement or production confirmation is provided.",
                     "A discovery project will begin production next year.", "A discovery project has not commenced production."]:
            with self.subTest(text=text):self.assertTrue(prospective_supply_only({"summary":text}))
        for text in ["Gas discovery project has commenced production.","Gas discovery announces first gas deliveries to Europe.",
                     "该天然气发现项目已投产。", "Gas discovery project: European actual flows increased by 40 mcm/day."]:
            with self.subTest(text=text):self.assertFalse(prospective_supply_only({"summary":text}))

    def test_actual_relief_and_cut_have_opposite_mechanisms(self):
        for word, expected in [("增加", "down"), ("减少", "up")]:
            candidates = extract_mechanisms({"summary": f"欧洲实际流量{word}40 mcm/d"})
            self.assertEqual({c["pressure"] for c in candidates}, {expected})
            self.assertEqual(candidates[0]["evidence_kind"], "actual_flow")
            self.assertEqual(candidates[0]["magnitude_mcm_d"], 40)

    def test_reversal_removes_old_outage_and_no_mild_not_invented_cold(self):
        items = extract_mechanisms({"summary": "此前outage已结束。设施复产，欧洲实际供气增加40 mcm/d。"})
        self.assertEqual({i["pressure"] for i in items}, {"down"})
        self.assertEqual(extract_mechanisms({"summary": "No outage occurred. Weather is not mild."}), [])

    def test_notice_is_conditional_capacity_not_actual_flow(self):
        item = {"source": "gassco", "title": "Gassco Troll: Unplanned unavailability (Active)",
                "summary": "Technical capacity: 135.8 mcm/d; available: 130.8; unavailable: 5.0.",
                "event_status": "Active", "temporal_state": "active_notice"}
        signals = extract_mechanisms(item)
        self.assertEqual(signals[0]["evidence_kind"], "capacity_only")
        self.assertTrue(signals[0]["conditional"])
        self.assertNotIn("magnitude_mcm_d", signals[0])
        item["event_status"] = "Dismissed"
        signals = extract_mechanisms(item)
        self.assertEqual(signals[0]["mechanism"], "capacity_risk_removed")
        self.assertEqual(signals[0]["evidence_kind"], "capacity_only")

    def test_no_flow_inference_for_zero_capacity_reduction(self):
        self.assertEqual(extract_mechanisms({"source": "gassco", "title": "Gassco plant Active", "unavailable_capacity": 0}), [])

    def test_missing_independent_flow_measurement_is_not_recovery(self):
        fact = "No independent actual flow measurement or recovery confirmation is provided."
        self.assertEqual(extract_mechanisms({"summary":fact}), [])
        signals = extract_mechanisms({"source":"gassco", "event_status":"Dismissed",
                                     "title":"Gassco test: Unplanned field unavailability (Dismissed)",
                                     "summary":fact})
        self.assertEqual([(s["mechanism"],s["evidence_kind"]) for s in signals],[("capacity_risk_removed","capacity_only")])
        self.assertTrue(signals[0]["conditional"])

    def test_forecast_actual_flow_wording_is_not_observed_flow(self):
        signals = extract_mechanisms({"summary": "预计欧洲实际流量增加40 mcm/d。"})
        self.assertEqual(signals[0]["evidence_kind"], "reported_fact")
        self.assertTrue(signals[0]["conditional"])

    def test_bounded_publisher_fact_excerpt_is_visible(self):
        signals = extract_mechanisms({"title": "Norway restart", "summary": "Operator announced restart", "fact_excerpt": "欧洲实际流量增加40 mcm/d。"})
        self.assertTrue(any(s["evidence_kind"] == "actual_flow" and s["magnitude_mcm_d"] == 40 for s in signals))

    def test_past_tense_actual_flows_keep_direction(self):
        for text, expected in [("Confirmed European actual flows rose by 40 mcm/day", "down"), ("European actual flows fell by 40 mcm/day", "up")]:
            self.assertEqual({s["pressure"] for s in extract_mechanisms({"summary": text})}, {expected})

    def test_negated_change_is_not_an_opposite_observation(self):
        for text in ["European flows did not increase", "Gas demand did not rise", "供应没有减少", "天然气需求没有增加", "库存尚未下降"]:
            with self.subTest(text=text):
                self.assertEqual(extract_mechanisms({"summary": text}), [])

    def test_restart_after_outage_does_not_count_old_outage_twice(self):
        signals = extract_mechanisms({"summary": "Production restarted after an outage. Actual European flows rose by 40 mcm/day."})
        self.assertEqual({s["pressure"] for s in signals}, {"down"})

    def test_absence_of_supply_restoration_information_is_not_supply_restoration(self):
        for text in ["本窗口首次发布，未给供应恢复或其他反向事实", "输入未提供供应恢复数据", "未提及供应中断", "没有给出温和天气证据", "No information on supply restoration", "Input does not mention gas demand fell"]:
            with self.subTest(text=text):self.assertEqual(extract_mechanisms({"summary": text}), [])

    def test_missing_other_data_does_not_erase_an_affirmed_change(self):
        signals = extract_mechanisms({"summary": "未提供流量数据，但供应中断已确认。供应实际减少40mcm/d，但未提供公告前后价格反应。"})
        self.assertEqual({s["pressure"] for s in signals}, {"up"})


class SemanticTests(unittest.TestCase):
    def assertValid(self, output, ctx, prepared=None):
        before = copy.deepcopy(output)
        validate_analysis_semantics(output, ctx, prepared or {"price_reference": ctx.get("price_reference", {})})
        self.assertEqual(output, before, "semantic checks must never silently rewrite a conclusion")

    def test_collector_reports_unknown_flow_and_reversed_supply_together(self):
        ctx=context('项目合作可能在未来扩大天然气生产。')
        notice={'id':'N3','source':'gassco','title':'Gassco Troll: Planned unavailability (Active)',
                'facts':'Technical capacity: 100 mcm/d; available: 80; unavailable: 20.',
                'event_status':'Active','temporal_state':'active_notice'}
        prepared={'current_news':{'id':'N0','source':'offshore_energy'},'window_news':[notice]}
        out=analysis('flat','项目合作意向','欧洲当前传导有限')
        out['news_impact']['bullish_evidence']=['[N0]该项目开启将增加供应 → TTF看涨机制']
        out['news_impact']['neutralizing_evidence']=['[N3]供给约束未确认流量损失 → TTF中性机制']
        before=copy.deepcopy((out,ctx,prepared))
        errors=collect_analysis_semantic_errors(out,ctx,prepared)
        self.assertTrue(any('不能作为TTF中性机制' in error for error in errors))
        self.assertTrue(any('供需机制相反' in error for error in errors))
        self.assertGreaterEqual(len(errors),2)
        with self.assertRaises(ValueError) as failure:
            validate_analysis_semantics(out,ctx,prepared)
        self.assertEqual(str(failure.exception),errors[0])
        self.assertEqual((out,ctx,prepared),before)

    def test_collector_valid_result_is_empty_and_pure(self):
        ctx=context('公司勘探许可延期，未来投资细节待定。')
        out=analysis('flat','许可延期','欧洲当前传导有限')
        prepared={'price_reference':{}}
        before=copy.deepcopy((out,ctx,prepared))
        self.assertEqual(collect_analysis_semantic_errors(out,ctx,prepared),[])
        validate_analysis_semantics(out,ctx,prepared)
        self.assertEqual((out,ctx,prepared),before)

    def test_collector_and_validator_share_acceptance_set(self):
        cases=[(analysis('down'),context('欧洲实际供气流量增加40 mcm/d。'),{}),
               (analysis('up','欧洲实际流量增加40 mcm/d','供应增加推升TTF价格'),context('欧洲实际供气流量增加40 mcm/d。'),{}),
               (analysis('flat','项目勘探事项','欧洲传导有限'),context('项目勘探许可延期。'),{})]
        for out,ctx,prepared in cases:
            before=copy.deepcopy((out,ctx,prepared))
            errors=collect_analysis_semantic_errors(out,ctx,prepared)
            try:
                validate_analysis_semantics(out,ctx,prepared)
            except ValueError as exc:
                self.assertTrue(errors)
                self.assertEqual(str(exc),errors[0])
            else:
                self.assertEqual(errors,[])
            self.assertEqual((out,ctx,prepared),before)

    def test_rejects_supply_relief_bullish_audit_failure(self):
        ctx = context("挪威处理厂提前恢复，欧洲实际供气增加40 mcm/d，计量已确认。")
        output = analysis("up", "欧洲实际供气增加40 mcm/d", "供应宽松，推升价格")
        with self.assertRaisesRegex(ValueError, "机制相反|传导方向相反"):
            validate_analysis_semantics(output, ctx, {})

    def test_rejects_supply_cut_bearish_and_wrong_bucket(self):
        ctx = context("欧洲实际供气减少40 mcm/d，运营方已确认。")
        bad = analysis("down", "欧洲实际供气减少40 mcm/d", "供应约束导致TTF上涨")
        with self.assertRaisesRegex(ValueError, "证据栏"):
            validate_analysis_semantics(bad, ctx, {})

    def test_good_relief_and_disruption_pass(self):
        self.assertValid(analysis(), context("欧洲实际供气流量增加40 mcm/d，运营方已确认。"))
        self.assertValid(analysis("up", "欧洲实际供气减少40 mcm/d", "供应受限产生上涨压力"),
                         context("欧洲实际供气减少40 mcm/d，运营方已确认。"))

    def test_large_one_sided_flow_cannot_flatten_because_quotes_missing(self):
        ctx = context("欧洲实际流量减少40 mcm/d。")
        out = analysis("flat", "欧洲实际流量减少40 mcm/d", "供应约束产生上涨压力")
        out["news_impact"]["bullish_evidence"] = ["[N0]欧洲实际流量减少40 mcm/d → TTF供应受限产生上涨压力"]
        out["news_impact"]["neutralizing_evidence"] = ["price_reference:未提供历史资料和盘中行情"]
        with self.assertRaisesRegex(ValueError, "实际流量单向变化"):
            validate_analysis_semantics(out, ctx, {})

    def test_old_window_flow_shock_is_background_not_forced_today(self):
        ctx = context("欧洲实际流量减少40 mcm/d。", session={"window_start": "2026-10-19T15:15:00Z"})
        ctx["current_news"]["published_at"] = "2026-10-18T10:00:00Z"
        out = analysis("flat", "旧窗口报道欧洲流量下降", "旧消息不等于今日新增冲击")
        self.assertValid(out, ctx)

    def test_stale_background_cannot_offset_fresh_current_recovery(self):
        for previous in [
            {"summary": "欧洲实际流量减少40 mcm/d。", "published_at": "2026-10-18T10:00:00Z"},
            {"summary": "欧洲寒潮，采暖需求增加。", "published_at": "2026-10-18T10:00:00Z"},
            {"summary": "欧洲实际流量减少40 mcm/d。", "published_at": "2026-10-20T08:00:00Z", "event_end": "2026-10-20T10:00:00Z"},
        ]:
            ctx = context("欧洲实际流量增加40 mcm/d，计量确认恢复。", session={"window_start": "2026-10-19T15:15:00Z"}, window_news=[previous])
            ctx["current_news"]["published_at"] = "2026-10-20T11:30:00Z"
            out = analysis("flat", "欧洲实际流量增加40 mcm/d", "缺少行情无法量化")
            with self.subTest(previous=previous), self.assertRaisesRegex(ValueError, "实际流量单向变化"):
                validate_analysis_semantics(out, ctx, {})

    def test_elapsed_cut_cannot_force_net_but_confirmed_current_restoration_can(self):
        ctx = context("欧洲实际流量减少40 mcm/d。")
        ctx["current_news"].update(event_end="2026-10-20T10:00:00Z", temporal_state="announced_end_elapsed_unverified_flow")
        out = analysis("flat", "已过公告预计时段", "实际流量恢复仍需核实")
        self.assertValid(out, ctx)
        ctx["current_news"]["summary"] = "欧洲实际流量增加40 mcm/d，计量确认提前恢复。"
        wrong = analysis("flat", "欧洲实际流量已增加40 mcm/d", "缺行情无法量化幅度")
        with self.assertRaisesRegex(ValueError, "实际流量单向变化"):
            validate_analysis_semantics(wrong, ctx, {})

    def test_measured_priced_in_evidence_can_support_flat_even_for_flow_change(self):
        ctx = context("欧洲实际流量增加40 mcm/d，运营方已确认。")
        out = analysis("flat", "欧洲实际流量增加40 mcm/d", "所给反应资料表明变化已被市场部分计入")
        prepared = {"price_reference": {"priced_in_evidence": {"observed_reaction": 0, "source": "known-price-snapshot"}}}
        self.assertValid(out, ctx, prepared)

    def test_supplied_mixed_supply_weather_can_net_flat(self):
        ctx = context("欧洲实际流量减少40 mcm/d。同时欧洲天气温和，采暖需求减少，LNG到港增加。")
        out = analysis("flat", "供应约束与需求缓解并存", "看涨压力被部分抵消")
        out["news_impact"]["bullish_evidence"] = ["[N0]欧洲实际流量减少40 mcm/d → TTF供应约束产生上涨压力"]
        out["news_impact"]["bearish_evidence"] = ["[N0]欧洲天气温和、采暖需求减少 → TTF下行压力"]
        out["analysis"]["evidence"] = out["news_impact"]["bullish_evidence"] + out["news_impact"]["bearish_evidence"]
        self.assertValid(out, ctx)

    def test_future_capacity_cannot_force_a_final_direction(self):
        news = {"id": "notice", "title": "Gassco Kollsnes Planned unavailability Active",
                "source": "gassco", "summary": "Technical capacity: 156; available: 120; unavailable: 36; event in 2027.",
                "event_status": "Active", "temporal_state": "scheduled"}
        ctx = context("", current_news=news)
        out = analysis("flat", "公告预定明年维护", "可能形成远期供应风险")
        self.assertValid(out, ctx)

    def test_same_event_in_history_is_not_priced_in_evidence(self):
        ctx = context("欧洲实际流量增加40 mcm/d。", historical_events=[{"news_ids": ["manual-1"]}])
        for section, field in [("news_impact", "summary"), ("close_forecast", "basis"), ("analysis", "priced_in_comment")]:
            out = analysis()
            out[section][field] = "公告已在历史库出现，市场已经消化这条消息"
            with self.subTest(section=section), self.assertRaisesRegex(ValueError, "已计价/已消化"):
                validate_analysis_semantics(out, ctx, {})

    def test_after_the_fact_uncertainty_does_not_erase_priced_in_assertion(self):
        out = analysis()
        out["news_impact"]["neutralizing_evidence"] = ["[N0]消息已计价，无法确认进一步影响"]
        with self.assertRaisesRegex(ValueError, "已计价/已消化"):
            validate_analysis_semantics(out, context("欧洲实际流量增加40 mcm/d。"), {})

    def test_priced_in_negative_and_conditional_are_valid(self):
        for sentence in ["无法确认该消息是否已被市场计入价格", "入库不等于市场已消化", "若消息已计价，则新增边际影响可能减少", "未见已计价证据", "没有已计价证据", "未提供该消息已计价的依据"]:
            out = analysis()
            out["analysis"]["priced_in_comment"] = sentence
            with self.subTest(sentence=sentence):self.assertValid(out, context("欧洲实际流量增加40 mcm/d。"))

    def test_priced_in_can_use_explicit_reaction_evidence(self):
        out = analysis()
        out["analysis"]["priced_in_comment"] = "公告后价格反应显示已部分计入"
        self.assertValid(out, context("欧洲实际流量增加40 mcm/d。"), {"price_reference": {"priced_in_evidence": {"source": "price-event-study", "known_at": "2026-10-20T11:00:00Z"}}})

    def test_missing_data_cannot_become_mild_weather_stable_storage_or_no_loss(self):
        for statement in ["欧洲天气温和，采暖需求减少", "欧洲库存稳定", "LNG到港保持稳定", "公告维护未导致实际流量损失"]:
            out = analysis()
            out["news_impact"]["neutralizing_evidence"] = ["[N0]" + statement]
            with self.subTest(statement=statement), self.assertRaisesRegex(ValueError, "输入未提供"):
                validate_analysis_semantics(out, context("欧洲实际流量增加40 mcm/d。"), {})

    def test_missing_data_not_missing_world_events(self):
        out = analysis()
        out["close_forecast"]["basis"] = "本窗口无供需冲击"
        with self.assertRaisesRegex(ValueError, "覆盖不足"):
            validate_analysis_semantics(out, context("一家公司获牙买加长期勘探许可证，尚未钻探。"), {})
        out["close_forecast"]["basis"] = "输入新闻未显示即时欧洲供需冲击"
        self.assertValid(out, context("欧洲实际流量增加40 mcm/d。"))

    def test_unknown_data_conditions_remain_conditions(self):
        out = analysis()
        out["detail"] = {"scenarios": {"bearish": "若欧洲天气温和、采暖需求减少，TTF下行压力增强", "bullish": "若寒潮使需求增加，TTF上涨压力增强"}}
        self.assertValid(out, context("欧洲实际流量增加40 mcm/d。"))

    def test_real_parser_detail_warm_demand_fall_cannot_be_bullish(self):
        out=analysis()
        out["detail"]={"scenarios":{"bullish":"若出现欧洲库存显著增加或天气暖冬导致需求下降，可能抵消供应增加的下行压力"}}
        original=copy.deepcopy(out)
        with self.assertRaisesRegex(ValueError,"情景名称|同向"):
            validate_analysis_semantics(out,context("欧洲实际流量增加40 mcm/d，计量确认恢复。"),{})
        self.assertEqual(out,original)

    def test_same_parser_error_in_invalidation_condition_is_rejected(self):
        out=analysis()
        out["detail"]={"invalidation_conditions":["[N0]若出现欧洲库存显著增加或天气暖冬导致需求下降，可能抵消供应增加的下行压力"]}
        with self.assertRaisesRegex(ValueError,"同向"):
            validate_analysis_semantics(out,context("欧洲实际流量增加40 mcm/d。"),{})

    def test_single_direction_scenarios_cannot_invert_mechanism_without_price_words(self):
        for role,text in (("bullish","若欧洲天然气供应增加"),("bullish","若采暖需求下降"),("bullish","若库存增加"),
                          ("bearish","若欧洲天然气供应减少"),("bearish","若采暖需求增加"),("bearish","若库存下降")):
            out=analysis();out["detail"]={"scenarios":{role:text}}
            with self.subTest(role=role,text=text),self.assertRaisesRegex(ValueError,"情景名称"):
                validate_analysis_semantics(out,context("欧洲实际流量增加40 mcm/d。"),{})

    def test_opposite_offset_conditions_and_correct_scenarios_remain_valid(self):
        out=analysis()
        out["detail"]={"scenarios":{"bullish":"若寒潮导致采暖需求增加，可抵消供应增加的下行压力", "bearish":"若供应增加持续，TTF下行压力增强"},
                       "invalidation_conditions":["[N0]若欧洲供应减少，可抵消当前供应增加的下行压力"]}
        self.assertValid(out,context("欧洲实际流量增加40 mcm/d。"))

    def test_same_direction_passive_offset_is_rejected(self):
        for text in ("若库存增加，供应增加的下行压力被抵消", "若采暖需求增加，价格上涨压力减弱"):
            out=analysis();out["detail"]={"invalidation_conditions":[text]}
            with self.subTest(text=text),self.assertRaisesRegex(ValueError,"同向"):
                validate_analysis_semantics(out,context("欧洲实际流量增加40 mcm/d。"),{})

    def test_mixed_ambiguous_and_pure_conditions_are_not_forced_to_one_sign(self):
        for role,text in (("bullish","若供应增加但需求也增加，净影响仍需权衡"),
                          ("bearish","若供应减少但采暖需求也下降，方向取决于幅度"),
                          ("bullish","若新的反向信息出现，当前判断需要复核"),
                          ("bullish","若暖冬结束，价格或面临变化")):
            out=analysis();out["detail"]={"scenarios":{role:text}}
            with self.subTest(role=role,text=text):self.assertValid(out,context("欧洲实际流量增加40 mcm/d。"))

    def test_negative_offset_statement_does_not_assert_opposite_effect(self):
        out=analysis();out["detail"]={"invalidation_conditions":["若库存增加，不能抵消供应增加的下行压力"]}
        self.assertValid(out,context("欧洲实际流量增加40 mcm/d。"))

    def test_clear_reversal_of_same_price_sign_is_rejected(self):
        out=analysis();out["detail"]={"invalidation_conditions":["若供应增加，使当前下跌判断失效"]}
        with self.assertRaisesRegex(ValueError,"同向"):
            validate_analysis_semantics(out,context("欧洲实际流量增加40 mcm/d。"),{})

    def test_physical_supply_gap_relief_is_not_a_price_pressure_offset_operator(self):
        for text in ("若[N0]实际供气流量恢复且供应缺口缓解 → TTF下行压力增强",
                     "若[N0]实际供气流量恢复且供应缺口缓解并且库存增加，TTF价格下降",
                     "若[N0]供应增加缓解供应缺口且库存增加，TTF下行压力增强"):
            out=analysis();out["detail"]={"scenarios":{"bearish":text}}
            with self.subTest(text=text):self.assertValid(out,context("欧洲实际流量增加40 mcm/d。"))

    def test_expectation_qualifiers_leave_direction_open(self):
        for role,text in (("bullish","若供应恢复不及预期，价格可能反弹"),
                          ("bullish","若供应增加不足以补足此前缺口，TTF仍有上涨风险"),
                          ("bearish","若库存下降但降幅低于预期，价格可能下跌"),
                          ("bullish","若供给恢复已被充分计价，价格可能反弹")):
            out=analysis();out["detail"]={"scenarios":{role:text}}
            with self.subTest(role=role,text=text):self.assertValid(out,context("欧洲实际流量增加40 mcm/d。"))

    def test_weaker_upward_pressure_is_a_valid_bearish_recovery_mechanism(self):
        out = analysis()
        out["detail"] = {"scenarios": {"bearish": "若[N0]设施复产、供给恢复 → TTF上涨压力减弱"}}
        self.assertValid(out, context("欧洲实际流量增加40 mcm/d。"))
        out["detail"]["scenarios"]["bearish"] = "若[N0]供给恢复 → 缓解TTF上涨压力"
        self.assertValid(out, context("欧洲实际流量增加40 mcm/d。"))

    def test_weaker_downward_pressure_is_bullish_and_not_a_bearish_bucket(self):
        out = analysis("up", "欧洲需求增加", "TTF下行压力减弱")
        self.assertValid(out, context("欧洲天然气需求增加。"))
        wrong = analysis("down", "欧洲实际供气减少40 mcm/d", "TTF下行压力减弱")
        with self.assertRaises(ValueError):
            validate_analysis_semantics(wrong, context("欧洲实际供气减少40 mcm/d。"), {})

    def test_supplied_current_weather_supports_claim_but_historical_weather_does_not(self):
        out = analysis()
        out["news_impact"]["neutralizing_evidence"] = ["[N0]欧洲天气温和 → TTF下行压力"]
        self.assertValid(out, context("欧洲实际流量增加40 mcm/d。欧洲天气温和。"))
        ctx = context("欧洲实际流量增加40 mcm/d。", historical_events=[{"facts": "2022 年欧洲天气温和"}])
        with self.assertRaisesRegex(ValueError, "天气"):
            validate_analysis_semantics(out, ctx, {})

    def test_rejects_hollow_schema_fact_even_with_correct_direction(self):
        out = analysis()
        out["analysis"]["evidence"] = ["[N0]输入事实 → TTF供需或价格传导"]
        with self.assertRaisesRegex(ValueError, "占位事实"):
            validate_analysis_semantics(out, context("欧洲实际流量增加40 mcm/d。"), {})

    def test_summary_direction_and_enum_must_agree(self):
        out = analysis()
        out["close_forecast"]["summary"] = "预计TTF结算价上涨"
        with self.assertRaisesRegex(ValueError, "文字价格方向"):
            validate_analysis_semantics(out, context("欧洲实际流量增加40 mcm/d。"), {})

    def test_news_pressure_can_differ_from_close_with_price_momentum(self):
        out = analysis()
        out["close_forecast"].update(direction="up", summary="盘中价仍高于前一参考价，预计TTF结算上涨", basis="供应恢复产生下行压力，但盘中上行惯性尚未逆转")
        prepared = {"price_reference": {"market_baseline": {"available": True, "observed_direction": "up", "observed_delta": 1.5}}}
        self.assertValid(out, context("欧洲实际流量增加40 mcm/d。"), prepared)

    def test_insufficient_evidence_is_not_forced_flat(self):
        out = analysis("flat", "牙买加许可证", "暂未形成即时欧洲传导")
        out["close_forecast"].update(direction=None, evidence_status="insufficient_evidence", summary="现有新闻不足以形成收盘方向", basis="price_reference:缺少可用当日基准")
        self.assertValid(out, context("牙买加长期勘探许可证，尚未钻探，无欧洲供气计划。"))

    def test_mild_weather_cannot_raise_heating_demand(self):
        out = analysis("flat", "天气温和", "天气影响需结合需求")
        out["analysis"]["evidence"] = ["[N0]天气温和导致采暖需求增加 → TTF上涨压力"]
        with self.assertRaisesRegex(ValueError, "温和天气通常降低"):
            validate_analysis_semantics(out, context("欧洲天气温和。"), {})

    def test_supplied_cooling_mechanism_can_raise_demand(self):
        out = analysis("up", "夏季高温推动制冷发电用气需求增加", "需求增加产生上涨压力")
        self.assertValid(out, context("欧洲夏季高温推动制冷发电用气需求增加。"))

    def test_uncertain_input_weather_is_not_support_for_actual_mild_claim(self):
        out = analysis("flat", "牙买加许可证", "欧洲传导有限")
        out["news_impact"]["neutralizing_evidence"] = ["[N0]欧洲天气温和 → TTF下行压力"]
        with self.assertRaisesRegex(ValueError, "天气"):
            validate_analysis_semantics(out, context("牙买加许可证。未知欧洲天气温和与否。"), {})

    def test_future_unavailable_weather_cannot_offset_current_actual_flow(self):
        out = analysis("flat", "欧洲实际流量减少40 mcm/d", "供应风险带来上涨压力")
        ctx = context("欧洲实际流量减少40 mcm/d。", window_news=[{"title": "weather", "summary": "欧洲天气温和、采暖需求减少", "published_at": "2026-10-21T12:00:00Z"}])
        with self.assertRaisesRegex(ValueError, "实际流量单向变化"):
            validate_analysis_semantics(out, ctx, {})

    def test_future_revision_and_availability_clocks_cannot_supply_offset(self):
        for clock in ["updated_at", "revised_at", "revision_available_at", "discovered_at", "collected_at", "available_at", "known_at", "version_seen_at", "first_seen_at", "fact_excerpt_created_at"]:
            out = analysis("flat", "欧洲实际流量减少40 mcm/d", "供应风险带来上涨压力")
            news = {"title": "weather", "summary": "欧洲天气温和、采暖需求减少", "published_at": "2026-10-19T10:00:00Z", "first_seen_at": "2026-10-19T10:01:00Z", clock: "2026-10-21T12:00:00Z"}
            ctx = context("欧洲实际流量减少40 mcm/d。", window_news=[news])
            with self.subTest(clock=clock), self.assertRaisesRegex(ValueError, "实际流量单向变化"):
                validate_analysis_semantics(out, ctx, {})

    def test_future_revised_focus_cannot_force_today_net(self):
        ctx = context("欧洲实际流量减少40 mcm/d。")
        ctx["current_news"].update(published_at="2026-10-19T12:00:00Z", first_seen_at="2026-10-19T12:01:00Z", updated_at="2026-10-21T00:00:00Z")
        self.assertValid(analysis("flat", "现有资料未提供当前变化", "新闻覆盖不足以确定新压力"), ctx, {"current_news": {}, "window_news": []})

    def test_newly_fetched_excerpt_cannot_be_read_as_old_available_evidence(self):
        ctx = context("旧摘要只提及一次长期项目。")
        ctx["current_news"].update(published_at="2026-10-19T12:00:00Z", first_seen_at="2026-10-19T12:01:00Z", fact_excerpt="欧洲实际流量减少40 mcm/d。", fact_excerpt_created_at="2026-10-21T00:00:00Z")
        self.assertValid(analysis("flat", "旧摘要没有即时欧洲传导", "缺少当前事实"), ctx, {"current_news": {}, "window_news": []})

    def test_local_date_publication_near_eu_midnight_remains_known(self):
        ctx = context("欧洲实际流量减少40 mcm/d。", as_of="2026-10-20T23:30:00Z")
        ctx["current_news"].update(source="eu_energy", published_date="2026-10-21", first_seen_at="2026-10-20T23:20:00Z")
        with self.assertRaisesRegex(ValueError, "实际流量单向变化"):
            validate_analysis_semantics(analysis("flat", "欧洲流量减少40", "资料不足"), ctx, {})

    def test_fact_direction_cannot_be_invented_under_a_valid_alias(self):
        out = analysis("up", "欧洲实际流量减少40 mcm/d", "供应约束产生上涨压力")
        with self.assertRaisesRegex(ValueError, "引用来源"):
            validate_analysis_semantics(out, context("欧洲实际流量增加40 mcm/d。"), {})

    def test_duplicate_window_alias_resolves_to_same_current_fact(self):
        out = analysis()
        out["news_impact"]["bearish_evidence"] = ["[N1]欧洲实际流量增加40 mcm/d → TTF下行压力"]
        prepared = {"window_news": [{"id": "N1", "same_as_current_news_id": "N0"}]}
        self.assertValid(out, context("欧洲实际流量增加40 mcm/d。"), prepared)

    def test_flat_news_cannot_erase_fresh_observed_upward_price_path(self):
        out = analysis("flat", "尚未钻探的牙买加许可证", "即时欧洲传导有限")
        prepared = {"price_reference": {"market_baseline": {"available": True, "observed_direction": "up", "observed_delta": 1.5}}}
        with self.assertRaisesRegex(ValueError, "已观察同合约价格"):
            validate_analysis_semantics(out, context("牙买加许可证尚未钻探，无欧洲供气计划。"), prepared)
        out["close_forecast"].update(direction="up", summary="盘中价高于前一参考价，预计TTF上涨", basis="盘中价上涨1.5，新闻未提供足以逆转的即时事实")
        self.assertValid(out, context("牙买加许可证尚未钻探，无欧洲供气计划。"), prepared)

    def test_sourced_relief_can_support_pullback_of_observed_upward_path(self):
        out = analysis()
        out["close_forecast"].update(direction="flat", summary="预计TTF基本维持", basis="[N0]供应恢复产生下行压力，可能抵消盘中涨幅并引发回吐")
        prepared = {"price_reference": {"market_baseline": {"available": True, "observed_direction": "up", "observed_delta": 1.5}}}
        self.assertValid(out, context("欧洲实际流量增加40 mcm/d。"), prepared)

    def test_observed_path_is_not_a_hardcoded_close_forecast(self):
        out = analysis("flat", "牙买加许可证", "传导有限")
        out["close_forecast"].update(direction=None, evidence_status="insufficient_evidence", summary="不足以判断结算方向", basis="无法确定盘中变化在结算前延续或反转")
        prepared = {"price_reference": {"market_baseline": {"available": True, "observed_direction": "up", "observed_delta": 1.5}}}
        ctx = context("牙买加长期勘探许可证。")
        ctx['window_news'] = [{'id':'inventory-1','source':'eia','summary':'欧洲库存增加。'}]
        prepared['window_news'] = [{'id':'N1','source':'eia','facts':'欧洲库存增加。'}]
        out['close_forecast']['basis'] = '[N1]库存增加形成下行压力，与已观察上行基准存在冲突，资料不足确定何者占优'
        self.assertValid(out, ctx, prepared)

    def test_empty_sanitized_price_never_falls_back_to_future_raw_baseline(self):
        out = analysis("flat", "牙买加许可证", "欧洲传导有限")
        ctx = context("牙买加许可证。", price_reference={"market_baseline": {"available": True, "observed_direction": "up", "observed_delta": 3, "available_at": "2026-10-21T12:00:00Z"}})
        for filtered in [{}, None]:
            with self.subTest(filtered=filtered):self.assertValid(out, ctx, {"price_reference": filtered})

    def test_filtered_future_reaction_cannot_disable_actual_flow_invariant(self):
        ctx = context("欧洲实际流量减少40 mcm/d。", price_reference={"priced_in_evidence": {"available_at": "2026-10-21T12:00:00Z"}})
        out = analysis("flat", "欧洲流量下降", "缺行情无法量化")
        with self.assertRaisesRegex(ValueError, "实际流量单向变化"):
            validate_analysis_semantics(out, ctx, {"price_reference": None})

    def test_available_baseline_cannot_be_claimed_absent_even_when_abstaining(self):
        prepared = {"price_reference": {"market_baseline": {"available": True, "observed_direction": "up", "observed_delta": 1.5}}}
        for sentence in ["缺乏可用市场基准", "未提供盘中报价和事件前后反应", "市场基准数据不可用", "没有当日价格基准"]:
            out = analysis("flat", "牙买加许可证", "传导有限")
            out["close_forecast"].update(direction=None, evidence_status="insufficient_evidence", summary="信息不足以判断收盘", basis=sentence)
            with self.subTest(sentence=sentence), self.assertRaisesRegex(ValueError, "已提供"):
                validate_analysis_semantics(out, context("牙买加许可证。"), prepared)

    def test_available_nonmaterial_baseline_still_allows_real_limits_and_insufficient_status(self):
        prepared = {"price_reference": {"market_baseline": {"available": True, "observed_direction": "up", "observed_delta": 0.2}}}
        for sentence in ["已有盘中价格但缺乏连续数据，无法确定涨幅延续性", "现有市场基准可用，但缺少官方结算资料", "缺少实时盘中报价，当前仅有延迟参考"]:
            out = analysis("flat", "牙买加许可证", "传导有限")
            out["close_forecast"].update(direction=None, evidence_status="insufficient_evidence", summary="现有证据不足以判断收盘方向", basis=sentence)
            with self.subTest(sentence=sentence):self.assertValid(out, context("牙买加许可证。"), prepared)

    def test_available_reference_cannot_be_listed_wholly_missing(self):
        out = analysis("flat", "牙买加许可证", "欧洲传导有限")
        out["close_forecast"].update(direction=None, evidence_status="insufficient_evidence", summary="现有证据不足", basis="盘中变化延续性不清楚")
        out["missing_fields"] = ["price_reference.market_baseline"]
        with self.assertRaisesRegex(ValueError, "missing_fields"):
            validate_analysis_semantics(out, context("牙买加许可证。"), {"price_reference": {"market_baseline": {"available": True}}})

    def test_valid_anchor_directional_news_cannot_abstain_only_for_unknown_pricing(self):
        out=analysis('down')
        out['close_forecast'].update(evidence_status='insufficient_evidence',direction=None,
            summary='缺乏价格反应证据，无法确定是否已计入该供应增加的影响，故对目标结算相对前一结算的方向缺乏足够依据。',
            basis='TTF参考价格未变，且无已知价格反应或逆向消息。')
        ctx=context('欧洲实际供气流量增加40 mcm/d。')
        baseline={'price_reference':{'market_baseline':{'available':True,'contract':'Nov26','previous_price':88.04,'latest_price':88.04,'observed_delta':0,'observed_direction':'flat'}}}
        with self.assertRaisesRegex(ValueError,'不能单独作为拒绝收盘预判'):
            validate_analysis_semantics(out,ctx,baseline)
        self.assertValid(out,ctx)  # No anchor: original no-quote controls remain.
        out['close_forecast'].update(direction='down',evidence_status='forecast',summary='若无反向冲击，供应增加对平稳基准带来下行压力',basis='当前基准持平，欧洲供气增加形成下行压力')
        self.assertValid(out,ctx,baseline)

    def test_real_sourced_conflict_allows_insufficient_close_with_valid_anchor(self):
        ctx=context('欧洲实际供气流量增加40 mcm/d。')
        ctx['window_news']=[{'title':'欧洲库存更新','summary':'欧洲库存下降，库存消耗增加。'}]
        out=analysis('down')
        out['close_forecast'].update(direction=None,evidence_status='insufficient_evidence',
            summary='供应增加带来下行压力，但库存下降形成上行压力，反向机制存在冲突且权重不明。',
            basis='缺乏公告前后价格反应，供应增加与库存下降的抵消程度无法确定。')
        baseline={'price_reference':{'market_baseline':{'available':True,'observed_delta':0,'observed_direction':'flat'}}}
        self.assertValid(out,ctx,baseline)

    def test_scopeguard_no_reaction_and_unable_to_judge_pricing_cannot_cancel_known_anchor(self):
        ctx=context('运营方计量确认欧洲实际天然气流量增加40 mcm/d。')
        baseline={'price_reference':{'market_baseline':{'available':True,'observed_delta':0,'observed_direction':'flat'}}}
        out=analysis('down')
        out['close_forecast'].update(direction=None,evidence_status='insufficient_evidence',
            summary='基于已观察的基准平稳且缺少明确价格反应证据，缺乏足够证据判断是否已计价，故无法确定目标结算相对前一结算的方向。',
            basis='市场基准观察显示同合约参考变化为flat，且无价格反应证据，故无法判断是否已计价。')
        before=copy.deepcopy(out)
        with self.assertRaisesRegex(ValueError,'不能单独作为拒绝收盘预判'):
            validate_analysis_semantics(out,ctx,baseline)
        self.assertTrue(any('不能单独作为拒绝收盘预判' in error for error in collect_analysis_semantic_errors(out,ctx,baseline)))
        self.assertEqual(out,before)
        self.assertValid(out,ctx)  # Without a valid anchor null is still valid.
        weak=analysis('flat','项目行政事项','欧洲传导有限')
        weak['close_forecast']=copy.deepcopy(out['close_forecast'])
        self.assertValid(weak,context('公司行政许可证延期，欧洲传导有限。'),baseline)
        ctx['window_news']=[{'title':'欧洲库存更新','summary':'欧洲库存下降。'}]
        out['close_forecast']['basis']='无价格反应证据，供应增加与库存下降的反向机制存在冲突、权重未知。'
        self.assertValid(out,ctx,baseline)

    def test_same_unknown_pricing_abstention_rule_applies_to_supply_cut(self):
        ctx=context('欧洲实际供气流量减少40 mcm/d。')
        out=analysis('up','欧洲实际供气流量减少40 mcm/d','供应减少带来上行压力')
        out['close_forecast'].update(direction=None,evidence_status='insufficient_evidence',
            summary='缺乏价格反应证据，无法确定是否已计入该供应减少的影响。',
            basis='同合约基准持平，但不知道是否已计价且无逆向消息。')
        with self.assertRaisesRegex(ValueError,'不能单独作为拒绝收盘预判'):
            validate_analysis_semantics(out,ctx,{'price_reference':{'market_baseline':{'available':True,'observed_delta':0,'observed_direction':'flat'}}})
        self.assertValid(out,ctx)

    def test_supplied_actual_flow_quantity_is_not_missing_quantification(self):
        ctx=context('挪威处理厂提前复产，欧洲实际供气流量增加40 mcm/d。')
        out=analysis('down')
        out['detail']={'limitations':['current_news:未提供实际流量量化资料']}
        with self.assertRaisesRegex(ValueError,'已明确提供实际流量变化的量化原值'):
            validate_analysis_semantics(out,ctx,{})
        for limitation in ['current_news:未提供独立核验的实际流量量化资料','current_news:实际流量40mcm/d来自手动条件，缺少独立验证','current_news:缺少后续实际流量定量序列']:
            out['detail']['limitations']=[limitation]
            with self.subTest(limitation=limitation):self.assertValid(out,ctx)
        capacity=self.notice_context()
        out=self.notice_output('当前容量公告提供潜在供给风险')
        out['detail']={'limitations':['current_news:未提供实际流量量化资料']}
        self.assertValid(out,capacity)

    def notice_context(self, status="Active", state="active_notice", **extra):
        row = {"id": "notice", "source": "gassco", "title": f"Gassco Troll: Unplanned unavailability ({status})",
               "summary": "Technical capacity: 135.8 mcm/d; available: 130.8; unavailable: 5.0.",
               "event_status": status, "temporal_state": state, "published_at": "2026-10-20T11:00:00Z"}
        row.update(extra)
        return context("", current_news=row)

    def notice_output(self, claim):
        out = analysis("flat", "Gassco公告列出容量限制", "容量公告仅提供潜在价格影响")
        out["news_impact"]["summary"] = claim
        return out

    def test_future_active_notice_is_not_current_actual_flow_loss(self):
        ctx = self.notice_context(state="scheduled_notice", event_start="2027-05-31T06:00:00+0200")
        for claim in ["[N0]当前供应已减少36mcm/d", "[N0]实际供气已减少36mcm/d", "[N0]当前设施停产"]:
            with self.subTest(claim=claim), self.assertRaisesRegex(ValueError, "未来维护公告"):
                validate_analysis_semantics(self.notice_output(claim), ctx, {})

    def test_current_active_capacity_is_not_measured_actual_flow_loss(self):
        ctx = self.notice_context()
        with self.assertRaisesRegex(ValueError, "独立计量/实际流量"):
            validate_analysis_semantics(self.notice_output("[N0]实际供气减少5mcm/d"), ctx, {})
        self.assertValid(self.notice_output("[N0]可用容量受限5mcm/d，影响取决于实际流量"), ctx)

    def test_dismissed_cancelled_or_end_does_not_prove_restoration(self):
        for ctx in [self.notice_context("Dismissed", "dismissed_notice"),
                    self.notice_context("Cancelled", "dismissed_notice"),
                    self.notice_context("Active", "announced_end_elapsed_unverified_flow", event_end="2026-10-20T10:00:00Z")]:
            for claim in ["[N0]公告已撤销，实际流量已恢复", "[N0]设施已经复产", "[N0]公告时段结束，已经恢复生产"]:
                with self.subTest(status=ctx["current_news"]["event_status"],claim=claim), self.assertRaisesRegex(ValueError, "不能证明实际流量恢复"):
                    validate_analysis_semantics(self.notice_output(claim), ctx, {})

    def test_notice_unknown_and_conditional_recovery_remain_valid(self):
        ctx = self.notice_context("Dismissed", "dismissed_notice")
        for claim in ["[N0]公告已撤销，实际流量恢复待核实", "[N0]若实际流量已恢复，则供应缺口缓解", "[N0]公告结束不代表实际流量已恢复", "[N0]无法确认设施已经复产", "[N0]非计划停机公告时段结束 → TTF残余压力取决于实际流量恢复", "[N0]残余压力取决于是否实际流量已恢复"]:
            with self.subTest(claim=claim):self.assertValid(self.notice_output(claim), ctx)
        out = self.notice_output("[N0]撤销公告仅减少容量风险，实际恢复待核实")
        out["detail"] = {"scenarios": {"bearish": "设施已复产、供给恢复时，价格下行压力增强"}}
        self.assertValid(out, ctx)

    def test_dismissed_notice_cannot_eliminate_unqualified_supply_risk(self):
        ctx=self.notice_context('Dismissed','dismissed_notice',fact_excerpt='No independent actual flow measurement or recovery confirmation is provided.')
        for claim in ['公告已撤销，未确认实际供气恢复，供应风险已消除，价格可能略有下行。',
                      '[N0]公告已撤销，供应风险已消除 → TTF看跌机制',
                      '公告已撤销，供应风险已经完全解除，但实际恢复仍未知。',
                      '[N0]公告撤销，完全消除了供应风险。']:
            out=self.notice_output(claim)
            before=copy.deepcopy(out)
            with self.subTest(claim=claim),self.assertRaisesRegex(ValueError,'撤销仅限该公告约束'):
                validate_analysis_semantics(out,ctx,{})
            self.assertEqual(out,before)
            self.assertTrue(any('撤销仅限该公告约束' in message for message in collect_analysis_semantic_errors(out,ctx,{})))
        # The observed model repeated this unsupported assertion across fields.
        out=self.notice_output('公告已撤销，未确认实际供气恢复，供应风险已消除，价格可能略有下行。')
        out['close_forecast']['basis']='公告已撤销，供应风险已消除，但未确认实际供气恢复，缺乏价格基准。'
        out['analysis']['evidence']=['[N0]公告已撤销，供应风险已消除 → TTF供需或价格传导']
        self.assertTrue(any('撤销仅限该公告约束' in error for error in collect_analysis_semantic_errors(out,ctx,{})))

    def test_dismissed_announcement_risk_scope_conditions_and_independent_facts_remain_valid(self):
        ctx=self.notice_context('Dismissed','dismissed_notice')
        for claim in ['[N0]该公告的供应风险已消除，实际流量恢复待核实。',
                      '[N0]公告容量风险已消除，整体供气状态仍未知。',
                      '[N0]所述约束的供应风险已解除，实际恢复待核实。',
                      '[N0]公告已撤销，供应风险缓解，实际恢复待核实。',
                      '[N0]公告撤销不代表供应风险已消除。',
                      '[N0]无法确认供应风险已消除。',
                      '[N0]若供应风险已消除，则下行压力可能增强。',
                      '[N0]供应风险已消除与否仍待核实。']:
            with self.subTest(claim=claim):self.assertValid(self.notice_output(claim),ctx)
        confirmed=self.notice_context('Dismissed','dismissed_notice',fact_excerpt='独立计量确认Troll实际流量完全恢复，Troll供应风险已消除。')
        self.assertValid(self.notice_output('[N0]Troll供应风险已消除。'),confirmed)
        with self.assertRaisesRegex(ValueError,'整体供应风险'):
            validate_analysis_semantics(self.notice_output('[N0]欧洲整体供应风险已消除。'),confirmed,{})
        # Current notices and non-notice corporate news are outside this gate.
        self.assertValid(self.notice_output('供应风险已消除。'),self.notice_context())
        self.assertValid(self.notice_output('供应风险已消除。'),context('企业行政许可事项。'))

    def test_dismissed_fact_projection_limits_risk_removal_without_direction(self):
        rows=[{'id':'N0','state':'dismissed_notice','actual_flow_observation':'输入未提供独立实际流量计量'},
              {'id':'N2','state':'active_notice'},
              {'id':'N3','state':'scheduled_notice'},
              {'id':'N4','state':'announced_end_elapsed_unverified_flow'}]
        projection=source_fact_projection({'evidence_ledger':{'notice_facts':rows}})
        self.assertEqual(projection['operator_notices'][0]['state_description'],'仅该公告约束撤销；实际流量与整体供应风险是否消除仍未知')
        self.assertEqual([row['state_description'] for row in projection['operator_notices'][1:]],['当前公告；流量未知','未来公告','公告已结束；当前未知'])
        self.assertNotIn('direction',str(projection))
        rows[0]['actual_flow_observation']='原文含实际流量陈述，数值和核验状态见该N原文'
        known=source_fact_projection({'evidence_ledger':{'notice_facts':rows}})
        self.assertEqual(known['operator_notices'][0]['state_description'],'已撤销；流量未知')

    def test_unobserved_flow_is_epistemic_but_not_zero_supply_impact(self):
        ctx = self.notice_context("Dismissed", "dismissed_notice", fact_excerpt="No independent actual flow measurement or recovery confirmation is provided.")
        self.assertValid(self.notice_output("[N0]公告已撤销，未见独立实际流量损失测量，实际恢复待核实"),ctx)
        out = self.notice_output("[N0]公告撤销仅减少公告容量风险，实际损失未确认")
        out["news_impact"]["neutralizing_evidence"]=["[N0]未确认实际流量损失 → 供应无冲击"]
        with self.assertRaisesRegex(ValueError,"只是缺测，不代表供应无冲击"):
            validate_analysis_semantics(out,ctx,{})

    def test_absent_flow_data_does_not_prove_no_window_supply_shock(self):
        ctx = self.notice_context("Dismissed", "dismissed_notice", fact_excerpt="No independent actual flow measurement or recovery confirmation is provided.")
        out = self.notice_output("[N0]本窗口无有效供应冲击，公告已被驳回，未见实际流量损失")
        with self.assertRaisesRegex(ValueError,"不能证明"):
            validate_analysis_semantics(out,ctx,{})

    def test_notice_missing_recovery_cannot_become_no_shock_across_comma(self):
        ctx = self.notice_context("Dismissed", "dismissed_notice", fact_excerpt="No independent actual flow measurement or recovery confirmation is provided.")
        for claim in ["公告已撤销，未确认实际流量恢复，未形成供需冲击，因而对TTF价格无明显影响。",
                      "公告已撤销，未确认实际流量恢复，无供需冲击。",
                      "[N0]公告已撤销，供应不受影响。",
                      "[N0]实际流量未受影响。"]:
            with self.subTest(claim=claim),self.assertRaisesRegex(ValueError,"须有独立无变化计量事实"):
                validate_analysis_semantics(self.notice_output(claim),ctx,{})
        out=self.notice_output('公告仅撤销容量风险，实际流量未知')
        out['news_impact']['neutralizing_evidence']=['[N0]公告已撤销，未确认实际供气恢复 → 供应约束解除但无实际流量影响']
        with self.assertRaisesRegex(ValueError,'须有独立无变化计量事实'):
            validate_analysis_semantics(out,ctx,{})
        for claim in ["[N0]输入未显示供需冲击，实际流量恢复待核实。",
                      "[N0]输入没有供需冲击证据，不能确认实际恢复。",
                      "[N0]缺少供需冲击证据，撤销公告仅减少容量风险。",
                      "[N0]未确认实际流量未受影响。",
                      "[N0]若供应不受影响，则不形成额外供需压力。"]:
            with self.subTest(claim=claim):self.assertValid(self.notice_output(claim),ctx)

    def test_future_notice_is_not_proof_current_price_was_unaffected(self):
        ctx=self.notice_context(state='scheduled_notice',event_start='2027-05-31T04:00:00Z')
        with self.assertRaisesRegex(ValueError,'没有这条公告的价格反应观测'):
            validate_analysis_semantics(self.notice_output('未来计划停产未影响当前价格'),ctx,{})
        for claim in ['未来维护不计作当前停供，当前新闻压力较弱','未来计划停产公告未计入当前供气损失，缺乏实际流量确认，当前价格影响不确定','未来维护预计对当前价格影响有限，反应仍待观察','无法确认未来维护是否影响当前价格','若未来维护尚未产生实物约束，当前新闻压力可能有限']:
            with self.subTest(claim=claim):self.assertValid(self.notice_output(claim),ctx)
        out=self.notice_output('公告公布后当前价格未受影响')
        self.assertValid(out,ctx,{'price_reference':{'announcement_price_reaction':{'observed_change':0,'available_at':'2026-10-20T11:00:00Z'}}})

    def test_not_priced_in_is_also_a_price_observation_claim(self):
        ctx=self.notice_context(state='scheduled_notice',event_start='2027-05-31T04:00:00Z')
        for claim in ['未来容量约束尚未计入当前价格','未来计划公告当前未计入价格，影响未知','可能存在后续风险，但当前未计入价格','The notice is not yet priced in.']:
            with self.subTest(claim=claim),self.assertRaisesRegex(ValueError,'不能宣称未计入价格'):
                validate_analysis_semantics(self.notice_output(claim),ctx,{})
        for claim in ['无法确认公告是否尚未计入当前价格','不能断言公告未计入价格','若公告未计入价格，则其风险可能影响预期','未来公告未计入当前供气损失，当前价格影响未知','缺乏前一结算价格基准，存在未计价风险。','可能尚未计价，反应仍待观察','是否尚未计价无法确认']:
            with self.subTest(claim=claim):self.assertValid(self.notice_output(claim),ctx)
        self.assertValid(self.notice_output('公告尚未计入当前价格'),ctx,
                         {'price_reference':{'announcement_price_reaction':{'source':'known-reaction-comparison'}}})

    def test_fieldfeedback_future_raw_cannot_assert_no_current_price_effect(self):
        fixture=json.loads((Path(__file__).parent/'fixtures'/'fieldfeedback_future_price_no_effect.json').read_text())
        self.assertTrue(fixture['provenance']['source_contract_passed'])
        self.assertEqual(json.loads(fixture['raw_content']),fixture['raw_result'])
        before=copy.deepcopy(fixture['raw_result'])
        errors=collect_analysis_semantic_errors(fixture['raw_result'],fixture['input_snapshot'],fixture['prepared'])
        self.assertTrue(any('analysis.evidence[0]:输入没有这条公告的价格反应观测' in error for error in errors))
        with self.assertRaisesRegex(ValueError,'不能断言未影响当前价格或价格未受影响'):
            validate_analysis_semantics(fixture['raw_result'],fixture['input_snapshot'],fixture['prepared'])
        self.assertEqual(fixture['raw_result'],before)

    def test_no_price_effect_word_order_keeps_uncertainty_and_physical_scope(self):
        ctx=self.notice_context(state='scheduled_notice',event_start='2027-05-22T04:00:00Z')
        for claim in ['[N0]供应约束但未来，未对当前价格产生影响',
                      '[N0]未来公告并未对当日TTF价格造成影响',
                      '[N0]未来安排对当前价格未产生影响']:
            with self.subTest(claim=claim),self.assertRaisesRegex(ValueError,'价格反应观测'):
                validate_analysis_semantics(self.notice_output(claim),ctx,{})
        for claim in ['[N0]尚未发生该未来公告时段的当前物理停供，价格反应未知',
                      '[N0]未见该公告的价格影响证据',
                      '[N0]无法确认该公告未对当前价格产生影响',
                      '[N0]若公告未对当前价格产生影响，则需继续观察独立公告前后反应',
                      '[N0]没有证据表明未对当前价格产生影响',
                      '[N0]未对当前价格产生影响的证据不足',
                      '[N0]未来容量安排的当前价格压力较弱，仍需观察反应']:
            with self.subTest(claim=claim):self.assertValid(self.notice_output(claim),ctx)

    def test_no_price_effect_requires_known_comparable_reaction_not_single_quote(self):
        from ttf_news.analysis import prepare_context
        ctx=self.notice_context(state='scheduled_notice',event_start='2027-05-22T04:00:00Z')
        out=self.notice_output('[N0]该公告未对当前价格产生影响')
        out['close_forecast'].update(evidence_status='insufficient_evidence',direction=None,summary='缺少目标日前交易日锚点与方向性新闻。',basis='公告反应与相对前结算的价格比较分别核对。')
        ctx['price_reference']={'market_baseline':{'available':True,'observed_delta':0,'observed_direction':'flat','observed_at':'2026-10-20T11:30:00Z','available_at':'2026-10-20T11:40:00Z','previous_price':88.04,'latest_price':88.04}}
        prepared,_=prepare_context(ctx)
        with self.assertRaisesRegex(ValueError,'价格反应观测'):
            validate_analysis_semantics(out,ctx,prepared)
        reaction={'source':'independent announcement before/after comparison','observed_at':'2026-10-20T11:40:00Z','available_at':'2026-10-20T11:50:00Z',
                  'before':{'value':88.04,'observed_at':'2026-10-20T10:50:00Z'},'after':{'value':88.04,'observed_at':'2026-10-20T11:40:00Z'}}
        ctx['price_reference']={'announcement_price_reaction':reaction}
        prepared,_=prepare_context(ctx)
        self.assertValid(out,ctx,prepared)
        for change in [{'available_at':'2026-10-20T12:01:00Z'},{'observed_at':'2026-10-20T12:01:00Z'}]:
            ctx['price_reference']={'announcement_price_reaction':{**reaction,**change}}
            prepared,_=prepare_context(ctx)
            self.assertNotIn('announcement_price_reaction',prepared['price_reference'])
            with self.subTest(change=change),self.assertRaisesRegex(ValueError,'价格反应观测'):
                validate_analysis_semantics(out,ctx,prepared)

    def test_revision_future_raw_reports_every_unpriced_field(self):
        # Exact prose from both untouched attempts of
        # candidate-revision-validation/future_planned_notice.json.
        ctx=self.notice_context(state='scheduled_notice',event_start='2027-05-22T04:00:00Z')
        first='计划的天然气生产场所容量减少20\u202fmcm/d，事件已在2027‑05‑22至2027‑05‑25安排，当前窗口内无实际供气损失，未计入价格。'
        second='计划的天然气生产场所容量在未来5月22日至5月25日减少20\u202fmcm/d，但截至10月20日未产生实际供气损失，未形成当前供需冲击，故对TTF价格影响中性。'
        for summary,analysis_summary,paths in [
            (first,'新增事实：'+first+'净影响为中性。',['news_impact.summary','close_forecast.basis','analysis.summary']),
            (second,'新增事实：'+second,['close_forecast.basis'])]:
            out=self.notice_output(summary)
            out['analysis']['summary']=analysis_summary
            out['close_forecast']['basis']='无可用基准价格，新闻为未来事件，未计入价格。'
            before=copy.deepcopy(out)
            pricing=[error for error in collect_analysis_semantic_errors(out,ctx,{}) if '不能宣称未计入价格/尚未计价' in error]
            with self.subTest(paths=paths):
                self.assertEqual(len(pricing),1)
                self.assertEqual(pricing[0].split(':',1)[0].split('、'),paths)
                self.assertLessEqual(len(pricing[0]),100)
                # This is the actual per-issue/global retry budget; no key
                # failed field is lost before repair_prompt sees the issue.
                feedback='；'.join(error[:100] for error in [pricing[0]])[:320]
                self.assertTrue(all(path in feedback for path in paths))
                self.assertEqual(out,before)

    def test_revision_eni_elapsed_notice_raw_is_not_current_unconditional_risk(self):
        fixture=json.loads((Path(__file__).parent/'fixtures'/'revision_eni_elapsed_notice.json').read_text())
        self.assertTrue(fixture['provenance']['source_contract_passed'])
        self.assertEqual(json.loads(fixture['raw_content']),fixture['raw_result'])
        out=fixture['raw_result'];before=copy.deepcopy(out)
        errors=collect_analysis_semantic_errors(out,fixture['input_snapshot'],fixture['prepared'])
        self.assertTrue(any('bullish_evidence[0]:N2公告时段已过' in error for error in errors))
        with self.assertRaisesRegex(ValueError,'N2公告时段已过.*不能裸写供应约束'):
            validate_analysis_semantics(out,fixture['input_snapshot'],fixture['prepared'])
        self.assertEqual(out,before)

    def test_elapsed_notice_requires_local_residual_condition_or_history(self):
        fixture=json.loads((Path(__file__).parent/'fixtures'/'revision_eni_elapsed_notice.json').read_text())
        for mechanism in ['供应约束','潜在供应约束','供应约束，推高当前价格']:
            out=copy.deepcopy(fixture['raw_result'])
            out['news_impact']['bullish_evidence'][0]=out['news_impact']['bullish_evidence'][0].rsplit('TTF机制：',1)[0]+'TTF机制：'+mechanism
            with self.subTest(mechanism=mechanism),self.assertRaisesRegex(ValueError,'N2公告时段已过'):
                validate_analysis_semantics(out,fixture['input_snapshot'],fixture['prepared'])
        for mechanism in ['若独立计量确认Troll实际流量仍受限，则存在条件性上行风险',
                          '残余上行风险取决于独立实际流量是否仍受限',
                          '历史背景：当时供应约束，需要区分公告容量与实流']:
            out=copy.deepcopy(fixture['raw_result'])
            for section,field in [('news_impact','bullish_evidence'),('analysis','evidence')]:
                out[section][field][0]=out[section][field][0].rsplit('TTF机制：',1)[0]+'TTF机制：'+mechanism
            with self.subTest(mechanism=mechanism):self.assertValid(out,fixture['input_snapshot'],fixture['prepared'])

    def test_elapsed_notice_residual_flow_must_be_known_and_observed_after_end(self):
        fixture=json.loads((Path(__file__).parent/'fixtures'/'revision_eni_elapsed_notice.json').read_text())
        end=datetime.fromisoformat(next(row for row in fixture['prepared']['window_news'] if row['id']=='N2')['operational']['event_end_utc'])
        now=datetime.fromisoformat(fixture['input_snapshot']['as_of'])
        observation={'title':'Troll independent meter update','summary':'独立计量确认Troll实际供气流量减少1mcm/d，受限仍在。',
                     'published_at':(end+timedelta(hours=1)).isoformat(),'observed_at':(end+timedelta(hours=1)).isoformat(),
                     'available_at':(end+timedelta(hours=2)).isoformat()}
        ctx=copy.deepcopy(fixture['input_snapshot']);ctx['window_news'].append(observation)
        self.assertValid(fixture['raw_result'],ctx,fixture['prepared'])
        for change in [{'available_at':(now+timedelta(hours=1)).isoformat()},
                       {'observed_at':(end-timedelta(hours=1)).isoformat()},
                       {'observed_at':(now+timedelta(hours=1)).isoformat()},
                       {'title':'Nyhamna independent meter update','summary':'独立计量确认Nyhamna实际供气流量减少1mcm/d。'}]:
            ctx=copy.deepcopy(fixture['input_snapshot']);ctx['window_news'].append({**observation,**change})
            with self.subTest(change=change),self.assertRaisesRegex(ValueError,'N2公告时段已过'):
                validate_analysis_semantics(fixture['raw_result'],ctx,fixture['prepared'])

    def test_expired_same_facility_sibling_does_not_taint_current_notice(self):
        ctx=context('非欧洲项目交付范围未知。')
        expired={'id':'N2','source':'gassco','title':'Gassco Troll: Unplanned unavailability (Active)',
                 'temporal_state':'announced_end_elapsed_unverified_flow','operational':{'announced_end_passed':True,'event_end_utc':'2026-10-20T10:00:00Z','unavailable_capacity':6.6}}
        active={'id':'N3','source':'gassco','title':'Gassco Troll: Unplanned unavailability (Active)',
                'temporal_state':'active_notice','operational':{'announced_end_passed':False,'event_end_utc':'2026-10-20T13:00:00Z','unavailable_capacity':5.2}}
        out=analysis('up','项目交付范围未知','当前容量公告构成条件风险')
        out['news_impact']['bullish_evidence']=['[N3]Troll；当前容量公告；实流未知；TTF机制：供应约束']
        out['analysis']['evidence']=out['news_impact']['bullish_evidence'][:]
        self.assertValid(out,ctx,{'window_news':[expired,active]})
        out['news_impact']['bullish_evidence']=['[N2]Troll；公告时段已过，当前容量未知；TTF机制：供应约束']
        with self.assertRaisesRegex(ValueError,'N2公告时段已过'):
            validate_analysis_semantics(out,ctx,{'window_news':[expired,active]})

    def test_pricing_diagnostics_deduplicate_paths_and_preserve_acceptance(self):
        ctx=self.notice_context()
        out=self.notice_output('公告未计入价格，尚未计价，公告未计入价格。')
        pricing=[error for error in collect_analysis_semantic_errors(out,ctx,{}) if '不能宣称未计入价格/尚未计价' in error]
        self.assertEqual(len(pricing),1)
        self.assertEqual(pricing[0].split(':',1)[0],'news_impact.summary')
        for statement in ['无法确认公告是否已计价，也不能断言尚未计价。',
                          '若公告未计入价格，其风险可能影响预期。',
                          '缺少公告反应证据，无法确认是否已计价。']:
            valid=self.notice_output(statement);valid['close_forecast']['basis']=statement;valid['analysis']['summary']=statement
            with self.subTest(statement=statement):self.assertValid(valid,ctx)
        for reference in [{'priced_in_evidence':{'source':'comparable sourced reaction'}},
                          {'announcement_price_reaction':{'source':'independent reaction'}}]:
            with self.subTest(reference=reference):self.assertValid(out,ctx,{'price_reference':reference})

    def test_actual_anchor_retry_risk_requires_check_not_priced_fact(self):
        fixture=json.loads((Path(__file__).parent/'fixtures'/'anchor_verification_request_not_priced_claim.json').read_text())
        self.assertFalse(fixture['provenance']['source_contract_passed'])
        for index,attempt in enumerate(fixture['attempts']):
            out=attempt['raw_result'];before=copy.deepcopy(out)
            with self.subTest(attempt=index+1):
                self.assertEqual(json.loads(attempt['raw_content']),out)
                self.assertIn(fixture['risk_literal'],out['risk_flags'])
                self.assertEqual(out['news_impact']['direction'],'flat')
                self.assertIsNone(out['close_forecast']['direction'])
                self.assertValid(out,fixture['input_snapshot'],attempt['prepared'])
                self.assertEqual(collect_analysis_semantic_errors(out,fixture['input_snapshot'],attempt['prepared']),[])
                self.assertEqual(out,before)

    def test_pricing_status_verification_requests_are_symmetric_and_local(self):
        ctx=self.notice_context()
        for statement in ['须核对修订内容与已计价情况。','需核实已计价状态。','待核对已计价与否。',
                          '需要判断是否已计价。','需核实尚未计价情况。','待核对未计价状态。',
                          '需判断是否尚未计价。','须核对“已计价”的情况是否属实。']:
            out=self.notice_output('公告容量风险仍需核实');out['risk_flags']=[statement]
            with self.subTest(statement=statement):self.assertValid(out,ctx)
        for statement in ['核对后确认已计价。','需核对后确认已计价情况属实。',
                          '须核实是否已计价，现已确认消息已计价。',
                          '须核对修订内容与已计价情况，消息已计价。',
                          '待核实是否尚未计价，已确认消息尚未计价。',
                          '核对后确认尚未计价。']:
            out=self.notice_output('公告容量风险仍需核实');out['risk_flags']=[statement]
            with self.subTest(statement=statement),self.assertRaisesRegex(ValueError,'不能宣称'):
                validate_analysis_semantics(out,ctx,{})
            self.assertTrue(any('risk_flags[0]' in error for error in collect_analysis_semantic_errors(out,ctx,{})))

    def test_missing_flow_cannot_itself_supply_neutral_mechanism(self):
        ctx=self.notice_context()
        for evidence in ['[N0]供给约束未确认流量损失 → TTF中性机制','[N0]未提供实际流量计量，因此价格影响中性']:
            out=self.notice_output('公告容量风险需结合流量核实')
            out['news_impact']['neutralizing_evidence']=[evidence]
            with self.subTest(evidence=evidence),self.assertRaisesRegex(ValueError,'不能作为TTF中性机制'):
                validate_analysis_semantics(out,ctx,{})
        for evidence in ['[N0]未确认流量损失，容量约束风险仍在，降低把握程度','[N0]未确认流量损失不代表TTF中性机制','[N0]未确认流量损失，不能直接判为中性','[N0]实际流量损失未确认 → 当前影响不确定']:
            out=self.notice_output('公告容量风险需结合流量核实')
            out['news_impact']['neutralizing_evidence']=[evidence]
            with self.subTest(evidence=evidence):self.assertValid(out,ctx)

    def test_conditional_current_capacity_risk_is_not_neutralizing_evidence(self):
        ctx=context('Eni非洲项目启动，欧洲即时传导有限。')
        rows=[{'id':'N3','source':'gassco','title':'Gassco Åsgard: Unplanned unavailability (Active)',
               'temporal_state':'active_notice','operational':{'capacity_is_not_lost_flow':True,'unavailable_capacity':5.2}},
              {'id':'N7','source':'gassco','title':'Gassco Nyhamna: Planned unavailability (Active)',
               'temporal_state':'active_notice','operational':{'capacity_is_not_lost_flow':True,'unavailable_capacity':19.8}}]
        claims=['[N3] Åsgard公告5.2\u202fmcm/d容量缺口 → 可能的供应约束（未确认流量损失）',
                '[N7] Nyhamna公告19.8\u202fmcm/d容量缺口 → 可能的供应约束（未确认流量损失）']
        for claim in claims:
            out=analysis('flat','项目启动事项','欧洲即时传导有限')
            out['news_impact']['neutralizing_evidence']=[claim]
            before=copy.deepcopy(out)
            with self.subTest(claim=claim),self.assertRaisesRegex(ValueError,'条件性上行风险.*不能以未确认流量损失作为抵消'):
                validate_analysis_semantics(out,ctx,{'window_news':rows})
            self.assertTrue(any('条件性上行风险' in error for error in collect_analysis_semantic_errors(out,ctx,{'window_news':rows})))
            self.assertEqual(out,before)
        # Correctly reporting the conditional risk never mandates a net label.
        out=analysis('flat','项目启动事项','欧洲即时传导有限，净新闻压力较弱')
        out['news_impact']['bullish_evidence']=claims
        self.assertValid(out,ctx,{'window_news':rows})
        out['news_impact']['bullish_evidence']=[]
        out['analysis']['evidence']=claims
        self.assertValid(out,ctx,{'window_news':rows})

    def test_capacity_bucket_preserves_temporal_scopes_unchanged_flow_and_real_offsets(self):
        claim='[N0]容量缺口 → 可能的供应约束（未确认流量损失）'
        for ctx in [self.notice_context('Dismissed','dismissed_notice'),
                    self.notice_context(state='scheduled_notice',event_start='2027-05-31T04:00:00Z'),
                    self.notice_context(state='announced_end_elapsed_unverified_flow',event_end='2026-10-20T10:00:00Z'),
                    self.notice_context(fact_excerpt='独立计量确认Troll实际流量保持不变。')]:
            out=self.notice_output('容量状态与实际流量应分别分析')
            out['news_impact']['neutralizing_evidence']=[claim]
            with self.subTest(state=ctx['current_news'].get('temporal_state'),excerpt=ctx['current_news'].get('fact_excerpt')):self.assertValid(out,ctx)
        ctx=self.notice_context()
        warm={'id':'N4','source':'weather','title':'欧洲天气温和','facts':'欧洲温和天气降低采暖需求。'}
        ctx['window_news']=[warm]
        out=self.notice_output('供应约束与需求缓解权衡，新闻净压力较弱')
        out['news_impact']['neutralizing_evidence']=['[N0]容量缺口 → 可能供应约束，但[N4]温和天气降低采暖需求，形成反向压力']
        self.assertValid(out,ctx,{'window_news':[warm]})
        warm['facts']='未提供欧洲天气或需求资料。'
        warm['title']='天气资料缺失'
        with self.assertRaises(ValueError):
            validate_analysis_semantics(out,ctx,{'window_news':[warm]})
        self.assertTrue(any('条件性上行风险' in error for error in collect_analysis_semantic_errors(out,ctx,{'window_news':[warm]})))

    def test_provenance_eni_analysis_exclusion_is_not_market_not_priced_in(self):
        ctx=context('非欧洲项目的欧洲即时交付范围未给。')
        prepared={'window_news':[
            {'id':'N3','source':'gassco','title':'Gassco Åsgard: Unplanned unavailability (Active)',
             'temporal_state':'active_notice','operational':{'capacity_is_not_lost_flow':True,'unavailable_capacity':5.2}},
            {'id':'N7','source':'gassco','title':'Gassco Nyhamna: Planned unavailability (Active)',
             'temporal_state':'active_notice','operational':{'capacity_is_not_lost_flow':True,'unavailable_capacity':19.8}}]}
        out=analysis('flat','非欧洲项目交付范围未知','本窗口新闻传导有限')
        out['news_impact']['summary']='截至2026‑10‑19，未确认任何直接影响TTF的供需冲击。N0为非欧洲项目，N3与N7虽公告容量受限，但缺乏实际流量证据，故不计入价格冲击。'
        out['analysis']['summary']='新公布的非欧洲气源项目未对TTF产生直接影响；现有公告的容量受限（N3、N7）缺乏实际流量确认，故未计入价格冲击。'
        out['analysis']['evidence']=[
            '[N3]Åsgard；非计划；公告不可用容量5.2mcm/d；当前容量公告；实流未知；TTF机制：潜在供应约束',
            '[N7]Nyhamna；计划；公告不可用容量19.8mcm/d；当前容量公告；实流未知；TTF机制：潜在供应约束']
        before=copy.deepcopy(out)
        errors=collect_analysis_semantic_errors(out,ctx,prepared)
        self.assertTrue(any('缺乏实际流量确认不能因此不计入价格冲击' in error for error in errors))
        self.assertFalse(any('不能宣称未计入价格/尚未计价' in error for error in errors))
        with self.assertRaisesRegex(ValueError,'这不是市场未计价判断'):
            validate_analysis_semantics(out,ctx,prepared)
        self.assertEqual(out,before)

    def test_unknown_capacity_cannot_be_excluded_from_analyst_accounting(self):
        ctx=self.notice_context()
        for claim in ['[N0]缺乏实际流量确认，故未计入价格冲击',
                      '[N0]实际流量未确认，因此不纳入本次分析',
                      '[N0]缺少实际流量证据，所以未纳入当前风险判断']:
            with self.subTest(claim=claim),self.assertRaisesRegex(ValueError,'条件性供应风险.*不是市场未计价'):
                validate_analysis_semantics(self.notice_output(claim),ctx,{})
        for claim in ['[N0]缺乏实际流量确认，风险与价格压力幅度不确定，净新闻压力较弱',
                      '[N0]实际流量未确认，不能因此不计入价格冲击，仍保留容量约束风险',
                      '[N0]公告容量未计入已确认实际供气损失，仍作为条件风险分析',
                      '[N0]无法确认公告是否已计价，容量风险仍需分析']:
            out=self.notice_output(claim)
            out['news_impact']['direction']='flat'
            with self.subTest(claim=claim):self.assertValid(out,ctx)

    def test_true_market_unpriced_claim_remains_distinct_from_analysis_exclusion(self):
        ctx=self.notice_context()
        for claim in ['[N0]市场尚未计入价格冲击','[N0]公告尚未计入当前价格','[N0]市场未消化该公告，尚未计价']:
            with self.subTest(claim=claim),self.assertRaisesRegex(ValueError,'不能宣称未计入价格/尚未计价'):
                validate_analysis_semantics(self.notice_output(claim),ctx,{})

    def test_capacity_exclusion_guard_preserves_temporal_and_observed_scope(self):
        claim='[N0]缺乏实际流量确认，故不计入价格冲击'
        for ctx in [self.notice_context('Dismissed','dismissed_notice'),
                    self.notice_context(state='scheduled_notice',event_start='2027-05-31T04:00:00Z'),
                    self.notice_context(state='announced_end_elapsed_unverified_flow',event_end='2026-10-20T10:00:00Z')]:
            with self.subTest(state=ctx['current_news']['temporal_state']):self.assertValid(self.notice_output(claim),ctx)
        ctx=self.notice_context()
        ctx['window_news']=[{'title':'Troll metered update','summary':'独立计量确认Troll实际流量保持不变。'}]
        self.assertValid(self.notice_output(claim),ctx)
        ctx['window_news'][0]['summary']='独立计量确认Nyhamna实际流量保持不变。'
        with self.assertRaisesRegex(ValueError,'条件性供应风险'):
            validate_analysis_semantics(self.notice_output(claim),ctx,{})

    def test_flat_quote_before_news_is_not_announcement_no_reaction_evidence(self):
        ctx=context('运营方和计量确认欧洲实际供气增加40mcm/d；未提供消息前后价格反应。')
        ctx['current_news']['published_at']='2026-10-20T12:00:00Z'
        baseline={'available':True,'observed_delta':0,'observed_direction':'flat','latest_price':88.04,'previous_price':88.04,
                  'observed_at':'2026-10-20T11:40:00Z','available_at':'2026-10-20T11:59:59Z'}
        ctx['price_reference']={'available':True,'market_baseline':baseline}
        for field,claim in [('summary','基于市场基准平稳且供应增加未导致价格下跌，预计结算价相对前一日持平。'),
                            ('basis','市场基准显示参考价格无变化，且供应增加未见即时价格反应，说明价格可能已被预期。')]:
            out=analysis('down')
            out['close_forecast'].update(direction='flat',summary='基准保持平稳，目标结算判断为持平',basis='新闻下行压力与基准应分别分析')
            out['close_forecast'][field]=claim
            before=copy.deepcopy(out)
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'observed_at不晚于新闻发布时间.*available_at只是'):
                validate_analysis_semantics(out,ctx,{'price_reference':ctx['price_reference']})
            self.assertEqual(out,before)
            self.assertTrue(any('消息前后独立价格反应' in error for error in collect_analysis_semantic_errors(out,ctx,{'price_reference':ctx['price_reference']})))
        # A later quote alone also does not establish a particular event's
        # reaction; the reject reason must not invent a pre-news timestamp.
        ctx['as_of']='2026-10-20T12:10:00Z'
        ctx['price_reference']['market_baseline'].update(observed_at='2026-10-20T12:05:00Z',available_at='2026-10-20T12:05:01Z')
        out=analysis('down');out['close_forecast']['basis']='供应增加未见即时价格反应。'
        with self.assertRaisesRegex(ValueError,'消息前后独立价格反应') as error:
            validate_analysis_semantics(out,ctx,{'price_reference':ctx['price_reference']})
        self.assertNotIn('observed_at不晚于新闻发布时间',str(error.exception))
        # Another announcement's reaction cannot be borrowed for this one.
        unrelated={'id':'N3','source':'other','facts':'该消息未见即时价格反应。'}
        with self.assertRaisesRegex(ValueError,'消息前后独立价格反应'):
            validate_analysis_semantics(out,ctx,{'price_reference':ctx['price_reference'],'window_news':[unrelated]})

    def test_reaction_unknown_baseline_description_conditions_and_actual_reaction_evidence_remain_valid(self):
        ctx=context('本窗口首次计量确认欧洲供气增加40mcm/d。')
        ctx['current_news']['published_at']='2026-10-20T12:00:00Z'
        ctx['price_reference']={'market_baseline':{'available':True,'observed_delta':0,'observed_direction':'flat','observed_at':'2026-10-20T11:40:00Z','available_at':'2026-10-20T11:59:59Z'}}
        for basis in ['已观察基准持平，供应增加的公告后价格反应未知。',
                      '无法确认供应增加未导致价格下跌。',
                      '若供应增加未导致价格下跌，则需重新检查其他冲击。',
                      '输入未提供供应增加后无即时价格反应的证据。']:
            out=analysis('down');out['close_forecast']['basis']=basis
            with self.subTest(basis=basis):self.assertValid(out,ctx,{'price_reference':ctx['price_reference']})
        out=analysis('down');out['close_forecast']['basis']='供应增加未见即时价格反应。'
        proven=copy.deepcopy(ctx['price_reference'])
        proven['announcement_price_reaction']={'source':'independent event comparison','observed_change':0,'available_at':'2026-10-20T12:00:00Z'}
        self.assertValid(out,ctx,{'price_reference':proven})

    def mixed_notice_prepared(self):
        return {'evidence_ledger':{'notice_facts':[
            {'id':'N3','facility':'Åsgard','notice_type':'非计划','state':'active_notice','cause_original':'Yearly maintenance'},
            {'id':'N7','facility':'Nyhamna','notice_type':'计划','state':'active_notice'},
            {'id':'N8','facility':'Kollsnes','notice_type':'计划','state':'scheduled_notice'},
            {'id':'N1','facility':'Troll','notice_type':'非计划','state':'dismissed_notice'},
            {'id':'N2','facility':'Troll','notice_type':'非计划','state':'announced_end_elapsed_unverified_flow'}]}}

    def test_mixed_active_types_cannot_be_aggregated_as_one_type(self):
        ctx=context('Eni项目的欧洲即时传导有待观察。')
        prepared=self.mixed_notice_prepared()
        for claim in ['两项活跃的非计划停产导致潜在供应约束，伴随新项目开启预期未来供给。',
                      'Nyhamna与Åsgard两条计划停产公告提供供应风险。',
                      '两处均为计划检修，实际流量仍待核实。',
                      '[N3][N7]两个活跃停机均为非计划。']:
            out=analysis('flat','项目消息','欧洲直接传导有限')
            out['news_impact']['summary']=claim
            before=copy.deepcopy(out)
            with self.subTest(claim=claim),self.assertRaisesRegex(ValueError,'类型混合.*N3=非计划.*N7=计划'):
                validate_analysis_semantics(out,ctx,prepared)
            self.assertEqual(out,before)
            self.assertTrue(any('当前两项容量公告类型混合' in error for error in collect_analysis_semantic_errors(out,ctx,prepared)))

    def test_correct_mixed_single_type_and_single_cited_notice_are_preserved(self):
        ctx=context('Eni项目事项。');prepared=self.mixed_notice_prepared()
        for claim in ['当前为1项计划+1项非计划容量公告，实际流量未核实。',
                      'Åsgard为非计划公告，Nyhamna为计划公告，仍需核实实际流量。',
                      '[N3]非计划公告的年检延期形成容量风险。',
                      '[N7]计划检修公告提供潜在供应约束。',
                      '两项公告并非均为非计划停产。',
                      '若两项均为非计划停产，需要重新核实来源类型。']:
            out=analysis('flat','项目事项','欧洲直接传导有限');out['analysis']['summary']=claim
            with self.subTest(claim=claim):self.assertValid(out,ctx,prepared)
        one_type=copy.deepcopy(prepared)
        one_type['evidence_ledger']['notice_facts'][0]['notice_type']='计划'
        out=analysis('flat','项目事项','欧洲直接传导有限');out['analysis']['summary']='两项活跃的计划检修容量公告。'
        self.assertValid(out,ctx,one_type)
        one_current=copy.deepcopy(prepared)
        one_current['evidence_ledger']['notice_facts'][1]['state']='scheduled_notice'
        self.assertValid(out,ctx,one_current)

    def test_active_mixed_notice_fact_summary_deduplicates_ids_and_ignores_future_dismissed(self):
        prepared=self.mixed_notice_prepared()
        prepared['evidence_ledger']['notice_facts'].append(copy.deepcopy(prepared['evidence_ledger']['notice_facts'][0]))
        summary=source_fact_projection(prepared)['active_notice_summary']
        self.assertEqual(summary,{'ids':['N3','N7'],'planned_count':1,'unplanned_count':1,
                                  'description':'1项计划+1项非计划容量公告，实际流量未核实'})
        self.assertNotIn('direction',summary)
        self.assertNotIn('flow',str(summary))
        for row in prepared['evidence_ledger']['notice_facts']:
            if row['id']=='N7':row['state']='dismissed_notice'
        self.assertNotIn('active_notice_summary',source_fact_projection(prepared))

    def test_future_notice_capacity_is_not_quantified_planned_loss(self):
        ctx=self.notice_context(state='scheduled_notice',event_start='2027-05-31T04:00:00Z')
        for claim in ['未来5月22日至25日计划停产20\u202fmcm/d，但未提供实际流量计量','[N0]预计减供20mcm/d，实际流量尚未测得']:
            with self.subTest(claim=claim),self.assertRaisesRegex(ValueError,'公告不可用容量不等于计划停产'):
                validate_analysis_semantics(self.notice_output(claim),ctx,{})
        for claim in ['[N0]未来维护公告不可用容量20mcm/d，实际减供尚未确认','[N0]不可用容量20mcm/d不能视为计划停产20mcm/d','[N0]若实际减供20mcm/d，供给风险将增加']:
            with self.subTest(claim=claim):self.assertValid(self.notice_output(claim),ctx)
        for sourced_plan in ['Troll production will decrease by 20 mcm/day during the future maintenance.',
                             'Troll未来检修的交付指引：供气流量预计减少20mcm/d。']:
            with self.subTest(sourced_plan=sourced_plan):
                planned=copy.deepcopy(ctx)
                planned['current_news']['fact_excerpt']=sourced_plan
                self.assertValid(self.notice_output('[N0]未来预计减供20mcm/d，仍是计划而非已发生流量损失'),planned)

    def test_verb_first_supply_expansion_cannot_be_bullish_evidence(self):
        ctx=context('Eni项目未来开发，天然气发现及合作细节尚待落实。')
        for evidence in ['[N0]该项目开启将增加供应 → TTF看涨机制','[N0]新发现将提升供应 → TTF看涨机制','[N0]合作扩大供应 → TTF看涨机制']:
            out=analysis('flat','项目事项','欧洲传导有限')
            out['news_impact']['bullish_evidence']=[evidence]
            with self.subTest(evidence=evidence),self.assertRaisesRegex(ValueError,'供需机制相反|未给已投产'):
                validate_analysis_semantics(out,ctx,{})
        out=analysis('flat','项目事项','欧洲传导有限')
        out['news_impact']['bearish_evidence']=['[N0]未来合作可能扩大供应 → 长期TTF下行预期，当前交付未确认']
        self.assertValid(out,ctx)

    def test_notice_independent_unchanged_flow_allows_no_loss_assertion(self):
        ctx=self.notice_context("Dismissed","dismissed_notice",fact_excerpt="独立计量确认Troll实际流量未受影响。")
        self.assertValid(self.notice_output("[N0]实际流量未受影响。"),ctx)
        ctx["fact_excerpt"]=""
        ctx["current_news"]["fact_excerpt"]="Troll实际流量未受影响与否尚未确认。"
        with self.assertRaisesRegex(ValueError,"须有独立无变化计量事实"):
            validate_analysis_semantics(self.notice_output("[N0]实际流量未受影响。"),ctx,{})

    def test_weak_non_notice_corporate_news_can_still_have_no_supply_impact(self):
        self.assertValid(self.notice_output("公司许可事项未形成供需冲击，欧洲直接传导有限"),context("牙买加公司许可证行政延期。"))

    def test_total_supply_share_requires_its_own_ratio_or_denominator(self):
        out=self.notice_output("约25mcm/d的容量限制虽占总供给约12%，影响仍需结合流量核实")
        ctx=self.notice_context(fact_excerpt="天然气参考价格较前日上涨12%。")
        with self.assertRaisesRegex(ValueError,'不能编造占比'):
            validate_analysis_semantics(out,ctx,{})
        ctx['current_news']['fact_excerpt']='该供应变化占欧洲总供给约12%。'
        self.assertValid(out,ctx)
        out['news_impact']['summary']='该供应变化占总供给12.5%，强度仍需结合时点'
        ctx['current_news'].update(fact_excerpt='',actual_flow_loss_mcm_d=25,total_supply_mcm_d=200)
        self.assertValid(out,ctx)
        out['news_impact']['summary']='若总供给为200mcm/d，25mcm/d约占总供给12.5%，该分母仅为假设'
        self.assertValid(out,self.notice_context())
        out['news_impact']['summary']='行情参考价格变化12%，与公告因果仍待核实'
        self.assertValid(out,self.notice_context())

    def test_source_fact_checks_match_known_metadata_without_changing_forecast(self):
        out=analysis('flat','弱新闻事项','欧洲传导有限')
        prepared={'evidence_ledger':{'notice_facts':[{'id':'N3','notice_type':'非计划','state_description':'公告时段已结束，实际流量未知'}],
                                    'pricing_observation':{'announcement_reaction_evidence_available':False,'observed_delta':.628}}}
        checks=source_fact_projection(prepared)
        out['source_fact_checks']=copy.deepcopy(checks)
        self.assertValid(out,context('弱新闻事项'),prepared)
        out['source_fact_checks']['operator_notices'][0]['notice_type']='计划'
        with self.assertRaisesRegex(ValueError,'source_fact_checks.*不一致'):
            validate_analysis_semantics(out,context('弱新闻事项'),prepared)

    def test_resource_discovery_is_not_current_supply_offset(self):
        discovery={"id":"N4","title":"Nordic energy players hit paydirt with new gas and condensate discovery",
                   "facts":"Vår Energi and Equinor have struck gas and condensate in Norwegian waters, packing up to 5.5 million barrels.",
                   "source":"offshore_energy"}
        ctx=self.notice_context()
        prepared={"window_news":[discovery]}
        out=self.notice_output("当前容量公告提供供应风险证据")
        out["news_impact"]["bearish_evidence"]=["[N4] Vår Energi 与 Equinor 在挪威海发现 5.5 百万桶 → 供给增加 ↓"]
        with self.assertRaisesRegex(ValueError,"N4.*未给已投产或实际交付"):
            validate_analysis_semantics(out,ctx,prepared)
        out["news_impact"]["bearish_evidence"]=["[N4] 发现油气资源 → 长期供给预期增加，尚不能视为当前交付"]
        self.assertValid(out,ctx,prepared)
        out["news_impact"]["bearish_evidence"]=["[N4] 若未来投产，供给增加可能缓解供应压力"]
        self.assertValid(out,ctx,prepared)
        delivery={**discovery,"facts":discovery["facts"]+" The project has commenced production; measured European actual flows increased 5 mcm/d."}
        out["news_impact"]["bearish_evidence"]=["[N4] 项目已投产，供给增加产生下行压力"]
        self.assertValid(out,ctx,{"window_news":[delivery]})

    def test_contract_discovery_physical_supply_arrow_cannot_be_bullish(self):
        discovery={'id':'N4','source':'offshore_energy','title':'Nordic energy players hit paydirt with new gas and condensate discovery',
                   'facts':'Vår Energi and Equinor have struck gas and condensate in Norwegian waters, packing up to 5.5 million barrels.'}
        ctx=context('当前容量公告仍需核实流量。')
        prepared={'window_news':[discovery]}
        out=analysis('up','维护容量受限','当前供给风险形成上行压力')
        out['news_impact']['bullish_evidence']=['[N4]Vår Energi/Equinor发现5.5mbl；资源发现；TTF机制：潜在供给↑（弱）']
        before=copy.deepcopy(out)
        with self.assertRaisesRegex(ValueError,'物理量箭头不是TTF价格箭头'):
            validate_analysis_semantics(out,ctx,prepared)
        self.assertTrue(any('物理量箭头不是TTF价格箭头' in error for error in collect_analysis_semantic_errors(out,ctx,prepared)))
        self.assertEqual(out,before)
        out['news_impact']['bullish_evidence']=[]
        out['news_impact']['summary']='资源发现为弱势看涨因素。'
        with self.assertRaisesRegex(ValueError,'不能因发现是公司利好'):
            validate_analysis_semantics(out,ctx,prepared)
        out['news_impact']['summary']='当前维护约束带来上行风险；资源发现为弱势看涨因素。'
        with self.assertRaisesRegex(ValueError,'不能因发现是公司利好'):
            validate_analysis_semantics(out,self.notice_context(),prepared)

    def test_quantity_arrows_keep_supply_and_price_scopes_distinct(self):
        ctx=context('欧洲供需变化仅作条件情景，不提供实物流量计量。')
        for direction,fact in [('down','潜在供给↑'),('up','潜在供给↓'),('up','潜在需求↑'),('down','潜在需求↓')]:
            out=analysis(direction,'条件性供需机制','下行压力' if direction=='down' else '上行压力')
            out['news_impact']['bearish_evidence' if direction=='down' else 'bullish_evidence']=['[N0]未来情景；TTF机制：'+fact]
            with self.subTest(fact=fact):self.assertValid(out,ctx)
        out=analysis('up','条件价格情景','上行压力')
        out['news_impact']['bullish_evidence']=['[N0]价格动量情景 → TTF价格↑']
        self.assertValid(out,ctx)
        out['news_impact']['bullish_evidence']=['[N0]潜在供给↑ → TTF价格↑']
        with self.assertRaisesRegex(ValueError,'物理量箭头不是TTF价格箭头|价格传导相反'):
            validate_analysis_semantics(out,ctx,{})

    def test_resource_conflicts_and_sourced_expectation_shortfall_remain_valid(self):
        discovery={'id':'N4','source':'offshore_energy','title':'New gas discovery','facts':'New gas discovery may increase future supply.'}
        demand={'id':'N5','source':'weather','title':'欧洲天气转冷','facts':'欧洲采暖需求上升。'}
        ctx=context('欧洲天气转冷，需求增加。')
        out=analysis('up','欧洲天气转冷','需求增加带来上行压力')
        out['news_impact']['bullish_evidence']=['[N4]潜在供给↑，但[N5]欧洲采暖需求上升形成上行压力']
        self.assertValid(out,ctx,{'window_news':[discovery,demand]})
        out['news_impact']['bullish_evidence']=['[N4]潜在供给↑，但[N5]采暖需求↑，存在相反供需压力']
        self.assertValid(out,ctx,{'window_news':[discovery,demand]})
        discovery['facts']='发现规模低于此前预期；新增远期供给预期下修。'
        out['news_impact']['bullish_evidence']=['[N4]潜在供给↑但发现规模低于此前预期，远期预期被下修 → TTF看涨']
        self.assertValid(out,ctx,{'window_news':[discovery]})
        discovery['facts']='New gas discovery may increase future supply; no expected discovery estimate is provided.'
        with self.assertRaisesRegex(ValueError,'物理量箭头不是TTF价格箭头|资源发现仅支持'):
            validate_analysis_semantics(out,context('企业发现事项。'),{'window_news':[discovery]})
        for unavailable_gap in ['发现规模没有低于此前预期。','没有证据表明发现规模低于此前预期。','发现规模低于此前预期与否尚未确认。']:
            discovery['facts']=unavailable_gap
            with self.subTest(unavailable_gap=unavailable_gap),self.assertRaisesRegex(ValueError,'物理量箭头不是TTF价格箭头|资源发现仅支持'):
                validate_analysis_semantics(out,context('企业发现事项。'),{'window_news':[discovery]})

    def test_conditional_price_move_does_not_prove_priced_in_consequence(self):
        ctx=context('运营方和计量确认欧洲实际供气增加40mcm/d；没有公告前后报价反应。')
        for claim in ['[N0]若市场报价在公告后出现显著上涨，表明已计价或存在其他上行因素',
                      '[N0]若公告后价格下跌，说明消息已计入价格',
                      '[N0]如果TTF价格上涨，就证明消息尚未计价']:
            out=analysis('down');out['detail']={'invalidation_conditions':[claim]}
            before=copy.deepcopy(out)
            with self.subTest(claim=claim),self.assertRaisesRegex(ValueError,'单向涨跌不能说明/证明已计价或未计价'):
                validate_analysis_semantics(out,ctx,{})
            self.assertEqual(out,before)
            self.assertTrue(any('单向涨跌不能说明/证明' in error for error in collect_analysis_semantic_errors(out,ctx,{})))

    def test_pricing_hypotheses_evidential_conditions_and_supply_invalidation_remain_valid(self):
        ctx=context('欧洲实际供气增加40mcm/d。')
        for claim in ['[N0]若市场报价在公告后上涨，需要重新检查其他上行供需因素',
                      '[N0]若已计价，则新增价格压力可能较小',
                      '[N0]若独立可比公告前后反应证据证实已计价，则重估边际影响',
                      '[N0]若未来供应恢复中断，原有下行判断可能失效',
                      '[N0]若价格上涨，不能说明已计价',
                      '[N0]若价格下跌，说明仍无法确认是否已计价']:
            out=analysis('down');out['detail']={'invalidation_conditions':[claim]}
            with self.subTest(claim=claim):self.assertValid(out,ctx)
        out=analysis('down');out['detail']={'invalidation_conditions':['[N0]若价格上涨，表明已计价或有其他因素']}
        self.assertValid(out,ctx,{'price_reference':{'announcement_price_reaction':{'source':'independent comparable event reaction'}}})

    def test_unaliased_summary_cannot_upgrade_cited_capacity_to_active_outage(self):
        ctx=context("Eni与非洲公司签署天然气项目MOU，未来合作细节待定。")
        prepared={"window_news":[{"id":"N3","source":"gassco","title":"Gassco Åsgard: Unplanned field unavailability (Active)",
                                 "operational":{"capacity_is_not_lost_flow":True,"unavailable_capacity":5.2}},
                                {"id":"N7","source":"gassco","title":"Gassco Nyhamna: Planned other unavailability (Active)",
                                 "operational":{"capacity_is_not_lost_flow":True,"unavailable_capacity":19.8}}]}
        out=analysis("up","Eni合作事项","当前容量公告带来上行风险")
        out["news_impact"]["bullish_evidence"]=["[N3] Åsgard容量受限 → TTF潜在供给风险", "[N7] Nyhamna容量受限 → TTF潜在供给风险"]
        out["analysis"]["evidence"]=out["news_impact"]["bullish_evidence"][:]
        for claim in ["两条活跃的供应中断导致TTF供给受限，推高价格。", "新增事实：两条活跃供应中断，净影响偏涨。"]:
            out["news_impact"]["summary"]=claim
            with self.subTest(claim=claim),self.assertRaisesRegex(ValueError,"不能证明实际流量损失"):
                validate_analysis_semantics(out,ctx,prepared)
        for claim in ["两条活跃的容量受限公告增加TTF供应中断风险，净影响偏涨。", "若公告限制导致当前供应中断，则TTF可能上行。"]:
            out["news_impact"]["summary"]=claim
            with self.subTest(claim=claim):self.assertValid(out,ctx,prepared)

    def test_binding_eni_announcement_noun_is_not_a_measured_outage(self):
        ctx=context("This gas project is set to slash 1.5 million tonnes of annual carbon emissions while unlocking fresh energy supplies for a growing domestic grid.")
        ctx["current_news"]["title"]="Eni and Libya’s National Oil Corporation turning on the taps at African gas project"
        prepared={"window_news":[{"id":"N3","source":"gassco","title":"Gassco Åsgard: Unplanned field unavailability (Active)",
                                 "temporal_state":"active_notice","operational":{"capacity_is_not_lost_flow":True,"unavailable_capacity":5.2}},
                                {"id":"N7","source":"gassco","title":"Gassco Nyhamna: Planned other unavailability (Active)",
                                 "temporal_state":"active_notice","operational":{"capacity_is_not_lost_flow":True,"unavailable_capacity":19.8}}]}
        out=analysis("flat","项目供能范围待确认","容量公告提供供给风险，新闻净压力有限")
        out["news_impact"]["bullish_evidence"]=[
            "[N3]Åsgard；非计划；公告不可用容量5.2mcm/d；当前容量公告；实流未知；TTF机制：供应约束",
            "[N7]Nyhamna；计划；公告不可用容量19.8mcm/d；当前容量公告；实流未知；TTF机制：供应约束"]
        out["analysis"]["evidence"]=out["news_impact"]["bullish_evidence"][:]
        for claim in ["当前窗口内有两条活跃的供给中断公告（Åsgard 与 Nyhamna），但缺乏实际流量确认，净影响偏平。",
                      "新增事实：Åsgard 与 Nyhamna 两条活跃的供给中断公告，实际流量未确认。",
                      "[N3]当前供应中断公告增加容量风险，实际流量待核实。"]:
            out["news_impact"]["summary"]=claim
            with self.subTest(claim=claim):self.assertValid(out,ctx,prepared)
        # Exact first raw summary: the notice noun is valid, but an unrelated
        # invented project status must still fail with the real reason.
        out["news_impact"]["summary"]="当前窗口内有两条活跃的供给中断公告（Åsgard 与 Nyhamna），但缺乏实际流量确认，且新项目尚未投产，整体对TTF价格的冲击有限，净影响偏平。"
        errors=collect_analysis_semantic_errors(out,ctx,prepared)
        self.assertTrue(any("缺少投产时刻或输欧数量不等于尚未投产" in error for error in errors))
        self.assertFalse(any("不能证明实际流量损失" in error for error in errors))

    def test_announcement_noun_does_not_excuse_separate_actual_loss(self):
        ctx=self.notice_context()
        for claim in ["[N0]当前供应中断，虽缺乏实际流量确认。",
                      "[N0]当前供应中断公告导致实际供气减少5mcm/d，实际流量未知。",
                      "[N0]当前设施已停产5mcm/d，虽然公告缺少实际流量确认。",
                      "[N0]实际供气已减少5mcm/d公告，虽然尚未核实。"]:
            with self.subTest(claim=claim),self.assertRaisesRegex(ValueError,"不能证明实际流量损失|公告不可用容量不等于"):
                validate_analysis_semantics(self.notice_output(claim),ctx,{})

    def test_turning_on_taps_does_not_support_invented_not_started_fact(self):
        ctx=context("The project unlocks fresh energy supplies for a growing domestic grid; no European delivery quantity or startup timestamp is provided.")
        ctx["current_news"]["title"]="Eni turning on the taps at African gas project"
        for claim in ["新项目尚未投产，欧洲传导有限。","[N0]项目尚未投入运营，因此暂无交付。"]:
            out=analysis("flat","项目交付范围未知","新闻欧洲传导有限")
            out["news_impact"]["summary"]=claim
            with self.subTest(claim=claim),self.assertRaisesRegex(ValueError,"缺少投产时刻或输欧数量不等于尚未投产"):
                validate_analysis_semantics(out,ctx,{})
        for claim in ["该项目投产时刻和输欧数量未给，直接传导范围未知。",
                      "若该项目尚未投产，后续供能时点需核实。",
                      "不能断言该项目尚未投产，输入没有明确投产时刻。",
                      "[N4]未来发现项目尚未投产。"]:
            out=analysis("flat","项目交付范围未知","新闻欧洲传导有限");out["news_impact"]["summary"]=claim
            with self.subTest(claim=claim):self.assertValid(out,ctx)

    def test_explicit_future_nonstartup_source_remains_valid(self):
        for source in ["The project has not commenced production and startup is planned next year.",
                       "The project is expected to start production next year.",
                       "项目预计2027年投产，当前尚未投产。"]:
            ctx=context(source);ctx["current_news"]["title"]="Eni turning on the taps at African gas project"
            out=analysis("flat","未来项目当前尚未投产","远期供给需等待交付")
            out["news_impact"]["summary"]="该项目尚未投产，当前交付量未给。"
            with self.subTest(source=source):self.assertValid(out,ctx)

    def test_actual_live_future_well_plan_cannot_become_completed_fact(self):
        fixture=json.loads((Path(__file__).parent/'fixtures'/'live_future_well_action_as_completed.json').read_text())
        before=copy.deepcopy(fixture['raw_result'])
        with self.assertRaisesRegex(ValueError,'来源仅说该井将封堵/弃置'):
            validate_analysis_semantics(fixture['raw_result'],fixture['input_snapshot'],{})
        self.assertTrue(any('news_impact.summary:N0来源仅说该井将封堵/弃置' in error
                            for error in collect_analysis_semantic_errors(fixture['raw_result'],fixture['input_snapshot'],{})))
        self.assertEqual(fixture['raw_result'],before)

    def test_well_plugging_keeps_future_completed_and_conditional_scopes(self):
        ctx=context('The well, which was not formation-tested, will now be permanently plugged and abandoned.')
        for claim in ['[N0]该井已被永久封堵。','[N0]该井已经完成永久封堵。',
                      '[N0]The well has been permanently plugged and abandoned.',
                      '[N0]The well was permanently plugged and abandoned.']:
            with self.subTest(claim=claim),self.assertRaisesRegex(ValueError,'不能写已被永久封堵'):
                validate_analysis_semantics(self.notice_output(claim),ctx,{})
        for claim in ['[N0]该井将永久封堵并弃置，不能当作已供气事实。',
                      '[N0]该井尚未被永久封堵，来源只给未来计划。',
                      '[N0]无法确认该井已被永久封堵。',
                      '[N0]若该井已被永久封堵，则需核对交付状态。']:
            with self.subTest(claim=claim):self.assertValid(self.notice_output(claim),ctx)
        for source in ['The well has been permanently plugged and abandoned.',
                       'The well was permanently plugged and abandoned.',
                       '该井已经完成永久封堵并弃置。']:
            with self.subTest(source=source):self.assertValid(self.notice_output('[N0]该井已被永久封堵。'),context(source))
        # A future statement about N1 is not proof that N0 was completed.
        out=self.notice_output('[N1]另一口井已被永久封堵。')
        self.assertValid(out,ctx,{'window_news':[{'id':'N1','title':'Other well','facts':'The other well has been permanently plugged and abandoned.'}]})

    def test_actual_manual_troll_production_notice_is_not_pipeline_maintenance(self):
        from ttf_news.analysis import prepare_context
        fixture=json.loads((Path(__file__).parent/'fixtures'/'live_troll_field_not_pipeline.json').read_text())
        prepared,_=prepare_context(fixture['input_snapshot'])
        before=copy.deepcopy(fixture['raw_result'])
        with self.assertRaisesRegex(ValueError,'N1来源设施类型为Gas production field'):
            validate_analysis_semantics(fixture['raw_result'],fixture['input_snapshot'],prepared)
        self.assertTrue(any('pipeline/管道维护' in error for error in collect_analysis_semantic_errors(fixture['raw_result'],fixture['input_snapshot'],prepared)))
        self.assertEqual(fixture['raw_result'],before)

    def test_facility_type_is_per_notice_not_borrowed_from_another_pipeline(self):
        rows=[{'id':'N1','source':'gassco','title':'Gassco Troll: Planned Gas production field unavailability (Active)'},
              {'id':'N2','source':'gassco','title':'Gassco Kårstø: Planned Gas treatment plant unavailability (Active)'},
              {'id':'N3','source':'gassco','title':'Gassco Unknown: Planned Other unavailability (Active)'},
              {'id':'N4','source':'gassco','title':'Gassco Pipe: Planned Pipeline unavailability (Active)'},
              {'id':'N5','source':'gassco','title':'Gassco Troll: Planned Pipeline unavailability (Active)'}]
        ctx=context('LNG Canada awarded a contract for the Coastal GasLink pipeline.')
        for claim in ['[N1]Troll管道未来维护计划。','[N2]Kårstø管道维护公告。','[N3]该管道计划维护。',
                      '[N1]Troll pipeline maintenance is planned.']:
            with self.subTest(claim=claim),self.assertRaisesRegex(ValueError,'不得称Troll等该实体为pipeline/管道维护'):
                validate_analysis_semantics(self.notice_output(claim),ctx,{'window_news':rows})
        for claim in ['[N1]Troll气田维护公告，当前流量待核实。','[N2]Kårstø处理厂维护公告。',
                      '[N3]Unknown设施公告未明确类别。','[N4]Pipe管道维护公告。',
                      '[N5]Troll管道维护公告。','[N1]气田容量风险可能经欧洲管道输送渠道传导。']:
            with self.subTest(claim=claim):self.assertValid(self.notice_output(claim),ctx,{'window_news':rows})

    def test_independent_current_flow_observation_can_confirm_restore_or_loss(self):
        for text, claim in [("计量确认Troll实际流量已恢复。", "[N0]实际流量已恢复"),
                            ("计量确认Troll实际流量减少5mcm/d。", "[N0]实际流量减少5mcm/d")]:
            ctx = self.notice_context(fact_excerpt=text)
            with self.subTest(text=text):self.assertValid(self.notice_output(claim), ctx)

    def test_unverified_or_other_facility_flow_cannot_confirm_notice_recovery(self):
        for extra in [{"fact_excerpt": "Troll实际流量恢复待核实。"}, {}]:
            ctx = self.notice_context("Dismissed", "dismissed_notice", **extra)
            ctx["window_news"] = [{"title": "Gassco Nyhamna: measured update", "summary": "计量确认Nyhamna实际流量恢复。", "published_at": "2026-10-20T11:30:00Z"}]
            with self.subTest(extra=extra), self.assertRaisesRegex(ValueError, "不能证明实际流量恢复"):
                validate_analysis_semantics(self.notice_output("[N0]Troll实际流量已恢复"), ctx, {})


if __name__ == "__main__":
    unittest.main()
