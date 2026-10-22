import re
import unittest

from ttf_news.prompts import NEWS_SYSTEM, PROMPT_VERSION, repair_prompt, system_prompt


class PricingEvidencePromptTests(unittest.TestCase):
    def test_pricing_rule_requires_evidence_in_both_directions(self):
        rule=re.search(r"^6\..*$",NEWS_SYSTEM,re.M).group()
        self.assertIn('“已计价”与“未计入价格/尚未计价”均须公告前后可比价格反应证据',rule)
        self.assertIn('不能由未知推断未计价',rule)
        self.assertIn('缺少证据统一写无法确认是否已计价',rule)
        self.assertIn('所有字段',rule)

    def test_missing_pricing_evidence_still_allows_supported_future_expectations(self):
        rule=re.search(r"^6\..*$",NEWS_SYSTEM,re.M).group()
        self.assertIn('可保留有依据的未来供需预期分析',rule)
        self.assertIn('不能当作事件因果',rule)

    def test_brief_detail_and_bounded_repair_receive_same_symmetric_rule(self):
        for detail in (False,True):
            with self.subTest(detail=detail):
                for prompt in (system_prompt(detail),repair_prompt('未计入价格缺反应证据',detail)):
                    self.assertIn('未计入价格/尚未计价',prompt)
                    self.assertIn('均须公告前后可比价格反应证据',prompt)
                    self.assertIn('无法确认是否已计价',prompt)
                    self.assertIn('可保留有依据的未来供需预期分析',prompt)

    def test_existing_prompt_version_is_preserved(self):
        self.assertEqual(PROMPT_VERSION,'ttf-news-standalone-2.3')
