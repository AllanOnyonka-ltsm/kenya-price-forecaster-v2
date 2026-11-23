"""Offline unit tests for crop_swarm. No network, no real CLI calls, no writes outside temp dirs."""
from __future__ import annotations

import copy
import json
import math
import os
import subprocess
import sys
import unittest
from unittest import mock

import numpy as np

SWARM_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SWARM_DIR not in sys.path:
    sys.path.insert(0, SWARM_DIR)

from crop_swarm import llm_provider as lp  # noqa: E402
from crop_swarm.agents import (MAX_NUDGE, NARRATIVE_SCHEMA, NarrativeAgent, SupplySeasonAgent,  # noqa: E402
                               TrendSeasonalAgent, bounded_nudge, weighted_median)
from crop_swarm.swarm import Swarm  # noqa: E402

KEY = ("Kale", "Nairobi", "KG", "Wholesale")
KEY2 = ("Tomatoes", "Mombasa", "KG", "Retail")


def synthetic_series(n=72, seed=0):
    """Dense monthly price arrays (grid start multiple of 12), positive, with seasonality and gaps."""
    rng = np.random.default_rng(seed)
    out = {}
    for k, phase in ((KEY, 0.0), (KEY2, 1.3)):
        i = np.arange(n)
        base = 50 + 0.2 * i + 8 * np.sin(2 * np.pi * (i + phase) / 12)
        noise = rng.normal(0, 2.0, n)
        arr = np.maximum(base + noise, 5.0)
        arr[rng.random(n) < 0.05] = np.nan          # a few unobserved months
        out[k] = arr
    return out


class FakeProvider:
    name = "fake"

    def __init__(self, reply):
        self.reply = reply
        self.last_fallback_reason = None
        self.calls = 0

    def complete_json(self, prompt, schema):
        self.calls += 1
        return dict(self.reply)


def fitted_swarm(series, horizon=1, cut=59, narrative=None):
    return Swarm(narrative=narrative, fit_window=40, residual_window=40).fit(series, horizon, cut)


class NoLookAheadTest(unittest.TestCase):
    def test_perturbing_target_and_later_months_does_not_change_forecast(self):
        series = synthetic_series()
        cut, target = 59, 60
        swarm = fitted_swarm(series, horizon=1, cut=cut)
        base = {k: swarm.forecast(k, v, target) for k, v in series.items()}
        rng = np.random.default_rng(7)
        for k, arr in series.items():
            pert = {kk: vv.copy() for kk, vv in series.items()}
            pert[k][target:] = rng.uniform(1e4, 2e4, len(pert[k][target:]))   # wildly different future
            again = swarm.forecast(k, pert[k], target)
            self.assertEqual(again, base[k], f"forecast for month t changed when months >= t changed ({k})")

    def test_perturbing_origin_does_change_forecast(self):
        # sanity check that the test above is not trivially constant
        series = synthetic_series()
        swarm = fitted_swarm(series, horizon=1, cut=59)
        k = KEY
        before = swarm.forecast(k, series[k], 60)["point"]
        pert = series[k].copy()
        pert[59] = pert[59] * 3
        after = swarm.forecast(k, pert, 60)["point"]
        self.assertNotAlmostEqual(before, after)

    def test_fit_ignores_months_after_the_cut(self):
        series = synthetic_series()
        cut = 59
        a = fitted_swarm(series, horizon=3, cut=cut)
        pert = {k: v.copy() for k, v in series.items()}
        for k in pert:
            pert[k][cut + 1:] = 9e5                 # future prices, handed to fit() on purpose
        b = fitted_swarm(pert, horizon=3, cut=cut)
        self.assertEqual(a.weights, b.weights)
        self.assertEqual(a.band.by_pair, b.band.by_pair)
        self.assertEqual(a.agents[0].trend_weight, b.agents[0].trend_weight)
        for k in series:
            self.assertEqual(a.forecast(k, series[k], 62), b.forecast(k, pert[k], 62))

    def test_horizon_mismatch_and_bad_origin_are_refused(self):
        series = synthetic_series()
        swarm = fitted_swarm(series, horizon=1, cut=59)
        with self.assertRaises(RuntimeError):
            Swarm().forecast(KEY, series[KEY], 60)
        with self.assertRaises(ValueError):
            swarm.forecast(KEY, series[KEY], 0)     # origin would be -1
        missing = series[KEY].copy()
        missing[59] = np.nan
        with self.assertRaises(ValueError):
            swarm.forecast(KEY, missing, 60)        # origin month not observed


class DeterminismTest(unittest.TestCase):
    def test_same_inputs_same_outputs(self):
        series = synthetic_series()
        first = fitted_swarm(series, horizon=1, cut=59)
        second = fitted_swarm(copy.deepcopy(series), horizon=1, cut=59)
        for k in series:
            self.assertEqual(first.forecast(k, series[k], 60), second.forecast(k, series[k], 60))
        self.assertEqual(first.weights, second.weights)

    def test_offline_provider_is_deterministic(self):
        p = lp.get_provider("offline")
        a = p.complete_json("x", NARRATIVE_SCHEMA)
        b = lp.get_provider("offline").complete_json("y", NARRATIVE_SCHEMA)
        self.assertEqual(a, b)
        self.assertEqual(a, {"direction": "flat", "confidence": 0, "reasons": []})

    def test_agents_are_pure(self):
        series = synthetic_series()
        hist = series[KEY][:60]
        t = TrendSeasonalAgent()
        self.assertEqual(t.forecast(KEY, hist, 61), t.forecast(KEY, hist.copy(), 61))
        s = SupplySeasonAgent()
        s.fit({KEY: series[KEY][:60]}, [], 1)
        self.assertEqual(s.forecast(KEY, hist, 61), s.forecast(KEY, hist.copy(), 61))


class ProviderFallbackTest(unittest.TestCase):
    def _env(self, **extra):
        env = {"CROP_SWARM_LLM": "claude_cli", "CROP_SWARM_ALLOW_CLAUDE_CLI": "1"}
        env.update(extra)
        return mock.patch.dict(os.environ, env, clear=False)

    def test_falls_back_to_offline_when_cli_missing(self):
        with self._env(CROP_SWARM_CLAUDE_BIN=os.path.join(SWARM_DIR, "no_such_claude_binary.exe")):
            p = lp.get_provider()
            out = p.complete_json("p", NARRATIVE_SCHEMA)
        self.assertEqual(out, {"direction": "flat", "confidence": 0, "reasons": []})
        self.assertIn("claude CLI not found", p.last_fallback_reason)

    def test_falls_back_when_claude_not_on_path(self):
        env = {"CROP_SWARM_LLM": "claude_cli", "CROP_SWARM_ALLOW_CLAUDE_CLI": "1"}
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CROP_SWARM_CLAUDE_BIN", None)
            with mock.patch.object(lp.shutil, "which", return_value=None):
                p = lp.get_provider()
                out = p.complete_json("p", NARRATIVE_SCHEMA)
        self.assertEqual(out["direction"], "flat")
        self.assertIn("claude CLI not found", p.last_fallback_reason)

    def test_refuses_cli_without_explicit_opt_in(self):
        env = {"CROP_SWARM_LLM": "claude_cli", "CROP_SWARM_CLAUDE_BIN": sys.executable}
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("CROP_SWARM_ALLOW_CLAUDE_CLI", None)
            with mock.patch.object(lp.subprocess, "run") as run:
                p = lp.get_provider()
                out = p.complete_json("p", NARRATIVE_SCHEMA)
        run.assert_not_called()
        self.assertEqual(out["direction"], "flat")
        self.assertIn("CROP_SWARM_ALLOW_CLAUDE_CLI", p.last_fallback_reason)

    def test_unknown_backend_falls_back_to_offline(self):
        p = lp.get_provider("sometimes_gpt")
        self.assertEqual(p.complete_json("p", NARRATIVE_SCHEMA)["direction"], "flat")
        self.assertIn("unknown CROP_SWARM_LLM", p.last_fallback_reason)

    def test_anthropic_api_without_key_falls_back(self):
        with mock.patch.dict(os.environ, {"CROP_SWARM_LLM": "anthropic_api"}, clear=False):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            p = lp.get_provider()
            out = p.complete_json("p", NARRATIVE_SCHEMA)
        self.assertEqual(out["direction"], "flat")
        self.assertIn("ANTHROPIC_API_KEY is not set", p.last_fallback_reason)


class ClaudeCLITest(unittest.TestCase):
    ENVELOPE = json.dumps({"type": "result", "subtype": "success", "is_error": False,
                           "result": json.dumps({"direction": "up", "confidence": 0.5, "reasons": ["test"]})})

    def _run_with(self, fake_run):
        env = {"CROP_SWARM_LLM": "claude_cli", "CROP_SWARM_ALLOW_CLAUDE_CLI": "1",
               "CROP_SWARM_CLAUDE_BIN": sys.executable, "ANTHROPIC_API_KEY": "sk-test-DUMMY-NOT-REAL"}
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(lp.subprocess, "run", side_effect=fake_run) as run:
            p = lp.get_provider()
            out = p.complete_json("p", NARRATIVE_SCHEMA)
        return p, out, run

    def test_strips_anthropic_api_key_and_never_passes_bare(self):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = list(cmd)
            seen["env"] = dict(kwargs["env"])
            return subprocess.CompletedProcess(cmd, 0, stdout=self.ENVELOPE, stderr="")

        p, out, run = self._run_with(fake_run)
        self.assertNotIn("ANTHROPIC_API_KEY", seen["env"])
        self.assertNotIn("--bare", seen["cmd"])
        self.assertEqual(seen["cmd"][1], "-p")
        self.assertIn("--output-format", seen["cmd"])
        self.assertEqual(seen["cmd"][seen["cmd"].index("--output-format") + 1], "json")
        self.assertEqual(out["direction"], "up")
        self.assertIsNone(p.last_fallback_reason)
        self.assertEqual(run.call_count, 1)

    def test_usage_limit_falls_back_to_offline(self):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Error: Usage limit reached for this window")

        p, out, _ = self._run_with(fake_run)
        self.assertEqual(out["direction"], "flat")
        self.assertIn("LimitError", p.last_fallback_reason)

    def test_timeout_falls_back_to_offline(self):
        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 1))

        p, out, _ = self._run_with(fake_run)
        self.assertEqual(out["direction"], "flat")
        self.assertIn("TimeoutExpired", p.last_fallback_reason)

    def test_invalid_reply_is_retried_once_then_accepted(self):
        replies = [
            subprocess.CompletedProcess([], 0, stdout=json.dumps({"result": "not json at all"}), stderr=""),
            subprocess.CompletedProcess([], 0, stdout=self.ENVELOPE, stderr=""),
        ]
        p, out, run = self._run_with(lambda cmd, **kw: replies.pop(0))
        self.assertEqual(run.call_count, 2)
        self.assertEqual(out["direction"], "up")

    def test_two_invalid_replies_fall_back(self):
        bad = json.dumps({"result": json.dumps({"direction": "sideways", "confidence": 2, "reasons": []})})
        p, out, run = self._run_with(lambda cmd, **kw: subprocess.CompletedProcess([], 0, stdout=bad, stderr=""))
        self.assertEqual(run.call_count, 2)
        self.assertEqual(out["direction"], "flat")
        self.assertIn("failed schema", p.last_fallback_reason)


class BoundedNudgeTest(unittest.TestCase):
    def test_nudge_never_exceeds_five_percent(self):
        for d in ("up", "flat", "down"):
            for c in (0, 0.3, 1, 5, -4, float("nan")):
                self.assertLessEqual(abs(bounded_nudge(d, c)), MAX_NUDGE + 1e-12)
        self.assertAlmostEqual(bounded_nudge("up", 1.0), 0.05)
        self.assertAlmostEqual(bounded_nudge("down", 1.0), -0.05)
        self.assertEqual(bounded_nudge("flat", 1.0), 0.0)

    def test_unknown_direction_rejected(self):
        with self.assertRaises(ValueError):
            bounded_nudge("sideways", 0.5)

    def test_swarm_point_moves_by_at_most_five_percent(self):
        series = synthetic_series()
        k, t = KEY, 60
        plain = fitted_swarm(series, horizon=1, cut=59).forecast(k, series[k], t)["point"]
        for direction, conf in (("up", 1.0), ("down", 1.0), ("up", 0.5), ("flat", 1.0), ("down", 0.0)):
            provider = FakeProvider({"direction": direction, "confidence": conf, "reasons": []})
            sw = fitted_swarm(series, horizon=1, cut=59, narrative=NarrativeAgent(provider))
            nudged = sw.forecast(k, series[k], t)
            ratio = nudged["point"] / plain
            self.assertLessEqual(abs(ratio - 1.0), MAX_NUDGE + 1e-12)
            self.assertAlmostEqual(ratio, 1.0 + bounded_nudge(direction, conf), places=12)
            self.assertEqual(nudged["narrative"]["direction"], direction)

    def test_schema_rejects_out_of_range_confidence(self):
        errs = lp.validate_json({"direction": "up", "confidence": 1.5, "reasons": []}, NARRATIVE_SCHEMA)
        self.assertTrue(any("maximum" in e for e in errs))
        errs = lp.validate_json({"direction": "sideways", "confidence": 0.2, "reasons": []}, NARRATIVE_SCHEMA)
        self.assertTrue(any("enum" in e or "not in" in e for e in errs))
        errs = lp.validate_json({"direction": "up", "confidence": 0.2}, NARRATIVE_SCHEMA)
        self.assertTrue(any("reasons" in e for e in errs))


class AggregatorTest(unittest.TestCase):
    def test_weighted_median_basic(self):
        self.assertEqual(weighted_median([1.0, 2.0, 3.0], [1, 1, 1]), 2.0)
        self.assertEqual(weighted_median([1.0, 2.0, 3.0], [1, 1, 10]), 3.0)
        self.assertAlmostEqual(weighted_median([0.0, 10.0], [1, 1]), 5.0)

    def test_weighted_median_skips_nan_and_zero_weight(self):
        self.assertEqual(weighted_median([float("nan"), 4.0, 7.0], [1, 1, 0]), 4.0)
        self.assertTrue(math.isnan(weighted_median([float("nan")], [1])))


class RealSliceBacktestTest(unittest.TestCase):
    """Runs the real backtest code on a small real slice of data/wfp_snapshot.csv.gz."""

    @classmethod
    def setUpClass(cls):
        import backtest  # noqa: WPS433 (import inside test: needs SWARM_DIR on sys.path)
        from crop_swarm.series import load_panel
        panel = load_panel()
        cls.slice = panel[panel["commodity"].isin(["Kale", "Tomatoes"])].copy()
        cls.backtest = backtest

    def test_backtest_runs_on_real_slice_and_matches_retrain_rows(self):
        out = self.backtest.run_backtest(self.slice, window=12, retrain_every=6, horizons=(1, 3),
                                         n_boot=50)
        parity = out["parity_with_retrain_h1"]
        self.assertTrue(parity["identical"], parity)
        r1 = out["results"]["1"]
        self.assertGreater(r1["n"], 0)
        for method in ("naive_last", "seasonal_naive", "drift", "swarm"):
            self.assertIn(method, r1["methods"])
            self.assertEqual(r1["methods"][method]["n"], r1["n"])
        self.assertIn(out["verdict"]["decision"], ("PROMOTE", "KEEP-CHAMPION"))
        self.assertIn("3", out["results"])

    def test_backtest_is_deterministic(self):
        a = self.backtest.run_backtest(self.slice, window=12, retrain_every=6, horizons=(1,), n_boot=50)
        b = self.backtest.run_backtest(self.slice, window=12, retrain_every=6, horizons=(1,), n_boot=50)
        self.assertEqual(json.dumps(a["results"], sort_keys=True), json.dumps(b["results"], sort_keys=True))


if __name__ == "__main__":
    unittest.main()
