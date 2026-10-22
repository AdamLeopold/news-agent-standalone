"""Literal source roles and tense remain facts, without prescribing prices."""

import copy
import unittest

from ttf_news import semantics
from ttf_news.analysis import prepare_context
from ttf_news.source_facts import award_roles, reported_source_facts


AWARD_TITLE = "LNG Canada entrusts Técnicas Reunidas with €200M EPCM contract"
AWARD_BODY = (
    "Técnicas Reunidas has secured the EPCM contract. "
    "The contract was awarded by LNG Canada."
)


def award_item(**extra):
    return {
        "id": "source-award",
        "title": AWARD_TITLE,
        "body": AWARD_BODY,
        "published_at": "2026-10-19T08:00:00Z",
        **extra,
    }


def source_context(item, window_news=None):
    return {
        "as_of": "2026-10-21T12:00:00Z",
        "analysis_kind": "manual",
        "current_news": item,
        "window_news": window_news or [],
        "historical_events": [],
        "previous_sessions": [],
        "price_reference": {},
    }


def output(claim, direction="flat"):
    return {
        "news_impact": {"direction": direction, "summary": claim},
        "close_forecast": {"direction": direction},
    }


class LiteralSourceFactTests(unittest.TestCase):
    def test_entrusts_title_preserves_exact_parties_and_source_quote(self):
        item = award_item()
        before = copy.deepcopy(item)
        self.assertEqual(
            award_roles(item),
            [{
                "grantor": "LNG Canada",
                "recipient": "Técnicas Reunidas",
                "source_quote": AWARD_TITLE,
                "rendering_zh": "原报道授予/委托方为LNG Canada，获授/承接方为Técnicas Reunidas",
            }],
        )
        self.assertEqual(item, before)

    def test_awarded_by_body_without_entrusts_title_retains_literal_roles(self):
        roles = award_roles(award_item(title="EPCM contract announcement"))
        self.assertEqual(len(roles), 1)
        self.assertEqual(roles[0]["grantor"], "LNG Canada")
        self.assertEqual(roles[0]["recipient"], "Técnicas Reunidas")
        self.assertEqual(roles[0]["source_quote"], "awarded by LNG Canada")

    def test_distinct_explicit_contracts_never_cross_their_parties(self):
        item = {
            "title": AWARD_TITLE,
            "body": "Shell entrusts Wood with a separate engineering contract",
        }
        roles = award_roles(item)
        self.assertEqual(
            [(row["grantor"], row["recipient"]) for row in roles],
            [("LNG Canada", "Técnicas Reunidas"), ("Shell", "Wood")],
        )
        self.assertEqual(roles[1]["source_quote"], item["body"])

    def test_separate_awarded_by_contracts_are_not_guessed_across_sentences(self):
        item = {
            "body": (
                "Técnicas Reunidas has secured an EPCM contract. "
                "It was awarded by LNG Canada.\n"
                "Wood has secured a separate engineering contract. "
                "It was awarded by Shell."
            )
        }
        self.assertEqual(award_roles(item), [])

    def test_future_well_action_keeps_publication_tense_after_report_date(self):
        item = {
            "title": "Exploration well result",
            "body": "The well will now be permanently plugged and abandoned.",
            "published_at": "2025-01-01T08:00:00Z",
        }
        before = copy.deepcopy(item)
        facts = reported_source_facts(item)
        self.assertEqual(facts, {"well_action": {
            "action": "permanent_plugging_and_abandonment",
            "tense": "future_in_report",
            "source_quote": "will now be permanently plugged and abandoned",
            "published_at": item["published_at"],
            "rendering_zh": "原报道说该井将永久封堵并弃井；该条来源未给完成更新，不能仅从报道日期已过推断已完成",
        }})
        self.assertEqual(item, before)

    def test_actual_completed_well_sources_do_not_receive_future_fact(self):
        for text in (
            "The well has been permanently plugged and abandoned.",
            "The well was permanently plugged and abandoned.",
            "The well will now be permanently plugged and abandoned. "
            "It has now been permanently plugged and abandoned.",
        ):
            with self.subTest(text=text):
                self.assertNotIn("well_action", reported_source_facts({"body": text}))

    def test_source_facts_only_include_applicable_literal_facts(self):
        self.assertEqual(reported_source_facts({"title": "Gas market update"}), {})
        facts = reported_source_facts(award_item())
        self.assertEqual(set(facts), {"contract_party_roles"})
        self.assertFalse(any(
            word in str(facts).casefold()
            for word in ("direction", "bullish", "bearish", "price_score")
        ))

    def test_prepare_context_adds_reported_facts_without_mutating_snapshot(self):
        well = {
            "id": "well-source",
            "title": "Exploration well result",
            "body": "The well will now be permanently plugged and abandoned.",
            "published_at": "2026-10-20T08:00:00Z",
        }
        plain = {
            "id": "plain-source", "title": "Corporate appointment",
            "body": "A board appointment was announced.",
            "published_at": "2026-10-20T09:00:00Z",
        }
        context = source_context(award_item(), [well, plain])
        before = copy.deepcopy(context)
        prepared, _ = prepare_context(context)
        self.assertEqual(context, before)
        self.assertEqual(
            prepared["current_news"]["source_reported_facts"],
            reported_source_facts(context["current_news"]),
        )
        self.assertEqual(
            prepared["window_news"][0]["source_reported_facts"]["well_action"]["tense"],
            "future_in_report",
        )
        self.assertNotIn("source_reported_facts", prepared["window_news"][1])
        for card in [prepared["current_news"], *prepared["window_news"]]:
            for key in ("direction", "news_direction", "close_direction", "contextual_news_score"):
                self.assertNotIn(key, card)


class ContractPartyGateTests(unittest.TestCase):
    def check(self, result, context=None, prepared=None):
        semantics._check_contract_party_roles(
            result, context or source_context(award_item()), prepared or {}
        )

    def test_grantor_must_not_be_written_as_recipient(self):
        for claim in (
            "[N0]LNG Canada获授约2亿欧元EPCM合同。",
            "[N0]LNG Canada获得Técnicas Reunidas授予的EPCM合同。",
        ):
            with self.subTest(claim=claim), self.assertRaises(ValueError):
                self.check(output(claim))

    def test_correct_award_roles_accept_accent_and_short_recipient_aliases(self):
        for recipient in ("Técnicas Reunidas", "Tecnicas Reunidas", "Técnicas", "Tecnicas", "Reunidas"):
            for claim in (
                f"[N0]LNG Canada授予{recipient}约2亿欧元EPCM合同。",
                f"[N0]{recipient}获授LNG Canada的EPCM合同。",
            ):
                with self.subTest(claim=claim):
                    self.check(output(claim))

    def test_contract_gate_does_not_require_party_mentions_or_choose_price_direction(self):
        for claim in (
            "[N0]该消息涉及工程管理合同，交付时点需后续核实。",
            "[N0]LNG Canada授予Técnicas Reunidas EPCM合同。",
        ):
            for direction in ("up", "down", "flat"):
                result = output(claim, direction)
                before = copy.deepcopy(result)
                with self.subTest(claim=claim, direction=direction):
                    self.check(result)
                    self.assertEqual(result, before)

    def test_explicit_other_source_scope_does_not_inherit_n0_roles(self):
        context = source_context(award_item())
        prepared = {"window_news": [{
            "id": "N1",
            "title": "Técnicas Reunidas entrusts LNG Canada with a separate contract",
            "time": "2026-10-20T08:00:00Z",
        }]}
        self.check(output("[N1]LNG Canada获授Técnicas Reunidas的合同。"), context, prepared)
        with self.assertRaises(ValueError):
            self.check(output("[N0]LNG Canada获授Técnicas Reunidas的合同。"), context, prepared)

    def test_correct_recipient_of_separate_source_contract_is_not_crossed(self):
        context = source_context(award_item())
        prepared = {"window_news": [{
            "id": "N1",
            "title": "Shell entrusts Wood with a separate engineering contract",
            "time": "2026-10-20T08:00:00Z",
        }]}
        self.check(output("[N1]Wood获授Shell工程合同。"), context, prepared)
        self.check(output("[N0]Técnicas Reunidas获授LNG Canada的EPCM合同。"), context, prepared)

    def test_multiple_explicit_contracts_in_same_source_keep_independent_roles(self):
        item = award_item(body="Shell entrusts Wood with a separate engineering contract")
        context = source_context(item)
        self.check(output("[N0]Wood获授Shell工程合同。"), context)
        self.check(output("[N0]Técnicas Reunidas获授LNG Canada的EPCM合同。"), context)
        for claim in ("[N0]LNG Canada获授EPCM合同。", "[N0]Shell获授Wood工程合同。"):
            with self.subTest(claim=claim), self.assertRaises(ValueError):
                self.check(output(claim), context)

    def test_rejection_preserves_result_source_and_prepared_snapshot(self):
        context = source_context(award_item())
        prepared, _ = prepare_context(context)
        result = output("[N0]LNG Canada获授EPCM合同。", "down")
        before = copy.deepcopy((result, context, prepared))
        with self.assertRaises(ValueError):
            self.check(result, context, prepared)
        self.assertEqual((result, context, prepared), before)

    def test_public_semantic_collector_includes_contract_role_gate(self):
        context = source_context(award_item())
        prepared, _ = prepare_context(context)
        result = output("[N0]LNG Canada获授EPCM合同。")
        with self.assertRaises(ValueError) as failure:
            self.check(result, context, prepared)
        self.assertIn(str(failure.exception), semantics.collect_analysis_semantic_errors(result, context, prepared))


if __name__ == "__main__":
    unittest.main()
