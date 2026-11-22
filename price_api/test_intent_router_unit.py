"""Stdlib unit tests for the current rule-parser and response behavior of intent_router.

No network, LLM, or third-party dependencies.
Run from price_api/: python -m unittest test_intent_router_unit -v
"""

import asyncio
import unittest

from intent_router import MENU_TEXT, Intent, ParsedIntent, build_response, parse_intent, rule_based_parse


class RuleBasedParseTests(unittest.TestCase):
    def assertParsed(self, text, intent, symbol=None, quantity=None, unit=None):
        parsed = rule_based_parse(text)
        self.assertIsNotNone(parsed)
        actual = (parsed.intent, parsed.symbol, parsed.quantity, parsed.unit, parsed.raw_text, parsed.via_llm)
        self.assertEqual(actual, (intent, symbol, quantity, unit, text, False))

    def test_greetings_in_english_and_swahili(self):
        self.assertParsed("hello", Intent.GREETING)
        self.assertParsed("Habari yako", Intent.GREETING)

    def test_greeting_takes_precedence_over_trade(self):
        self.assertParsed("hello sell 5 bags maize", Intent.GREETING)

    def test_sell_order_in_english(self):
        self.assertParsed("sell 50 bags of maize", Intent.SELL_ORDER, "MAIZE", 50.0, "bags")

    def test_buy_order_in_swahili_with_kg_unit(self):
        self.assertParsed("Nunua 10 kg viazi", Intent.BUY_ORDER, "POTATO", 10.0, "kg")

    def test_trade_unit_defaults_to_bags(self):
        self.assertParsed("sell 20 maize", Intent.SELL_ORDER, "MAIZE", 20.0, "bags")

    def test_decimal_quantity(self):
        self.assertParsed("buy 2.5 tonnes of beans", Intent.BUY_ORDER, "BEANS", 2.5, "tonnes")

    def test_trade_with_unknown_crop_has_no_symbol(self):
        self.assertParsed("sell 5 bags of gold", Intent.SELL_ORDER, None, 5.0, "bags")

    def test_price_query_with_crop(self):
        self.assertParsed("price of maize", Intent.PRICE_QUERY, "MAIZE")
        self.assertParsed("  Price of Maize  ", Intent.PRICE_QUERY, "MAIZE")

    def test_price_query_without_crop(self):
        self.assertParsed("bei", Intent.PRICE_QUERY)

    def test_bare_swahili_crop_is_price_query(self):
        self.assertParsed("maharagwe", Intent.PRICE_QUERY, "BEANS")

    def test_unclassifiable_text_returns_none(self):
        self.assertIsNone(rule_based_parse("random gibberish"))

    def test_parse_intent_uses_rules_without_llm(self):
        parsed = asyncio.run(parse_intent("sell 50 bags of maize"))
        self.assertEqual((parsed.intent, parsed.symbol, parsed.via_llm), (Intent.SELL_ORDER, "MAIZE", False))


class BuildResponseTests(unittest.TestCase):
    def test_payload_fields_are_copied(self):
        p = ParsedIntent(intent=Intent.BUY_ORDER, symbol="BEANS", quantity=2.5, unit="tonnes", raw_text="x", via_llm=True)
        expected = {"intent": Intent.BUY_ORDER, "symbol": "BEANS", "quantity": 2.5, "unit": "tonnes", "via_llm": True, "sms": None}
        self.assertEqual(build_response(p), expected)

    def test_greeting_returns_menu(self):
        self.assertEqual(build_response(ParsedIntent(intent=Intent.GREETING))["sms"], MENU_TEXT)

    def test_price_query_with_symbol_has_no_sms(self):
        self.assertIsNone(build_response(ParsedIntent(intent=Intent.PRICE_QUERY, symbol="MAIZE"))["sms"])

    def test_price_query_without_symbol_asks_for_crop(self):
        sms = build_response(ParsedIntent(intent=Intent.PRICE_QUERY))["sms"]
        self.assertIn("Which crop do you want prices for?", sms)

    def test_trade_without_symbol_asks_which_crop(self):
        sell_sms = build_response(ParsedIntent(intent=Intent.SELL_ORDER, quantity=5.0, unit="bags"))["sms"]
        buy_sms = build_response(ParsedIntent(intent=Intent.BUY_ORDER, quantity=5.0, unit="bags"))["sms"]
        self.assertIn("Which crop?", sell_sms)
        self.assertEqual(sell_sms, buy_sms)

    def test_trade_with_symbol_has_no_sms(self):
        self.assertIsNone(build_response(ParsedIntent(intent=Intent.SELL_ORDER, symbol="MAIZE", quantity=50.0, unit="bags"))["sms"])

    def test_unknown_returns_apology_plus_menu(self):
        sms = build_response(ParsedIntent(intent=Intent.UNKNOWN))["sms"]
        self.assertIn("Sijui hiyo", sms)
        self.assertTrue(sms.endswith("\n\n" + MENU_TEXT))


if __name__ == "__main__":
    unittest.main()
