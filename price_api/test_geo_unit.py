"""Stdlib unit tests for geo.py (haversine_km and markets_within_radius).

No model, network, or third-party dependencies. The coordinates below are copied from
app.py MARKET_COORDS (WFP) so this file does not need to import the FastAPI app.
Run from price_api/: python -m unittest test_geo_unit -v
"""

import unittest

from geo import haversine_km, markets_within_radius

COORDS = {
    "Nairobi": (-1.28, 36.82),
    "Wakulima (Nairobi)": (-1.29, 36.83),
    "Kangemi (Nairobi)": (-1.27, 36.74),
    "Kitengela (Kajiado)": (-1.29, 36.86),
    "Nakuru": (-0.28, 36.07),
    "Kisumu": (-0.10, 34.75),
    "Lodwar town": (3.12, 35.60),
}


class HaversineTests(unittest.TestCase):
    def test_zero_distance(self):
        self.assertEqual(haversine_km(-1.28, 36.82, -1.28, 36.82), 0.0)

    def test_one_degree_of_latitude(self):
        self.assertAlmostEqual(haversine_km(0, 0, 1, 0), 111.19, places=1)

    def test_one_degree_of_longitude_at_equator(self):
        self.assertAlmostEqual(haversine_km(0, 36, 0, 37), 111.19, places=1)

    def test_symmetry(self):
        a = haversine_km(-1.28, 36.82, -0.28, 36.07)
        b = haversine_km(-0.28, 36.07, -1.28, 36.82)
        self.assertAlmostEqual(a, b, places=9)

    def test_nairobi_to_nakuru_straight_line(self):
        # Nairobi (-1.28, 36.82) to Nakuru (-0.28, 36.07), both from MARKET_COORDS.
        # Hand check: ~111.2 km north-south, ~83.4 km east-west -> ~139 km.
        self.assertAlmostEqual(haversine_km(*COORDS["Nairobi"], *COORDS["Nakuru"]), 139.0, delta=1.0)

    def test_nairobi_to_kisumu_is_realistic(self):
        self.assertTrue(260 < haversine_km(-1.28, 36.82, -0.10, 34.75) < 275)

    def test_antipodal_points_do_not_raise(self):
        self.assertAlmostEqual(haversine_km(0, 0, 0, 180), 20015.1, places=0)


class MarketsWithinRadiusTests(unittest.TestCase):
    def test_excludes_origin(self):
        names = [m["market"] for m in markets_within_radius("Nairobi", COORDS, 50)]
        self.assertNotIn("Nairobi", names)

    def test_only_markets_inside_radius(self):
        names = [m["market"] for m in markets_within_radius("Nairobi", COORDS, 50)]
        self.assertEqual(set(names), {"Wakulima (Nairobi)", "Kangemi (Nairobi)", "Kitengela (Kajiado)"})

    def test_sorted_nearest_first_and_rounded_to_one_dp(self):
        result = markets_within_radius("Nairobi", COORDS, 50)
        distances = [m["distance_km"] for m in result]
        self.assertEqual(distances, sorted(distances))
        for d in distances:
            self.assertEqual(d, round(d, 1))
        self.assertEqual(result[0]["market"], "Wakulima (Nairobi)")

    def test_distances_do_not_depend_on_radius(self):
        # Guards against the old fabricated pattern (distance = radius * constant).
        small = {m["market"]: m["distance_km"] for m in markets_within_radius("Nairobi", COORDS, 50)}
        large = {m["market"]: m["distance_km"] for m in markets_within_radius("Nairobi", COORDS, 500)}
        for name, d in small.items():
            self.assertEqual(large[name], d)

    def test_larger_radius_includes_more(self):
        names = {m["market"] for m in markets_within_radius("Nairobi", COORDS, 200)}
        self.assertIn("Nakuru", names)
        self.assertNotIn("Kisumu", names)

    def test_remote_origin_has_no_neighbours(self):
        self.assertEqual(markets_within_radius("Lodwar town", COORDS, 50), [])

    def test_zero_radius_returns_empty(self):
        self.assertEqual(markets_within_radius("Nairobi", COORDS, 0), [])

    def test_limit_caps_results(self):
        result = markets_within_radius("Nairobi", COORDS, 500, limit=2)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["market"], "Wakulima (Nairobi)")

    def test_limit_zero_returns_empty(self):
        self.assertEqual(markets_within_radius("Nairobi", COORDS, 500, limit=0), [])

    def test_unknown_origin_raises_key_error(self):
        with self.assertRaises(KeyError):
            markets_within_radius("Atlantis", COORDS, 50)

    def test_invalid_radius_raises_value_error(self):
        for bad in (-1, float("nan"), float("inf"), None):
            with self.assertRaises(ValueError):
                markets_within_radius("Nairobi", COORDS, bad)

    def test_boundary_is_inclusive(self):
        d = haversine_km(*COORDS["Nairobi"], *COORDS["Nakuru"])
        names = [m["market"] for m in markets_within_radius("Nairobi", COORDS, d)]
        self.assertIn("Nakuru", names)


if __name__ == "__main__":
    unittest.main()
