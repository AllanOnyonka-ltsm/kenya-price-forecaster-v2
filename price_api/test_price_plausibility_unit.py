"""Unit tests for the /predict unit and plausibility fix.

Before the fix, /predict always labelled prices "kg" and flagged any price above CROP_THRESHOLDS,
whose values were really WFP pack sizes in kg (64 for a tomato crate). So a 7619 KES crate of
wholesale tomatoes and an 80 KES/kg retail potato price were both flagged "unreasonable".

Uses an in-memory price store, never artifacts/latest_prices.json.
Run from price_api/: python -m unittest test_price_plausibility_unit -v
"""

import unittest
from unittest import mock

from latest_prices import PLAUSIBLE_FACTOR, parse_latest_prices, plausibility


def _row(market, commodity, pricetype, price, unit, date="2026-09-15"):
    return {"market": market, "commodity": commodity, "pricetype": pricetype, "price": price,
            "unit": unit, "currency": "KES", "date": date}


PAYLOAD = {
    "schema_version": 1, "source": "test", "generated_at": "2026-11-22T00:00:00+00:00",
    "data_through": "2026-10", "n_entries": 6,
    "prices": [
        _row("Kibuye (Kisumu)", "Tomatoes", "Wholesale", 7619.0, "64 KG"),
        _row("Nakuru", "Tomatoes", "Wholesale", 1920.0, "64 KG"),
        _row("Hagadera (Daadab)", "Potatoes (Irish)", "Retail", 80.0, "KG", "2026-10-15"),
        _row("Kitui", "Potatoes (Irish)", "Retail", 35.0, "KG"),
        _row("Kitui", "Cabbage", "Retail", 50.0, "Head"),
        _row("Nakuru", "Cabbage", "Retail", 60.0, "Head"),
    ],
}


def _store():
    store, problem = parse_latest_prices(PAYLOAD)
    assert problem is None, problem
    return store


class SeriesUnitTests(unittest.TestCase):
    def test_own_market_unit_is_used(self):
        self.assertEqual(_store().series_unit("Kibuye (Kisumu)", "Tomatoes", "Wholesale"), "64 KG")

    def test_pricetype_is_case_insensitive(self):
        self.assertEqual(_store().series_unit("Hagadera (Daadab)", "Potatoes (Irish)", "retail"), "KG")

    def test_falls_back_to_most_common_unit_for_commodity(self):
        self.assertEqual(_store().series_unit("Mukuru (Nairobi)", "Tomatoes", "Wholesale"), "64 KG")

    def test_none_without_any_observation(self):
        self.assertIsNone(_store().series_unit("Nakuru", "Tomatoes", "Retail"))


class ReferenceRangeTests(unittest.TestCase):
    def test_range_across_markets_same_unit(self):
        self.assertEqual(_store().reference_range("Tomatoes", "Wholesale", "64 KG"), (1920.0, 7619.0, 2))

    def test_none_for_unknown_series(self):
        self.assertIsNone(_store().reference_range("Tomatoes", "Retail", "KG"))


class PlausibilityTests(unittest.TestCase):
    def test_inside_range_is_not_flagged(self):
        out = plausibility(7619.0, (1920.0, 7619.0, 2), "64 KG")
        self.assertFalse(out["unreasonable"])
        self.assertEqual(out["plausible_min"], round(1920.0 / PLAUSIBLE_FACTOR, 2))
        self.assertEqual(out["plausible_max"], round(7619.0 * PLAUSIBLE_FACTOR, 2))

    def test_far_outside_range_is_flagged_with_reason(self):
        out = plausibility(500.0, (35.0, 80.0, 2), "KG")
        self.assertTrue(out["unreasonable"])
        self.assertIn("per KG", out["note"])
        self.assertIn("35.0-80.0", out["note"])

    def test_no_reference_data_never_flags(self):
        for ref, unit in ((None, "KG"), ((1.0, 2.0, 1), None)):
            out = plausibility(10_000.0, ref, unit)
            self.assertFalse(out["unreasonable"])
            self.assertIn("not checked", out["note"])
            self.assertIsNone(out["plausible_min"])


class PredictEndpointTests(unittest.TestCase):
    """Calls the /predict function directly with LATEST_PRICES patched."""

    @classmethod
    def setUpClass(cls):
        import app as A
        cls.A = A

    def _predict(self, store, **kw):
        body = dict(date="2026-10-15", admin1="Nyanza", market="Kibuye (Kisumu)",
                    commodity="tomatoes", pricetype="Wholesale", previous_month_price=7619.0)
        body.update(kw)
        with mock.patch.object(self.A, "LATEST_PRICES", store):
            return self.A.predict(self.A.PredictRequest(**body))

    def test_wholesale_crate_price_is_not_flagged_and_unit_is_reported(self):
        out = self._predict(_store())
        self.assertFalse(out["unreasonable"])
        self.assertEqual(out["unit"], "64 KG")
        self.assertEqual(out["price_per_kg"], round(out["prediction_per_kg"] / 64, 2))

    def test_retail_potato_price_is_not_flagged(self):
        out = self._predict(_store(), admin1="North Eastern", market="Hagadera (Daadab)",
                            commodity="potatoes", pricetype="Retail", previous_month_price=80.0)
        self.assertFalse(out["unreasonable"])
        self.assertEqual(out["unit"], "KG")
        self.assertEqual(out["price_per_kg"], out["prediction_per_kg"])

    def test_crate_price_sent_as_retail_kg_is_flagged(self):
        out = self._predict(_store(), admin1="North Eastern", market="Hagadera (Daadab)",
                            commodity="potatoes", pricetype="Retail", previous_month_price=7619.0)
        self.assertTrue(out["unreasonable"])
        self.assertIn("plausible range", out["note"])

    def test_unit_without_weight_has_no_per_kg(self):
        out = self._predict(_store(), admin1="Eastern", market="Kitui", commodity="cabbage",
                            pricetype="Retail", previous_month_price=55.0)
        self.assertEqual(out["unit"], "Head")
        self.assertIsNone(out["price_per_kg"])
        self.assertFalse(out["unreasonable"])

    def test_price_file_disabled_does_not_flag(self):
        out = self._predict(None)
        self.assertFalse(out["unreasonable"])
        self.assertTrue(out["unit"].startswith("unknown"))
        self.assertIsNone(out["price_per_kg"])


if __name__ == "__main__":
    unittest.main()
