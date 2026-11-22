"""Stdlib unit tests for feature_importance (grouping of RandomForest importances).

No model load, network, or third-party dependencies.
Run from price_api/: python -m unittest test_feature_importance_unit -v
"""

import unittest

from feature_importance import group_feature_importances, impact_label

COLUMNS = [
    "price_ma_3", "price_lag_1", "price_vol_6", "sin_month", "cos_month",
    "month", "quarter", "market", "latitude", "longitude", "admin1", "year",
]


class ImpactLabelTests(unittest.TestCase):
    def test_thresholds(self):
        cases = [
            (0.91, "Very High"), (0.5, "Very High"), (0.49, "High"), (0.2, "High"),
            (0.19, "Medium"), (0.05, "Medium"), (0.049, "Low"), (0.01, "Low"),
            (0.009, "Minimal"), (0.0, "Minimal"),
        ]
        for value, label in cases:
            with self.subTest(value=value):
                self.assertEqual(impact_label(value), label)


class GroupFeatureImportancesTests(unittest.TestCase):
    def test_groups_sum_per_factor(self):
        imps = [0.5, 0.1, 0.05, 0.02, 0.03, 0.01, 0.01, 0.1, 0.05, 0.05, 0.02, 0.06]
        result = dict(group_feature_importances(imps, COLUMNS))
        self.assertAlmostEqual(result["3-Month Price Average"], 0.5)
        self.assertAlmostEqual(result["Previous Month Price"], 0.1)
        self.assertAlmostEqual(result["Price Volatility"], 0.05)
        self.assertAlmostEqual(result["Seasonality"], 0.07)
        self.assertAlmostEqual(result["Market Location"], 0.22)
        self.assertAlmostEqual(result["Other"], 0.06)

    def test_sorted_descending(self):
        imps = [0.5, 0.1, 0.05, 0.02, 0.03, 0.01, 0.01, 0.1, 0.05, 0.05, 0.02, 0.06]
        grouped = group_feature_importances(imps, COLUMNS)
        values = [v for _, v in grouped]
        self.assertEqual(values, sorted(values, reverse=True))
        self.assertEqual(grouped[0][0], "3-Month Price Average")
        self.assertEqual(grouped[1][0], "Market Location")

    def test_other_omitted_when_all_columns_matched(self):
        grouped = group_feature_importances([0.6, 0.4], ["price_ma_3", "price_lag_1"])
        self.assertNotIn("Other", [f for f, _ in grouped])

    def test_other_omitted_when_unmatched_importance_is_zero(self):
        grouped = group_feature_importances([1.0, 0.0], ["price_ma_3", "mystery"])
        self.assertNotIn("Other", [f for f, _ in grouped])

    def test_unmatched_columns_go_to_other(self):
        grouped = dict(group_feature_importances([0.7, 0.2, 0.1], ["price_ma_3", "unit", "year"]))
        self.assertAlmostEqual(grouped["Other"], 0.3)

    def test_known_groups_always_present_even_if_zero(self):
        grouped = dict(group_feature_importances([1.0], ["price_ma_3"]))
        self.assertEqual(set(grouped), {
            "3-Month Price Average", "Previous Month Price", "Price Volatility",
            "Seasonality", "Market Location",
        })
        self.assertEqual(grouped["Market Location"], 0.0)

    def test_rounding(self):
        grouped = dict(group_feature_importances([0.123456789], ["price_ma_3"]))
        self.assertEqual(grouped["3-Month Price Average"], 0.1235)
        grouped = dict(group_feature_importances([0.123456789], ["price_ma_3"], ndigits=3))
        self.assertEqual(grouped["3-Month Price Average"], 0.123)

    def test_length_mismatch_raises(self):
        with self.assertRaises(ValueError):
            group_feature_importances([0.5], ["price_ma_3", "price_lag_1"])


if __name__ == "__main__":
    unittest.main()
