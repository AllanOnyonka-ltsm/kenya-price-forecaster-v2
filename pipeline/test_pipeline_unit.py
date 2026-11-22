"""Unit tests for the retrain pipeline (stdlib unittest; pandas/sklearn needed at runtime).

Run from the repo root:  python -m unittest discover -s pipeline -v
No network access: downloads are tested with fake openers. Synthetic CSVs live in temp dirs.
"""
import ast
import csv
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import features as F  # noqa: E402
import retrain as R  # noqa: E402
from validate import validate_snapshot  # noqa: E402

RETRAIN = os.path.join(HERE, "retrain.py")
HEADER = ["date", "admin1", "admin2", "market", "market_id", "latitude", "longitude", "category", "commodity",
          "commodity_id", "unit", "priceflag", "pricetype", "currency", "price", "usdprice"]

# (raw commodity, unit, category)
COMBOS = [
    ("Tomatoes", "64 KG", "vegetables and fruits"),
    ("Kale", "KG", "vegetables and fruits"),
    ("Potatoes (Irish, red)", "50 KG", "cereals and tubers"),
    ("Cabbage", "126 KG", "vegetables and fruits"),
    ("Bananas", "Unit", "cereals and tubers"),
    ("Onions (dry)", "13 KG", "vegetables and fruits"),
    ("Maize", "KG", "cereals and tubers"),  # not tracked: must be filtered out
]
MARKETS = [("Nairobi", "Nairobi", -1.28, 36.82), ("Mombasa", "Coast", -4.05, 39.67)]


def make_csv(path, months=36, seed=0, hxl=True):
    """Synthetic WFP-shaped CSV: monthly rows per market x commodity, random-walk prices."""
    rng = random.Random(seed)
    rows = []
    for raw, unit, cat in COMBOS:
        for mkt, adm, lat, lon in MARKETS:
            p = 50 + rng.random() * 50
            for i in range(months):
                y, m = 2020 + i // 12, i % 12 + 1
                p *= np.exp(rng.gauss(0.005, 0.05))
                rows.append([f"{y}-{m:02d}-15", adm, mkt, mkt, "1", str(lat), str(lon), cat, raw, "1", unit,
                             "actual", "Retail", "KES", f"{p:.2f}", "0.1"])
    rows.sort(key=lambda r: (r[0], r[3], r[8]))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        if hxl:
            w.writerow(["#date"] + ["#x"] * (len(HEADER) - 1))
        w.writerows(rows)
    return path


def load(path):
    return F.read_wfp_csv(path)


def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="retrain_test_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def p(self, name):
        return os.path.join(self.tmp, name)


# ---------------------------------------------------------------- validation
class ValidateTests(TempDirTest):
    def setUp(self):
        super().setUp()
        self.prev = load(make_csv(self.p("prev.csv")))

    def test_clean_file_passes_and_hxl_row_is_dropped(self):
        res = validate_snapshot(self.prev.copy(), self.prev)
        self.assertTrue(res["ok"], res["problems"])
        self.assertNotIn("#date", set(self.prev["date"]))

    def test_missing_column(self):
        new = self.prev.drop(columns=["price"])
        res = validate_snapshot(new, self.prev)
        self.assertFalse(res["ok"])
        self.assertTrue(any("price" in p for p in res["problems"]))

    def test_row_count_shrink(self):
        new = self.prev.sample(frac=0.90, random_state=1)
        res = validate_snapshot(new, self.prev)
        self.assertFalse(res["ok"])
        self.assertTrue(any("shrank" in p for p in res["problems"]))

    def test_small_shrink_within_5_percent_is_allowed(self):
        new = self.prev.sample(frac=0.97, random_state=1)
        res = validate_snapshot(new, self.prev)
        self.assertFalse(any("shrank" in p for p in res["problems"]), res["problems"])

    def test_max_date_backwards(self):
        new = self.prev.copy()
        new.loc[new["date"] >= "2021-12-01", "date"] = "2021-06-15"
        res = validate_snapshot(new, self.prev)
        self.assertFalse(res["ok"])
        self.assertTrue(any("backwards" in p for p in res["problems"]))

    def test_negative_price(self):
        new = self.prev.copy()
        new.loc[new.index[0], "price"] = "-5"
        res = validate_snapshot(new, self.prev)
        self.assertFalse(res["ok"])
        self.assertTrue(any("negative" in p for p in res["problems"]))

    def test_non_numeric_price(self):
        new = self.prev.copy()
        new.loc[new.index[:50], "price"] = "n/a"
        res = validate_snapshot(new, self.prev)
        self.assertFalse(res["ok"])
        self.assertTrue(any("non-numeric" in p for p in res["problems"]))

    def test_tracked_share_collapse(self):
        new = self.prev.copy()
        rng = np.random.default_rng(0)
        idx = rng.choice(new.index.to_numpy(), size=int(len(new) * 0.8), replace=False)
        new.loc[idx, "commodity"] = "Maize"
        res = validate_snapshot(new, self.prev)
        self.assertFalse(res["ok"])
        self.assertTrue(any("tracked-commodity share collapsed" in p for p in res["problems"]))

    def test_duplicates_are_warned_not_fatal(self):
        new = pd.concat([self.prev, self.prev.head(5)], ignore_index=True)
        res = validate_snapshot(new, self.prev)
        self.assertTrue(res["ok"], res["problems"])
        self.assertEqual(res["stats"]["duplicate_key_rows"], 5)
        self.assertTrue(any("duplicate" in w for w in res["warnings"]))


# ---------------------------------------------------------------- features / leakage
class LeakFreeFeatureTests(TempDirTest):
    def setUp(self):
        super().setUp()
        self.panel = F.monthly_panel(load(make_csv(self.p("d.csv"), months=36)))

    def _series(self, frame):
        return frame[(frame.commodity == "Tomatoes") & (frame.market == "Nairobi") & (frame.unit == "64 KG")]

    def test_changing_price_at_t_does_not_change_features_at_t(self):
        base = F.build_features(self.panel)
        s = self._series(base).sort_values("per")  # keeps original row labels
        t_row = s.iloc[14]
        t = t_row["per"]
        bumped = self.panel.copy()
        mask = (bumped.commodity == "Tomatoes") & (bumped.market == "Nairobi") & (bumped.unit == "64 KG") & (bumped.per == t)
        self.assertEqual(int(mask.sum()), 1)
        bumped.loc[mask, "price"] = bumped.loc[mask, "price"] * 10
        after = F.build_features(bumped)
        a = self._series(after)
        a_t = a[a.per == t].iloc[0]
        for c in ["lag_1", "lag_3", "lag_6", "ma_3", "ma_6", "vol_6"]:
            self.assertAlmostEqual(float(t_row[c]), float(a_t[c]), places=10, msg=f"feature {c} at t changed")
        # The ratio feature matrix for t must also be unchanged.
        enc = F.fit_encoders(self.panel)
        xa = F.ratio_features(base.loc[[t_row.name]], enc).to_numpy()
        xb = F.ratio_features(after.loc[[a_t.name]], enc).to_numpy()
        np.testing.assert_allclose(xa, xb)

    def test_changing_price_at_t_does_change_the_target_and_next_month_feature(self):
        base = F.build_features(self.panel)
        s = self._series(base).sort_values("per").reset_index(drop=True)
        t = s.iloc[14]["per"]
        bumped = self.panel.copy()
        mask = (bumped.commodity == "Tomatoes") & (bumped.market == "Nairobi") & (bumped.unit == "64 KG") & (bumped.per == t)
        bumped.loc[mask, "price"] = bumped.loc[mask, "price"] * 10
        after = self._series(F.build_features(bumped))
        nxt_before = self._series(base)[self._series(base).per == t + 1].iloc[0]["lag_1"]
        nxt_after = after[after.per == t + 1].iloc[0]["lag_1"]
        self.assertNotAlmostEqual(nxt_before, nxt_after)  # sanity: the perturbation is visible one month later

    def test_lag_1_is_exactly_previous_calendar_month(self):
        f = F.build_features(self.panel)
        s = self._series(f).set_index("per")
        prev_month = s.index[5] - 1
        self.assertAlmostEqual(s.loc[s.index[5], "lag_1"], self._series(self.panel).set_index("per").loc[prev_month, "price"])

    def test_potato_variants_merged_and_non_tracked_dropped(self):
        names = set(self.panel["commodity"].unique())
        self.assertIn("Potatoes (Irish)", names)
        self.assertNotIn("Potatoes (Irish, red)", names)
        self.assertNotIn("Maize", names)
        self.assertTrue(names <= set(F.TRACKED_COMMODITIES))


class CanonicalNameTests(unittest.TestCase):
    """Cross-check canonical names against price_api/app.py COMMODITY_META when the repo file is reachable."""

    def test_matches_app_py(self):
        path = os.environ.get("PRICE_API_APP")
        if not path or not os.path.exists(path):
            self.skipTest("set PRICE_API_APP to price_api/app.py to run this check")
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        values = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "COMMODITY_META":
                for v in node.value.values:
                    values.add(v.elts[0].value)
        self.assertEqual(values, set(F.TRACKED_COMMODITIES))


# ---------------------------------------------------------------- bootstrap
class BootstrapSignTests(unittest.TestCase):
    def _T(self, pred_naive, pred_rf, n_months=12, seed=0):
        rng = np.random.default_rng(seed)
        rows = []
        for m in range(n_months):
            for _ in range(20):
                y = 100 * float(np.exp(rng.normal(0, 0.1)))
                rows.append({"per_i": m, "price": y,
                             "pred_naive": y * pred_naive(rng), "pred_rf": y * pred_rf(rng)})
        return pd.DataFrame(rows)

    def test_better_challenger_has_negative_interval(self):
        T = self._T(lambda r: 1.2, lambda r: 1.0)
        bs = R.paired_bootstrap(T, n_boot=500)
        self.assertLess(bs["point_pp"], 0)
        self.assertLess(bs["ci95_hi_pp"], 0)  # challenger wins under the promotion rule

    def test_worse_challenger_has_positive_interval(self):
        T = self._T(lambda r: 1.0, lambda r: 1.2)
        bs = R.paired_bootstrap(T, n_boot=500)
        self.assertGreater(bs["point_pp"], 0)
        self.assertGreater(bs["ci95_lo_pp"], 0)

    def test_identical_models_interval_contains_zero(self):
        T = self._T(lambda r: 1.1, lambda r: 1.1)
        bs = R.paired_bootstrap(T, n_boot=500)
        self.assertEqual(bs["point_pp"], 0)
        self.assertLessEqual(bs["ci95_lo_pp"], 0)
        self.assertGreaterEqual(bs["ci95_hi_pp"], 0)

    def test_noisy_small_difference_does_not_promote(self):
        T = self._T(lambda r: 1.0 + 0.3 * r.normal(), lambda r: 1.0 + 0.3 * r.normal(), n_months=6)
        bs = R.paired_bootstrap(T, n_boot=500)
        self.assertGreaterEqual(bs["ci95_hi_pp"], 0)


# ---------------------------------------------------------------- champion contract
class ChampionContractTests(unittest.TestCase):
    def test_valid_naive_champion(self):
        c = R.make_champion("naive_last", None, "2026-09", 24,
                            {"n": 10, "mape": 18.0, "median_ape": 14.0, "naive_mape": 18.0},
                            {"_ALL": {"n": 10, "ratio_p10": 0.8, "ratio_p50": 1.0, "ratio_p90": 1.2}})
        self.assertEqual(R.check_champion_contract(c), [])
        self.assertEqual(c["schema_version"], 2)
        self.assertEqual(set(c), {"schema_version", "model_type", "model_file", "created_at", "data_through",
                                  "backtest", "error_bands", "error_bands_by_pricetype"})

    def test_validator_accepts_schema1_without_new_key(self):
        c = R.make_champion("naive_last", None, "2026-09", 24,
                            {"n": 10, "mape": 18.0, "median_ape": 14.0, "naive_mape": 18.0},
                            {"_ALL": {"n": 10, "ratio_p10": 0.8, "ratio_p50": 1.0, "ratio_p90": 1.2}})
        del c["error_bands_by_pricetype"]
        c["schema_version"] = 1
        self.assertEqual(R.check_champion_contract(c), [])

    def test_validator_accepts_and_checks_schema2_pricetype_key(self):
        c = R.make_champion("naive_last", None, "2026-09", 24,
                            {"n": 10, "mape": 18.0, "median_ape": 14.0, "naive_mape": 18.0},
                            {"_ALL": {"n": 10, "ratio_p10": 0.8, "ratio_p50": 1.0, "ratio_p90": 1.2}},
                            bands_by_pricetype={"Tomatoes": {"Wholesale": {"n": 40, "ratio_p10": 0.7,
                                                                           "ratio_p50": 1.0, "ratio_p90": 1.3}}})
        self.assertEqual(R.check_champion_contract(c), [])
        c["error_bands_by_pricetype"]["Tomatoes"]["Wholesale"].pop("n")
        self.assertTrue(any("Wholesale.n" in e for e in R.check_champion_contract(c)))
        c["error_bands_by_pricetype"] = "nope"
        self.assertTrue(any("error_bands_by_pricetype" in e for e in R.check_champion_contract(c)))

    def test_violations_detected(self):
        bad = {"schema_version": 3, "model_type": "naive_last", "model_file": "x.pkl",
               "created_at": "yesterday", "data_through": "2026/09", "backtest": {}, "error_bands": {}}
        errs = R.check_champion_contract(bad)
        self.assertTrue(any("schema_version" in e for e in errs))
        self.assertTrue(any("model_file" in e for e in errs))
        self.assertTrue(any("created_at" in e for e in errs))
        self.assertTrue(any("data_through" in e for e in errs))
        self.assertTrue(any("_ALL" in e for e in errs))


# ---------------------------------------------------------------- per-pricetype bands
class PricetypeBandsTests(unittest.TestCase):
    def frame(self):
        rng = np.random.default_rng(1)
        rows = []
        for c, pt, n in [("Tomatoes", "Wholesale", 40), ("Tomatoes", "Retail", 35),
                         ("Tomatoes", "Organic", 12), ("Kale", "Wholesale", 50)]:
            for _ in range(n):
                pred = 100.0
                rows.append({"commodity": c, "pricetype": pt, "pred_naive": pred,
                             "price": pred * float(rng.uniform(0.7, 1.4))})
        return pd.DataFrame(rows)

    def test_per_group_stats_match_manual_quantiles(self):
        T = self.frame()
        out = R.error_bands_by_pricetype(T, "pred_naive")
        g = T[(T.commodity == "Tomatoes") & (T.pricetype == "Wholesale")]
        r = (g["price"] / g["pred_naive"]).to_numpy()
        band = out["Tomatoes"]["Wholesale"]
        self.assertEqual(band["n"], 40)
        self.assertAlmostEqual(band["ratio_p10"], round(float(np.quantile(r, 0.10)), 4))
        self.assertAlmostEqual(band["ratio_p50"], round(float(np.quantile(r, 0.50)), 4))
        self.assertAlmostEqual(band["ratio_p90"], round(float(np.quantile(r, 0.90)), 4))

    def test_small_groups_skipped_and_no_fabricated_bands(self):
        out = R.error_bands_by_pricetype(self.frame(), "pred_naive")
        self.assertNotIn("Organic", out["Tomatoes"])          # n=12 < 30
        self.assertEqual(set(out["Tomatoes"]), {"Retail", "Wholesale"})
        self.assertEqual(set(out), {"Tomatoes", "Kale"})
        self.assertEqual(set(out["Kale"]), {"Wholesale"})

    def test_min_n_one_emits_every_present_group_only(self):
        T = self.frame()
        out = R.error_bands_by_pricetype(T, "pred_naive", min_n=1)
        self.assertEqual(set(out["Tomatoes"]), {"Retail", "Wholesale", "Organic"})
        self.assertEqual(set(out["Kale"]), {"Wholesale"})

    def test_per_commodity_bands_unchanged(self):
        T = self.frame()
        self.assertEqual(R.error_bands(T, "pred_naive")["Tomatoes"]["n"], 87)
        self.assertEqual(R.error_bands(T, "pred_naive")["_ALL"]["n"], 137)

    def test_generated_champion_validates_and_is_schema2(self):
        T = self.frame()
        bands = R.error_bands(T, "pred_naive")
        c = R.make_champion("naive_last", None, "2026-09", 24,
                            {"n": 137, "mape": 18.0, "median_ape": 14.0, "naive_mape": 18.0}, bands,
                            bands_by_pricetype=R.error_bands_by_pricetype(T, "pred_naive"))
        self.assertEqual(R.check_champion_contract(c), [])
        self.assertEqual(c["schema_version"], 2)
        self.assertEqual(c["error_bands"], bands)


# ---------------------------------------------------------------- download (no network)
class _FakeResp:
    def __init__(self, data):
        self._b = data

    def read(self, n=-1):
        if n is None or n < 0:
            out, self._b = self._b, b""
            return out
        out, self._b = self._b[:n], self._b[n:]
        return out

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class DownloadTests(TempDirTest):
    CKAN = json.dumps({"success": True, "result": {"resources": [
        {"name": "Kenya - Market Coordinates", "url": "https://example.invalid/coords.csv"},
        {"name": "Kenya - Food Prices", "url": "https://example.invalid/food.csv"}]}}).encode()

    def test_resolves_named_resource(self):
        url = R.resolve_csv_url(lambda req, timeout: _FakeResp(self.CKAN))
        self.assertEqual(url, "https://example.invalid/food.csv")

    def test_refuses_non_https(self):
        body = json.dumps({"success": True, "result": {"resources": [
            {"name": "Kenya - Food Prices", "url": "http://example.invalid/food.csv"}]}}).encode()
        with self.assertRaises(RuntimeError):
            R.resolve_csv_url(lambda req, timeout: _FakeResp(body))

    def test_retries_then_succeeds(self):
        calls = {"n": 0}
        payload = b"date,price\n2020-01-15,1\n"

        def opener(req, timeout):
            if "package_show" in req.full_url:
                calls["n"] += 1
                if calls["n"] == 1:
                    raise urllib.error.URLError("boom")
                return _FakeResp(self.CKAN)
            return _FakeResp(payload)

        sleeps = []
        path, size, url = R.download_csv(self.p("dl"), opener=opener, sleep=sleeps.append)
        self.assertEqual(size, len(payload))
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), payload)
        self.assertEqual(sleeps, [5])

    def test_size_cap(self):
        with self.assertRaises(R.DownloadTooLarge):
            R.stream_to_file("https://example.invalid/x", self.p("big.csv"),
                             opener=lambda req, timeout: _FakeResp(b"x" * 100), max_bytes=10)
        self.assertFalse(os.path.exists(self.p("big.csv")))


# ---------------------------------------------------------------- end-to-end exit codes
class ExitCodeTests(TempDirTest):
    """Runs retrain.py as a subprocess on small synthetic CSVs (window 6 months, 100 bootstrap resamples)."""

    def run_cli(self, *args):
        cmd = [sys.executable, "-I", RETRAIN, "--window-months", "6", "--retrain-every", "3",
               "--bootstrap-n", "100", *args]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=600)

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="retrain_exit_")
        cls.csv = make_csv(os.path.join(cls._tmp, "full.csv"), months=36, seed=3)
        cls.first = os.path.join(cls._tmp, "first")
        cls.r0 = subprocess.run([sys.executable, "-I", RETRAIN, "--csv", cls.csv, "--out", cls.first,
                                 "--window-months", "6", "--retrain-every", "3", "--bootstrap-n", "100"],
                                capture_output=True, text=True, timeout=600)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_first_run_exit_0_writes_contract_champion(self):
        self.assertEqual(self.r0.returncode, 0, self.r0.stdout + self.r0.stderr)
        with open(os.path.join(self.first, "champion.json"), encoding="utf-8") as fh:
            obj = json.load(fh)
        self.assertEqual(R.check_champion_contract(obj), [])
        self.assertEqual(obj["model_type"], "naive_last")
        self.assertIsNone(obj["model_file"])
        self.assertEqual(obj["data_through"], "2022-12")
        self.assertEqual(obj["backtest"]["window_months"], 6)
        self.assertIn("_ALL", obj["error_bands"])
        self.assertTrue(os.path.exists(os.path.join(self.first, "report.md")))
        self.assertTrue(os.path.exists(os.path.join(self.first, "metrics.json")))
        self.assertTrue(os.path.exists(os.path.join(self.first, "wfp_snapshot.csv.gz")))

    def test_same_snapshot_exit_1_writes_nothing_new(self):
        out = self.p("same")
        r = self.run_cli("--csv", self.csv, "--previous-snapshot", self.csv,
                         "--previous-champion", os.path.join(self.first, "champion.json"), "--out", out)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(out, "champion.json")))
        self.assertTrue(os.path.exists(os.path.join(out, "report.md")))

    def test_truncated_csv_exit_2_leaves_champion_alone(self):
        out = self.p("trunc")
        trunc = self.p("trunc.csv")
        with open(self.csv, encoding="utf-8") as src:
            lines = src.readlines()
        with open(trunc, "w", encoding="utf-8") as dst:
            dst.writelines(lines[: len(lines) // 2])
        r = self.run_cli("--csv", trunc, "--previous-snapshot", self.csv, "--previous-champion",
                         os.path.join(self.first, "champion.json"), "--out", out)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(out, "champion.json")))
        report = read_text(os.path.join(out, "report.md"))
        self.assertIn("row count shrank", report)

    def test_unexpected_error_exit_3_still_writes_report(self):
        out = self.p("err")
        r = self.run_cli("--csv", self.p("does_not_exist.csv"), "--out", out)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        report = read_text(os.path.join(out, "report.md"))
        self.assertIn("UNEXPECTED ERROR", report)
        self.assertFalse(os.path.exists(os.path.join(out, "champion.json")))

    def test_dry_run_writes_no_champion(self):
        out = self.p("dry")
        r = self.run_cli("--csv", self.csv, "--out", out, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(out, "champion.json")))
        self.assertTrue(os.path.exists(os.path.join(out, "report.md")))


class HumanReviewPathTests(TempDirTest):
    """Forces the bootstrap to say the RF challenger won, then checks the candidate is written
    for a human and the champion stays naive_last (RF is never auto-promoted)."""

    def test_rf_win_writes_candidate_not_champion_model(self):
        csv_path = make_csv(self.p("d.csv"), months=36, seed=5)
        out = self.p("win")
        fake = {"point_pp": -3.0, "ci95_lo_pp": -4.0, "ci95_hi_pp": -1.0, "n_boot": 1, "n_months": 1}
        original = R.paired_bootstrap
        R.paired_bootstrap = lambda T, **kw: fake
        try:
            code = R.main(["--csv", csv_path, "--out", out, "--window-months", "6", "--retrain-every", "3"])
        finally:
            R.paired_bootstrap = original
        self.assertEqual(code, 0)
        champion = json.loads(read_text(os.path.join(out, "champion.json")))
        self.assertEqual(champion["model_type"], "naive_last")
        self.assertIsNone(champion["model_file"])
        cand_dir = os.path.join(out, "candidate")
        cand = json.loads(read_text(os.path.join(cand_dir, "champion_candidate.json")))
        self.assertEqual(R.check_champion_contract(cand), [])
        self.assertEqual(cand["model_type"], "random_forest")
        self.assertEqual(cand["model_file"], "random_forest_price_model.pkl")
        self.assertTrue(os.path.exists(os.path.join(cand_dir, "random_forest_price_model.pkl")))
        report = read_text(os.path.join(out, "report.md"))
        self.assertIn("HUMAN REVIEW: challenger beat champion", report)


def raw_frame(rows):
    base = {"admin1": "Nairobi", "admin2": "Nairobi", "market": "Nairobi", "market_id": "1",
            "latitude": "-1.28", "longitude": "36.82", "category": "vegetables and fruits",
            "commodity": "Tomatoes", "commodity_id": "1", "unit": "64 KG", "priceflag": "actual",
            "pricetype": "Retail", "currency": "KES", "price": "100", "usdprice": "1"}
    return pd.DataFrame([{**base, **r} for r in rows], dtype=str)


class FlagAndPricetypeTests(unittest.TestCase):
    def test_actual_preferred_over_aggregate_in_same_month(self):
        raw = raw_frame([
            {"date": "2020-01-15", "price": "100", "priceflag": "actual"},
            {"date": "2020-01-20", "price": "110", "priceflag": "actual"},
            {"date": "2020-01-18", "price": "500", "priceflag": "aggregate"},  # dropped: actual exists
            {"date": "2020-02-15", "price": "200", "priceflag": "aggregate"},  # kept: no actual that month
            {"date": "2020-02-16", "price": "900", "priceflag": "actual,aggregate"},  # excluded flag
        ])
        stats = {}
        p = F.monthly_panel(raw, stats).sort_values("per").reset_index(drop=True)
        self.assertEqual(len(p), 2)
        self.assertAlmostEqual(p.loc[0, "price"], 105.0)   # median of the two actual rows
        self.assertAlmostEqual(p.loc[1, "price"], 200.0)   # aggregate-only month
        self.assertEqual(stats, {"rows_used_actual": 2, "rows_used_aggregate": 1,
                                 "aggregate_rows_dropped_actual_present": 1})

    def test_retail_and_wholesale_are_separate_series(self):
        raw = raw_frame([
            {"date": "2020-01-15", "price": "100", "pricetype": "Retail"},
            {"date": "2020-02-15", "price": "110", "pricetype": "Retail"},
            {"date": "2020-01-15", "price": "80", "pricetype": "Wholesale"},
            {"date": "2020-02-15", "price": "90", "pricetype": "Wholesale"},
        ])
        f = F.build_features(F.monthly_panel(raw))
        feb = f[f.per == pd.Period("2020-02", "M")].set_index("pricetype")
        self.assertAlmostEqual(feb.loc["Retail", "lag_1"], 100.0)
        self.assertAlmostEqual(feb.loc["Wholesale", "lag_1"], 80.0)  # never the Retail price
        self.assertEqual(len(F.KEY), 4)
        self.assertIn("pricetype", F.KEY)


if __name__ == "__main__":
    unittest.main()
