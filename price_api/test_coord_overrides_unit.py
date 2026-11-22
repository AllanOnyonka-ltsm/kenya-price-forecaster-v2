"""Unit tests for geo.apply_coord_overrides and the (empty) MARKET_COORD_OVERRIDES table."""
import ast
import os
import unittest

from geo import apply_coord_overrides

BASE = {"Nairobi": (-1.28, 36.82), "Kitengela (Kajiado)": (-1.29, 36.86)}
HERE = os.path.dirname(os.path.abspath(__file__))


class ApplyCoordOverridesTests(unittest.TestCase):
    def test_empty_overrides_return_equal_copy(self):
        out = apply_coord_overrides(BASE, {})
        self.assertEqual(out, BASE)
        self.assertIsNot(out, BASE)

    def test_valid_override_replaces_only_that_market(self):
        # Test value only; not a claimed real-world coordinate.
        out = apply_coord_overrides(BASE, {"Kitengela (Kajiado)": {"lat": -1.5, "lon": 36.9, "source": "test"}})
        self.assertEqual(out["Kitengela (Kajiado)"], (-1.5, 36.9))
        self.assertEqual(out["Nairobi"], BASE["Nairobi"])
        self.assertEqual(BASE["Kitengela (Kajiado)"], (-1.29, 36.86))  # input not mutated

    def test_unknown_market_rejected(self):
        with self.assertRaises(ValueError):
            apply_coord_overrides(BASE, {"Atlantis": {"lat": 0.0, "lon": 37.0, "source": "x"}})

    def test_missing_or_blank_source_rejected(self):
        for o in ({"lat": -1.5, "lon": 36.9}, {"lat": -1.5, "lon": 36.9, "source": "  "}):
            with self.assertRaises(ValueError):
                apply_coord_overrides(BASE, {"Nairobi": o})

    def test_bad_numbers_rejected(self):
        for o in ({"lat": "abc", "lon": 36.9}, {"lon": 36.9}, {"lat": float("nan"), "lon": 36.9}):
            with self.assertRaises(ValueError):
                apply_coord_overrides(BASE, {"Nairobi": dict(o, source="x")})

    def test_outside_kenya_rejected(self):
        # Swapped lat/lon is the typical typo.
        with self.assertRaises(ValueError):
            apply_coord_overrides(BASE, {"Nairobi": {"lat": 36.82, "lon": -1.28, "source": "x"}})


class ShippedOverridesTableTests(unittest.TestCase):
    """Read app.py's literal without importing the app (no model load)."""

    def _table(self):
        tree = ast.parse(open(os.path.join(HERE, "app.py"), encoding="utf-8").read())
        for node in tree.body:
            if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == "MARKET_COORD_OVERRIDES":
                return ast.literal_eval(node.value)
        self.fail("MARKET_COORD_OVERRIDES not found in app.py")

    def test_table_is_empty_by_default(self):
        self.assertEqual(self._table(), {})


if __name__ == "__main__":
    unittest.main()
