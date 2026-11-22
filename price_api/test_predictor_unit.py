"""Stdlib unit tests for predictor.py (champion loading, fallbacks, bands, confidence).

No network, no sklearn/joblib. Run from price_api/: python -m unittest test_predictor_unit -v
"""

import copy
import json
import math
import os
import tempfile
import unittest

import predictor as P

HERE = os.path.dirname(os.path.abspath(__file__))


def valid_champion(model_type="naive_last"):
    return {
        "schema_version": 1,
        "model_type": model_type,
        "model_file": None if model_type == "naive_last" else P.RF_MODEL_FILE,
        "created_at": "2026-11-22T05:14:48+00:00",
        "data_through": "2026-09",
        "backtest": {"window_months": 60, "n": 1508, "mape": 17.39,
                     "naive_mape": 17.39, "median_ape": 11.83},
        "error_bands": {
            "Tomatoes": {"n": 331, "ratio_p10": 0.76, "ratio_p50": 1.0, "ratio_p90": 1.25},
            "Potatoes (Irish)": {"n": 338, "ratio_p10": 0.82, "ratio_p50": 1.02, "ratio_p90": 1.34},
            "_ALL": {"n": 1508, "ratio_p10": 0.76, "ratio_p50": 1.0, "ratio_p90": 1.31},
        },
    }


class TempChampion(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, obj, name="champion.json"):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            if isinstance(obj, str):
                fh.write(obj)
            else:
                json.dump(obj, fh)
        return path

    def assert_default(self, champ):
        self.assertEqual(champ["source"], "default")
        self.assertIsNotNone(champ["load_problem"])
        self.assertEqual(champ["model_type"], "naive_last")
        band = champ["error_bands"]["_ALL"]
        self.assertEqual((band["ratio_p10"], band["ratio_p50"], band["ratio_p90"]), (0.75, 1.0, 1.35))


class LoadChampionTests(TempChampion):
    def test_valid_file_loads(self):
        champ = P.load_champion(self.write(valid_champion()))
        self.assertIsNone(champ["load_problem"])
        self.assertEqual(champ["source"], "file")
        self.assertEqual(champ["data_through"], "2026-09")

    def test_valid_random_forest_file_loads(self):
        champ = P.load_champion(self.write(valid_champion("random_forest")))
        self.assertIsNone(champ["load_problem"])
        self.assertEqual(champ["model_type"], "random_forest")

    def test_utf8_bom_is_accepted(self):
        path = os.path.join(self.dir, "bom.json")
        with open(path, "wb") as fh:
            fh.write(b"\xef\xbb\xbf" + json.dumps(valid_champion()).encode("utf-8"))
        champ = P.load_champion(path)
        self.assertIsNone(champ["load_problem"])
        self.assertEqual(champ["source"], "file")

    def test_generated_champion_json_loads_if_present(self):
        path = os.path.join(HERE, "artifacts", "champion.json")
        if not os.path.exists(path):
            self.skipTest("artifacts/champion.json not built yet")
        champ = P.load_champion(path)
        self.assertIsNone(champ["load_problem"], champ["load_problem"])
        self.assertIn("_ALL", champ["error_bands"])

    def test_missing_file_gives_safe_default(self):
        champ = P.load_champion(os.path.join(self.dir, "nope.json"))
        self.assert_default(champ)
        self.assertIn("not found", champ["load_problem"])

    def test_none_path_gives_safe_default(self):
        self.assert_default(P.load_champion(None))

    def test_corrupt_json_gives_safe_default(self):
        self.assert_default(P.load_champion(self.write("{not json")))

    def test_nan_constant_gives_safe_default(self):
        self.assert_default(P.load_champion(self.write('{"schema_version": NaN}')))

    def test_top_level_list_gives_safe_default(self):
        self.assert_default(P.load_champion(self.write("[1, 2, 3]")))

    def test_bad_schema_version(self):
        c = valid_champion(); c["schema_version"] = 3
        self.assert_default(P.load_champion(self.write(c)))

    def test_bool_schema_version_rejected(self):
        c = valid_champion(); c["schema_version"] = True
        self.assert_default(P.load_champion(self.write(c)))

    def test_missing_required_key(self):
        for key in P.REQUIRED_KEYS:
            c = valid_champion(); del c[key]
            with self.subTest(key=key):
                self.assert_default(P.load_champion(self.write(c)))

    def test_unknown_model_type(self):
        c = valid_champion(); c["model_type"] = "prophet"
        self.assert_default(P.load_champion(self.write(c)))

    def test_naive_with_model_file_rejected(self):
        c = valid_champion(); c["model_file"] = P.RF_MODEL_FILE
        self.assert_default(P.load_champion(self.write(c)))

    def test_rf_with_wrong_model_file_rejected(self):
        c = valid_champion("random_forest"); c["model_file"] = "../../etc/passwd"
        self.assert_default(P.load_champion(self.write(c)))

    def test_bad_created_at(self):
        c = valid_champion(); c["created_at"] = "yesterday"
        self.assert_default(P.load_champion(self.write(c)))

    def test_created_at_without_offset_rejected(self):
        c = valid_champion(); c["created_at"] = "2026-11-22T05:14:48"
        self.assert_default(P.load_champion(self.write(c)))

    def test_bad_data_through(self):
        for bad in ("2026-9", "2026-13", "2026-09-15", 202609):
            c = valid_champion(); c["data_through"] = bad
            with self.subTest(value=bad):
                self.assert_default(P.load_champion(self.write(c)))

    def test_backtest_problems(self):
        c = valid_champion(); c["backtest"]["n"] = -1
        self.assert_default(P.load_champion(self.write(c)))
        c = valid_champion(); del c["backtest"]["mape"]
        self.assert_default(P.load_champion(self.write(c)))
        c = valid_champion(); c["backtest"]["median_ape"] = "11"
        self.assert_default(P.load_champion(self.write(c)))

    def test_missing_all_band_rejected(self):
        c = valid_champion(); del c["error_bands"]["_ALL"]
        self.assert_default(P.load_champion(self.write(c)))

    def test_empty_bands_rejected(self):
        c = valid_champion(); c["error_bands"] = {}
        self.assert_default(P.load_champion(self.write(c)))

    def test_band_order_violation_rejected(self):
        c = valid_champion(); c["error_bands"]["Tomatoes"]["ratio_p10"] = 1.5
        self.assert_default(P.load_champion(self.write(c)))

    def test_non_positive_ratio_rejected(self):
        c = valid_champion(); c["error_bands"]["_ALL"]["ratio_p10"] = 0
        self.assert_default(P.load_champion(self.write(c)))
        c = valid_champion(); c["error_bands"]["_ALL"]["ratio_p10"] = -0.5
        self.assert_default(P.load_champion(self.write(c)))

    def test_band_n_must_be_int(self):
        c = valid_champion(); c["error_bands"]["Tomatoes"]["n"] = 3.5
        self.assert_default(P.load_champion(self.write(c)))

    def test_loader_never_raises_on_directory_path(self):
        self.assert_default(P.load_champion(self.dir))

    def test_loaded_champion_is_a_copy(self):
        path = self.write(valid_champion())
        champ = P.load_champion(path)
        champ["error_bands"]["_ALL"]["ratio_p10"] = 99
        self.assertEqual(P.load_champion(path)["error_bands"]["_ALL"]["ratio_p10"], 0.76)


class PredictorTests(unittest.TestCase):
    def setUp(self):
        self.champ = P.load_champion(None)  # default
        self.file_champ = copy.deepcopy(valid_champion())
        self.file_champ["source"] = "file"
        self.file_champ["load_problem"] = None

    def test_naive_forecast_is_last_price(self):
        out = P.Predictor(self.file_champ).predict({}, 100.0, "Tomatoes")
        self.assertEqual(out["predicted_price"], 100.0)
        self.assertEqual(out["model_used"], "naive_last")
        self.assertIsNone(out["fallback_reason"])

    def test_commodity_band_used(self):
        out = P.Predictor(self.file_champ).predict({}, 100.0, "Tomatoes")
        self.assertEqual(out["band_source"], "Tomatoes")
        self.assertAlmostEqual(out["lower"], 76.0)
        self.assertAlmostEqual(out["upper"], 125.0)

    def test_unknown_commodity_falls_back_to_all(self):
        out = P.Predictor(self.file_champ).predict({}, 100.0, "Bananas")
        self.assertEqual(out["band_source"], "_ALL")
        self.assertAlmostEqual(out["upper"], 131.0)

    def test_commodity_match_is_case_insensitive(self):
        out = P.Predictor(self.file_champ).predict({}, 100.0, "potatoes (irish)")
        self.assertEqual(out["band_source"], "Potatoes (Irish)")

    def test_schema1_file_ignores_pricetype_and_uses_commodity_band(self):
        out = P.Predictor(self.file_champ).predict({}, 100.0, "Tomatoes", "Wholesale")
        self.assertEqual(out["band_source"], "Tomatoes")
        self.assertAlmostEqual(out["upper"], 125.0)

    def test_champion_without_bands_uses_default_band(self):
        champ = copy.deepcopy(self.file_champ)
        champ["error_bands"] = {}
        out = P.Predictor(champ).predict({}, 100.0, "Tomatoes")
        self.assertEqual(out["band_source"], "default")
        self.assertAlmostEqual(out["lower"], 75.0)
        self.assertAlmostEqual(out["upper"], 135.0)

    def test_default_champion_reports_default_band(self):
        out = P.Predictor(self.champ).predict({}, 100.0, "Tomatoes")
        self.assertEqual(out["band_source"], "default")
        self.assertAlmostEqual(out["lower"], 75.0)
        self.assertAlmostEqual(out["upper"], 135.0)

    def test_bad_previous_price_raises_value_error(self):
        pred = P.Predictor(self.file_champ)
        for bad in (0, -1, 0.0, float("nan"), float("inf"), float("-inf"), True, "58.2", None):
            with self.subTest(price=bad):
                with self.assertRaises(ValueError):
                    pred.predict({}, bad, "Tomatoes")

    def test_non_string_commodity_raises(self):
        with self.assertRaises(ValueError):
            P.Predictor(self.file_champ).predict({}, 10.0, None)

    def test_rf_champion_uses_loader_and_caches(self):
        calls = {"loader": 0, "predict": 0}

        def loader():
            calls["loader"] += 1

            def fn(features):
                calls["predict"] += 1
                return features["x"] * 2

            return fn

        pred = P.Predictor(valid_champion("random_forest") | {"source": "file"}, model_loader=loader)
        out1 = pred.predict({"x": 40.0}, 100.0, "Tomatoes")
        out2 = pred.predict({"x": 50.0}, 100.0, "Tomatoes")
        self.assertEqual(out1["model_used"], "random_forest")
        self.assertEqual(out1["predicted_price"], 80.0)
        self.assertEqual(out2["predicted_price"], 100.0)
        self.assertIsNone(out1["fallback_reason"])
        self.assertEqual(calls["loader"], 1)
        self.assertEqual(calls["predict"], 2)
        self.assertAlmostEqual(out1["lower"], 80.0 * 0.76)

    def test_rf_loader_raising_falls_back_to_naive_with_reason(self):
        def loader():
            raise RuntimeError("corrupt pickle")

        pred = P.Predictor(valid_champion("random_forest") | {"source": "file"}, model_loader=loader)
        out = pred.predict({}, 100.0, "Tomatoes")
        self.assertEqual(out["model_used"], "naive_last")
        self.assertEqual(out["predicted_price"], 100.0)
        self.assertIsNotNone(out["fallback_reason"])
        self.assertIn("RuntimeError", out["fallback_reason"])
        self.assertEqual(out["band_source"], "Tomatoes")

    def test_rf_predict_function_raising_falls_back(self):
        def loader():
            def fn(features):
                raise ValueError("bad feature")
            return fn

        out = P.Predictor(valid_champion("random_forest") | {"source": "file"},
                          model_loader=loader).predict({}, 100.0, "Tomatoes")
        self.assertEqual(out["model_used"], "naive_last")
        self.assertIn("ValueError", out["fallback_reason"])

    def test_rf_non_finite_or_non_positive_output_falls_back(self):
        for bad in (float("nan"), float("inf"), 0.0, -5.0):
            with self.subTest(value=bad):
                pred = P.Predictor(valid_champion("random_forest") | {"source": "file"},
                                   model_loader=lambda bad=bad: (lambda f: bad))
                out = pred.predict({}, 100.0, "Tomatoes")
                self.assertEqual(out["model_used"], "naive_last")
                self.assertIsNotNone(out["fallback_reason"])

    def test_rf_champion_without_loader_falls_back(self):
        out = P.Predictor(valid_champion("random_forest") | {"source": "file"}).predict({}, 100.0, "Tomatoes")
        self.assertEqual(out["model_used"], "naive_last")
        self.assertIsNotNone(out["fallback_reason"])
        self.assertIn("RuntimeError", out["fallback_reason"])

    def test_rf_loader_retried_after_failure(self):
        attempts = {"n": 0}

        def loader():
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise OSError("transient")
            return lambda f: 77.0

        pred = P.Predictor(valid_champion("random_forest") | {"source": "file"}, model_loader=loader)
        self.assertEqual(pred.predict({}, 100.0, "Tomatoes")["model_used"], "naive_last")
        self.assertEqual(pred.predict({}, 100.0, "Tomatoes")["predicted_price"], 77.0)


def pricetype_champion(schema_version=2):
    champ = copy.deepcopy(valid_champion())
    champ["schema_version"] = schema_version
    champ["error_bands_by_pricetype"] = {
        "Tomatoes": {
            "Wholesale": {"n": 200, "ratio_p10": 0.6, "ratio_p50": 1.0, "ratio_p90": 1.5},
            "Retail": {"n": 120, "ratio_p10": 0.9, "ratio_p50": 1.0, "ratio_p90": 1.1},
        },
    }
    champ["source"] = "file"
    champ["load_problem"] = None
    return champ


class PricetypeBandTests(TempChampion):
    def loaded(self, champ):
        return P.load_champion(self.write(champ))

    def test_schema2_pricetype_band_used(self):
        out = P.Predictor(self.loaded(pricetype_champion())).predict({}, 100.0, "Tomatoes", "Wholesale")
        self.assertEqual(out["band_source"], "Tomatoes/Wholesale")
        self.assertAlmostEqual(out["lower"], 60.0)
        self.assertAlmostEqual(out["upper"], 150.0)

    def test_pricetype_match_is_case_insensitive(self):
        out = P.Predictor(self.loaded(pricetype_champion())).predict({}, 100.0, "tomatoes", "RETAIL")
        self.assertEqual(out["band_source"], "Tomatoes/Retail")
        self.assertAlmostEqual(out["upper"], 110.0)

    def test_missing_pricetype_falls_back_to_commodity_band(self):
        out = P.Predictor(self.loaded(pricetype_champion())).predict({}, 100.0, "Tomatoes", "Organic")
        self.assertEqual(out["band_source"], "Tomatoes")
        self.assertAlmostEqual(out["upper"], 125.0)

    def test_no_pricetype_in_request_uses_commodity_band(self):
        out = P.Predictor(self.loaded(pricetype_champion())).predict({}, 100.0, "Tomatoes")
        self.assertEqual(out["band_source"], "Tomatoes")

    def test_commodity_without_pricetype_bands_falls_back_to_all(self):
        out = P.Predictor(self.loaded(pricetype_champion())).predict({}, 100.0, "Bananas", "Wholesale")
        self.assertEqual(out["band_source"], "_ALL")

    def test_schema2_without_pricetype_key_loads(self):
        champ = pricetype_champion()
        del champ["error_bands_by_pricetype"]
        loaded = self.loaded(champ)
        self.assertEqual(loaded["source"], "file")
        out = P.Predictor(loaded).predict({}, 100.0, "Tomatoes", "Wholesale")
        self.assertEqual(out["band_source"], "Tomatoes")

    def test_schema2_empty_pricetype_key_loads(self):
        champ = pricetype_champion()
        champ["error_bands_by_pricetype"] = {}
        self.assertEqual(self.loaded(champ)["source"], "file")

    def test_invalid_pricetype_entry_rejects_file(self):
        bad_cases = [
            lambda c: c["error_bands_by_pricetype"]["Tomatoes"]["Wholesale"].update(ratio_p10=0),
            lambda c: c["error_bands_by_pricetype"]["Tomatoes"]["Wholesale"].update(ratio_p10=1.8),
            lambda c: c["error_bands_by_pricetype"]["Tomatoes"]["Wholesale"].pop("ratio_p90"),
            lambda c: c["error_bands_by_pricetype"]["Tomatoes"].update(Wholesale="not a band"),
            lambda c: c["error_bands_by_pricetype"].update(Tomatoes=["not", "an", "object"]),
            lambda c: c["error_bands_by_pricetype"].update(Tomatoes={"  ": c["error_bands_by_pricetype"]["Tomatoes"]["Retail"]}),
            lambda c: c["error_bands_by_pricetype"].update(_ALL={"Wholesale": c["error_bands"]["_ALL"]}),
            lambda c: c.update(error_bands_by_pricetype=["not", "an", "object"]),
        ]
        for i, break_it in enumerate(bad_cases):
            with self.subTest(case=i):
                champ = pricetype_champion()
                break_it(champ)
                loaded = self.loaded(champ)
                self.assertEqual(loaded["source"], "default")
                self.assertIsNotNone(loaded["load_problem"])

    def test_schema1_with_valid_pricetype_key_is_also_used(self):
        loaded = self.loaded(pricetype_champion(schema_version=1))
        self.assertEqual(loaded["source"], "file")
        out = P.Predictor(loaded).predict({}, 100.0, "Tomatoes", "Wholesale")
        self.assertEqual(out["band_source"], "Tomatoes/Wholesale")

    def test_non_string_pricetype_raises(self):
        with self.assertRaises(ValueError):
            P.Predictor(self.loaded(pricetype_champion())).predict({}, 10.0, "Tomatoes", 3)

    def test_default_champion_ignores_pricetype(self):
        out = P.Predictor(P.load_champion(None)).predict({}, 100.0, "Tomatoes", "Wholesale")
        self.assertEqual(out["band_source"], "default")


class ConfidenceTests(unittest.TestCase):
    def test_known_value(self):
        # band [0.75, 1.35] around 100: half-width 30% -> 70
        self.assertEqual(P.confidence_from_band(75.0, 135.0, 100.0), 70)

    def test_zero_width_is_100(self):
        self.assertEqual(P.confidence_from_band(100.0, 100.0, 100.0), 100)

    def test_clamped_to_0_100(self):
        self.assertEqual(P.confidence_from_band(0.0, 1000.0, 100.0), 0)
        self.assertEqual(P.confidence_from_band(-5000.0, 5000.0, 100.0), 0)

    def test_invalid_inputs_give_zero(self):
        self.assertEqual(P.confidence_from_band(75.0, 135.0, 0.0), 0)
        self.assertEqual(P.confidence_from_band(75.0, 135.0, -1.0), 0)
        self.assertEqual(P.confidence_from_band(135.0, 75.0, 100.0), 0)
        self.assertEqual(P.confidence_from_band(float("nan"), 135.0, 100.0), 0)
        self.assertEqual(P.confidence_from_band(75.0, float("inf"), 100.0), 0)

    def test_returns_int_in_range(self):
        for w in (0.0, 1.0, 10.0, 37.3, 99.9):
            c = P.confidence_from_band(100 - w, 100 + w, 100.0)
            self.assertIsInstance(c, int)
            self.assertTrue(0 <= c <= 100)

    def test_monotonic_non_increasing_in_width(self):
        prev = 101
        for step in range(0, 400):
            w = step * 0.25
            c = P.confidence_from_band(100 - w, 100 + w, 100.0)
            self.assertLessEqual(c, prev)
            prev = c

    def test_scale_invariant(self):
        a = P.confidence_from_band(75.0, 135.0, 100.0)
        b = P.confidence_from_band(750.0, 1350.0, 1000.0)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
