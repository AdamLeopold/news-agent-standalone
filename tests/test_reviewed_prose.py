"""Narrow regressions for errors found in the final real-model prose review.

These cases validate evidence statements rather than making the forecast less
likely to be flat.  Each rejected statement has a nearby valid formulation.
"""

import copy
import unittest

from ttf_news.semantics import validate_analysis_semantics


def review_context(news):
    return {
        "as_of": "2026-10-20T12:00:00Z",
        "analysis_kind": "manual",
        "current_news": {"id": "review-news", **news},
        "window_news": [],
        "historical_events": [],
        "price_reference": {},
    }


def review_output(direction="flat"):
    return {
        "news_impact": {
            "direction": direction,
            "contextual_news_score": {"up": 2, "down": -2, "flat": 0}[direction],
            "summary": "供需影响依据本次来源事实判断。",
            "bullish_evidence": [],
            "bearish_evidence": [],
            "neutralizing_evidence": [],
        },
        "close_forecast": {
            "direction": None,
            "evidence_status": "insufficient_evidence",
            "summary": "缺少同合约前日价格基准，暂不生成收盘方向。",
            "basis": "前日同合约价格基准未提供。",
        },
        "analysis": {
            "summary": "分析保留公告容量与实际流量之间的区别。",
            "evidence": [],
            "priced_in_comment": "未提供公告前后价格反应，无法确认是否已计价。",
            "historical_comparisons": [],
        },
        "risk_flags": [],
        "missing_fields": ["price_reference.market_baseline"],
    }


def notice_context(future=False):
    return review_context({
        "source": "gassco",
        "title": "Gassco Testplant: Planned other unavailability (Active)",
        "summary": "Technical capacity: 100 mcm/d; available: 80; unavailable: 20.",
        "event_status": "Active",
        "temporal_state": "scheduled_notice" if future else "active_notice",
        "event_start": "2027-05-22T04:00:00Z" if future else "2026-10-20T04:00:00Z",
        "event_end": "2027-05-25T04:00:00Z" if future else "2026-10-22T04:00:00Z",
        "operational": {
            "capacity_is_not_lost_flow": True,
            "technical_capacity": 100,
            "available_capacity": 80,
            "unavailable_capacity": 20,
        },
        "fact_excerpt": "No independent actual flow measurement or recovery confirmation is provided.",
    })


class ReviewedProseTests(unittest.TestCase):
    def assertValidUnchanged(self, result, ctx):
        original = copy.deepcopy(result)
        validate_analysis_semantics(result, ctx, {})
        self.assertEqual(result, original, "a prose check must reject or pass, never rewrite the direction")

    def test_increasing_supply_cannot_be_written_as_bullish(self):
        ctx = review_context({"summary": "公司合作将增加未来供应，新增欧洲实物交付尚未确认。"})
        for clause in [
            "该项目开启将增加供应 → TTF看涨机制",
            "新发现将提升供应 → TTF看涨机制",
            "合作扩大供应 → TTF看涨机制",
        ]:
            # Keep the total flat and omit a measured-flow quantity so this
            # catches the evidence's causal reversal, not a net-sign rule.
            result = review_output()
            result["news_impact"]["bullish_evidence"] = ["[N0] " + clause]
            with self.subTest(clause=clause), self.assertRaises(ValueError):
                validate_analysis_semantics(result, ctx, {})

        ctx = review_context({"summary": "运营方确认项目已投产，欧洲实际供气增加5 mcm/d。"})
        for clause in [
            "项目增加供应 → TTF下行压力",
            "项目提升供应 → TTF上涨压力减弱",
            "合作扩大供应 → 有利于缓解TTF上涨压力",
        ]:
            result = review_output("down")
            result["news_impact"]["bearish_evidence"] = ["[N0] " + clause]
            with self.subTest(clause=clause):
                self.assertValidUnchanged(result, ctx)

    def test_missing_flow_measurement_cannot_erase_active_capacity_risk(self):
        ctx = notice_context()
        for clause in [
            "[N0] 供给约束未确认流量损失 → TTF中性机制",
            "[N0] 未确认实际流量损失，因此供应无冲击",
        ]:
            result = review_output()
            result["news_impact"]["neutralizing_evidence"] = [clause]
            with self.subTest(clause=clause), self.assertRaises(ValueError):
                validate_analysis_semantics(result, ctx, {})

        for clause in [
            "[N0] 实际流量损失未确认，持续容量约束风险仍在，不能把未知当零冲击",
            "[N0] 容量受限20mcm/d，实际流量损失待核实，不能把公告容量当作实际损失",
        ]:
            result = review_output()
            result["analysis"]["evidence"] = [clause]
            with self.subTest(clause=clause):
                self.assertValidUnchanged(result, ctx)

    def test_negative_priced_in_claim_requires_price_reaction_evidence(self):
        ctx = notice_context(future=True)
        for clause in [
            "未来维护的实际流量影响未知，当前未计入价格。",
            "未来容量限制尚未被计价。",
        ]:
            result = review_output()
            result["news_impact"]["summary"] = clause
            with self.subTest(clause=clause), self.assertRaises(ValueError):
                validate_analysis_semantics(result, ctx, {})

        for clause in [
            "未来容量限制的实际流量影响未知，无法确认是否已计入价格。",
            "无法确认未来容量限制是否尚未计入价格。",
            "未计入当前实际供气损失，不代表未计入价格；是否已计价无法确认。",
        ]:
            result = review_output()
            result["news_impact"]["summary"] = clause
            with self.subTest(clause=clause):
                self.assertValidUnchanged(result, ctx)

    def test_future_capacity_quantity_is_not_planned_physical_loss(self):
        ctx = notice_context(future=True)
        for clause in [
            "[N0] 未来5月22日至25日计划停产20mcm/d，但未提供实际流量计量。",
            "[N0] 计划停产20mcm/d → 未来容量约束",
        ]:
            result = review_output()
            result["analysis"]["evidence"] = [clause]
            with self.subTest(clause=clause), self.assertRaises(ValueError):
                validate_analysis_semantics(result, ctx, {})

        for clause in [
            "[N0] 2027年5月22日至25日公告不可用容量20mcm/d，实际流量影响待核实。",
            "[N0] 未来容量受限20mcm/d，不能写成计划停产20mcm/d的实物流量损失。",
        ]:
            result = review_output()
            result["analysis"]["evidence"] = [clause]
            with self.subTest(clause=clause):
                self.assertValidUnchanged(result, ctx)


if __name__ == "__main__":
    unittest.main()
