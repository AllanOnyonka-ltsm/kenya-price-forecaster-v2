"""Unit tests for latest_prices.py (WFP price lookup for /micro-market).

Uses temp JSON files, never artifacts/latest_prices.json. The endpoint tests patch the module-level
LATEST_PRICES so no real file is read.
Run from price_api/: python -m unittest test_latest_prices_unit -v
"""

import json
import os
import tempfile
import unittest
from unittest import mock

from latest_prices import load_latest_prices, parse_latest_prices, price_per_kg, MAX_MONTHS_OLD

PAYLOAD = {
    "schema_version": 1,
    "source": "test",
    "generated_at": "2026-11-22T00:00:00+00:00",
    "data_through": "2026-10",
    "n_entries": 4,
    "prices": [
        {"market": "Nairobi", "commodity": "Tomatoes", "pricetype": "Retail", "price": 120.0,
         "unit": "64 KG", "currency": "KES", "date": "2026-10-15"},
        {"market": "Nairobi", "commodity": "Tomatoes", "pricetype": "Wholesale", "price": 90.0,
         "unit": "64 KG", "currency": "KES", "date": "2026-10-15"},
        {"market": "Kibra (Nairobi)", "commodity": "Tomatoes", "pricetype": "Retail", "price": 70.0,
         "unit": "64 KG", "currency": "KES", "date": "2026-07-01"},
        {"market": "Kisumu", "commodity": "Kale", "pricetype": "Retail", "price": 50.0,
         "unit": "KG", "currency": "KES", "date": "2026-03-15"},
    ],
}


def _store(payload=PAYLOAD):
    store, problem = parse_latest_prices(payload)
    assert problem is None, problem
    return store


class LookupTests(unittest.TestCase):
    def test_fresh_observation_is_shown(self):
        r = _store().estimate("Nairobi", "Tomatoes", "Retail")
        self.assertEqual(r["estimated_price"], 120.0)
        self.assertEqual(r["price_date"], "2026-10-15")
        self.assertEqual(r["price_unit"], "64 KG")
        self.assertEqual(r["price_pricetype"], "Retail")
        self.assertIn("not a forecast", r["price_note"])

    def test_stale_observation_is_null_with_note(self):
        # Kisumu Kale is 2026-03: 7 months before 2026-10, so null.
        r = _store().estimate("Kisumu", "Kale")
        self.assertIsNone(r["estimated_price"])
        self.assertIsNone(r["price_date"])
        self.assertIn("latest WFP observation is from 2026-03", r["price_note"])
        self.assertIn("data through 2026-10", r["price_note"])

    def test_four_months_old_is_stale_and_three_months_is_fresh(self):
        # Kibra (Nairobi) tomatoes are from 2026-07: 3 months before data_through, so fresh.
        self.assertEqual(_store().estimate("Kibra (Nairobi)", "Tomatoes")["estimated_price"], 70.0)
        four = dict(PAYLOAD, prices=[dict(PAYLOAD["prices"][2], date="2026-06-15")])
        r = _store(four).estimate("Kibra (Nairobi)", "Tomatoes")
        self.assertIsNone(r["estimated_price"])
        self.assertIn("2026-06", r["price_note"])
        self.assertEqual(MAX_MONTHS_OLD, 3)

    def test_missing_key_is_null_with_note(self):
        r = _store().estimate("Eldoret town (Uasin Gishu)", "Tomatoes")
        self.assertIsNone(r["estimated_price"])
        self.assertEqual(r["price_note"], "no WFP observation for this market/commodity")

    def test_missing_pricetype_is_null_with_note(self):
        r = _store().estimate("Kibra (Nairobi)", "Tomatoes", "Wholesale")
        self.assertIsNone(r["estimated_price"])
        self.assertIn("with pricetype Wholesale", r["price_note"])

    def test_no_pricetype_prefers_latest_then_wholesale_on_tie(self):
        # Nairobi has Retail and Wholesale on the same date: Wholesale wins the tie.
        r = _store().estimate("Nairobi", "Tomatoes")
        self.assertEqual(r["price_pricetype"], "Wholesale")
        self.assertEqual(r["estimated_price"], 90.0)

    def test_no_pricetype_takes_most_recent_across_pricetypes(self):
        payload = dict(PAYLOAD, prices=[
            dict(PAYLOAD["prices"][0], date="2026-08-01", pricetype="Retail"),
            dict(PAYLOAD["prices"][1], date="2026-09-01", pricetype="Wholesale"),
        ])
        r = _store(payload).estimate("Nairobi", "Tomatoes")
        self.assertEqual(r["price_pricetype"], "Wholesale")
        self.assertEqual(r["price_date"], "2026-09-01")

    def test_retail_tie_beats_alphabetical_other_pricetypes(self):
        payload = dict(PAYLOAD, prices=[
            dict(PAYLOAD["prices"][0], pricetype="Other"),
            dict(PAYLOAD["prices"][0], pricetype="Retail", price=11.0),
        ])
        self.assertEqual(_store(payload).estimate("Nairobi", "Tomatoes")["price_pricetype"], "Retail")

    def test_bad_rows_are_skipped_and_counted(self):
        payload = dict(PAYLOAD, prices=PAYLOAD["prices"] + [
            {"market": "X", "commodity": "Tomatoes", "pricetype": "Retail", "price": -1, "date": "2026-10-01"},
            {"market": "Y", "commodity": "Tomatoes", "pricetype": "Retail", "price": 5, "date": "not-a-date"},
            "junk",
        ])
        store = _store(payload)
        self.assertEqual(store.skipped_rows, 3)
        self.assertIsNone(store.estimate("X", "Tomatoes")["estimated_price"])


class PricePerKgTests(unittest.TestCase):
    def test_weighted_units_are_divided(self):
        self.assertEqual(price_per_kg(6400.0, "64 KG"), 100.0)
        self.assertEqual(price_per_kg(2733.33, "50 KG"), 54.67)
        self.assertEqual(price_per_kg(58.0, "KG"), 58.0)
        self.assertEqual(price_per_kg(10.0, " 2.5 kg "), 4.0)

    def test_units_without_fixed_weight_are_not_converted(self):
        for unit in ("Head", "Unit", "Bunch", None, "", "0 KG"):
            self.assertIsNone(price_per_kg(100.0, unit), unit)


class LoadTests(unittest.TestCase):
    def _write(self, text):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        self.addCleanup(os.remove, path)
        return path

    def test_valid_file_loads(self):
        store, problem = load_latest_prices(self._write(json.dumps(PAYLOAD)))
        self.assertIsNone(problem)
        self.assertEqual(store.data_through, "2026-10")

    def test_missing_file_disables_without_raising(self):
        store, problem = load_latest_prices(os.path.join(tempfile.gettempdir(), "no_such_latest_prices.json"))
        self.assertIsNone(store)
        self.assertIn("file not found", problem)

    def test_corrupt_file_disables_without_raising(self):
        store, problem = load_latest_prices(self._write("{not json"))
        self.assertIsNone(store)
        self.assertIn("unreadable", problem)

    def test_wrong_schema_or_data_through_disables(self):
        self.assertIsNone(parse_latest_prices(dict(PAYLOAD, schema_version=2))[0])
        self.assertIsNone(parse_latest_prices(dict(PAYLOAD, data_through="2026-13x"))[0])
        self.assertIsNone(parse_latest_prices([])[0])


class MicroMarketEndpointTests(unittest.TestCase):
    """Calls the endpoint function directly (no TestClient), with LATEST_PRICES patched."""

    @classmethod
    def setUpClass(cls):
        import app as A  # imported once; loads the real artifacts lazily, not used here
        cls.A = A

    def _call(self, store, **kw):
        from app import MicroMarketRequest
        req = MicroMarketRequest(commodity="tomatoes", region="Nairobi", radius_km=400, date="2026-11-01", **kw)
        with mock.patch.object(self.A, "LATEST_PRICES", store):
            return self.A.get_micro_market_forecast(req)

    def _by_name(self, out):
        return {m["market_name"]: m for m in out["nearby_markets"]}

    def test_endpoint_shows_fresh_and_nulls_stale_and_missing(self):
        out = self._call(_store())
        markets = self._by_name(out)
        self.assertIn("Kibra (Nairobi)", markets)
        self.assertEqual(markets["Kibra (Nairobi)"]["estimated_price"], 70.0)
        self.assertEqual(markets["Kibra (Nairobi)"]["price_date"], "2026-07-01")
        self.assertEqual(markets["Kibra (Nairobi)"]["price_unit"], "64 KG")
        self.assertEqual(markets["Kibra (Nairobi)"]["price_pricetype"], "Retail")
        self.assertEqual(out["price_data_through"], "2026-10")
        for name, m in markets.items():
            if name != "Kibra (Nairobi)":
                self.assertIsNone(m["estimated_price"])
                self.assertTrue(m["price_note"])

    def test_endpoint_pricetype_request_field_is_used(self):
        markets = self._by_name(self._call(_store(), pricetype="wholesale"))
        self.assertIsNone(markets["Kibra (Nairobi)"]["estimated_price"])
        self.assertIn("with pricetype Wholesale", markets["Kibra (Nairobi)"]["price_note"])

    def test_endpoint_with_price_file_disabled(self):
        out = self._call(None)
        self.assertIsNone(out["price_data_through"])
        for m in out["nearby_markets"]:
            self.assertIsNone(m["estimated_price"])
            self.assertEqual(m["price_note"], self.A.MICRO_MARKET_PRICE_NOTE)
        self.assertIn("not compared", out["market_comparison"])


if __name__ == "__main__":
    unittest.main()
