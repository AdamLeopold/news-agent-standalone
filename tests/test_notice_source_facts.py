import copy
import unittest

from ttf_news.analysis import _validate_notice_source_facts


def sources():
    current = {"id":"N0","source":"gassco","title":"Gassco Nyhamna: Planned Other unavailability (Active)",
               "operational":{"technical_capacity":"79.8","available_capacity":"60.0","unavailable_capacity":"19.8"}}
    others = [{"id":"N2","source":"gassco","title":"Gassco Troll: Unplanned unavailability (Active)",
               "operational":{"technical_capacity":"137.4","available_capacity":"130.8","unavailable_capacity":"6.6","announced_end_passed":True}},
              {"id":"N3","source":"gassco","title":"Gassco Åsgard: Unplanned unavailability (Active)","facts":"Reason: Yearly maintenance. Remarks: Planned maintenance prolonged.",
               "operational":{"technical_capacity":"29.2","available_capacity":"24.0","unavailable_capacity":"5.2"}},
              {"id":"N6","same_as_current_news_id":"N0"}]
    return {"current_news":current,"window_news":others}


class NoticeSourceFactTests(unittest.TestCase):
    def check(self, text, data=None):
        data = data or sources()
        result = {"analysis":{"evidence":[text]}}
        before = copy.deepcopy(result)
        _validate_notice_source_facts(result,{},data)
        self.assertEqual(before,result)

    def test_each_notice_uses_own_capacity_not_n0(self):
        self.check("[N3]Åsgard年检导致5.2 mcm/d容量受限 → 供给风险")
        self.check("[N2]Troll公告不可用容量6.6 mcm/d，公告时段已结束，恢复待核实")
        self.check("[N6]不可用容量19.8mcm/d → 潜在供给风险")

    def test_swapped_other_notice_capacity_is_rejected(self):
        with self.assertRaisesRegex(ValueError,"N3.*5.2"):
            self.check("[N3]Åsgard不可用容量19.8mcm/d → 潜在供给风险")

    def test_capacity_values_must_match_field_not_just_any_allowed_value(self):
        with self.assertRaisesRegex(ValueError,"unavailable.*19.8"):
            self.check("[N0]不可用容量60.0mcm/d → 潜在供给风险")
        with self.assertRaisesRegex(ValueError,"available.*60.0"):
            self.check("[N0]可用容量19.8mcm/d → 潜在供给风险")
        self.check("[N0]技术容量79.8，可用60.0，不可用19.8mcm/d")

    def test_level_and_change_are_different_capacity_fields(self):
        self.check("[N0]可用容量下降到60.0mcm/d → 容量风险")
        self.check("[N0]可用容量下降19.8mcm/d → 容量风险")
        with self.assertRaisesRegex(ValueError,"available.*60.0"):
            self.check("[N0]可用容量下降到19.8mcm/d → 容量风险")

    def test_unaliased_facility_names_bind_each_capacity_to_its_source(self):
        self.check("Åsgard不可用容量5.2mcm/d → 潜在供给风险")
        with self.assertRaisesRegex(ValueError,"N3.*unavailable.*5.2"):
            self.check("Åsgard不可用容量19.8mcm/d → 潜在供给风险")
        self.check("Nyhamna与Åsgard两处年检导致可用容量分别下降19.8 mcm/d和5.2 mcm/d，形成约25 mcm/d的供给约束，推高TTF价格")
        with self.assertRaisesRegex(ValueError,"N0.*unavailable.*19.8"):
            self.check("Nyhamna与Åsgard两处年检导致可用容量分别下降5.2 mcm/d和19.8 mcm/d，形成约25 mcm/d的供给约束")

    def test_aggregate_or_historical_value_not_compared_to_n0(self):
        self.check("本窗口合计约37mcm/d容量受限")
        self.check("[N0]与[N3]合计25mcm/d容量受限")
        self.check("[E1]历史不可用容量10.8mcm/d → 类比需核实")

    def test_yearly_maintenance_reason_does_not_make_type_planned(self):
        self.check("[N3]Åsgard年检延长，公告为非计划不可用，5.2mcm/d容量受限")
        with self.assertRaisesRegex(ValueError,"N3.*Unplanned"):
            self.check("[N3]Åsgard计划停机导致5.2mcm/d容量受限")
        with self.assertRaisesRegex(ValueError,"N0.*Planned"):
            self.check("[N0]Nyhamna非计划停机导致19.8mcm/d容量受限")

    def test_ended_notice_cannot_be_claimed_active_outage(self):
        with self.assertRaisesRegex(ValueError,"N2.*时段已结束"):
            self.check("[N2]Troll未计划停产6.6mcm/d → 当前供给受限")
        self.check("[N2]Troll非计划停产公告时段已结束，实际流量恢复待核实")

    def test_ended_capacity_notice_is_not_current_price_constraint(self):
        for text in ["[N2] Gassco Troll: 6.6mcm/d unavailable→供应受限，推高TTF",
                     "[N2] Troll公告不可用容量6.6mcm/d → 当前活跃容量约束，推高TTF"]:
            with self.subTest(text=text), self.assertRaisesRegex(ValueError,"N2.*时段已结束"):
                self.check(text)
        self.check("[N2] Troll不可用容量6.6mcm/d的公告时段已结束，实际流量恢复待核实")
        self.check("[N2] 若Troll实际供给仍受限，则TTF上行压力可能持续")


if __name__=='__main__':unittest.main()
