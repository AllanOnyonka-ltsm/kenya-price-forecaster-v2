"""Swarm agents.

Every agent is a pure function of a price history prefix: ``forecast(key, hist, target)`` receives
``hist`` = the series array truncated to the origin month (the last element is month t-h), so it
cannot read later months. ``fit`` receives only training prefixes. There is no wall clock, no
randomness and no I/O (except the optional narrative LLM call, which is off by default).

Agents return log-price deltas relative to the origin price, ``delta = log(pred) - log(hist[-1])``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

MAX_NUDGE = 0.05                      # narrative agent may move the aggregate by at most +/-5%
DIRECTION_SIGN = {"up": 1, "flat": 0, "down": -1}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Kenyan two-season harvest calendar (month numbers 1-12). Approximate ranges taken from
# research/IMPROVEMENT_RESEARCH.md item 6 ("roughly"); not verified against FEWS NET or KAMIS.
HARVEST_PHASES = {
    "long_harvest": (4, 5, 6, 7, 8),
    "short_harvest": (9, 10, 11, 12, 1),
    "lean": (2, 3),
}
PHASE_OF_MONTH = {m: name for name, months in HARVEST_PHASES.items() for m in months}
MIN_MONTH_OBS = 24                    # pooled observations needed before a month-of-year index is used
TREND_GRID = (0.0, 0.25, 0.5, 0.75, 1.0)


def finite_pos(v) -> bool:
    return v is not None and math.isfinite(float(v)) and float(v) > 0


@dataclass(frozen=True)
class AgentOutput:
    name: str
    delta: float                      # log change from origin price; NaN = agent has no view
    conf: float
    detail: dict = field(default_factory=dict)


def _nan_out(name, reason):
    return AgentOutput(name, float("nan"), 0.0, {"fallback": reason})


class TrendSeasonalAgent:
    """Seasonal naive (value 12 months before the target) plus a damped recent trend.

    pred_log = log y[t-12] + g * (log y[o] - log y[o-12]),  o = origin = t - h
    g is chosen from TREND_GRID on the training prefixes only.
    """

    name = "trend_seasonal"

    def __init__(self, trend_weight: float = 0.5):
        self.trend_weight = float(trend_weight)

    @staticmethod
    def _delta(hist, target, g):
        o = len(hist) - 1
        if not finite_pos(hist[o]):
            return _nan_out(TrendSeasonalAgent.name, "origin_not_observed")
        ly_o = math.log(hist[o])
        base = target - 12
        if base < 0 or base > o or not finite_pos(hist[base]):
            return AgentOutput(TrendSeasonalAgent.name, 0.0, 0.2, {"fallback": "no_seasonal_lag"})
        if o - 12 >= 0 and finite_pos(hist[o - 12]):
            trend = ly_o - math.log(hist[o - 12])
            conf = 1.0
        else:
            trend, conf = 0.0, 0.6
        pred_log = math.log(hist[base]) + g * trend
        return AgentOutput(TrendSeasonalAgent.name, pred_log - ly_o, conf,
                           {"seasonal_base_index": base, "trend": round(trend, 6), "g": g})

    def forecast(self, key, hist, target):
        return self._delta(hist, target, self.trend_weight)

    def fit(self, train, rows, horizon):
        """rows: (key, origin prefix, target index, actual price) for training targets."""
        best_g, best_err = self.trend_weight, float("inf")
        for g in TREND_GRID:
            errs = []
            for _key, prefix, t, actual in rows:
                out = self._delta(prefix, t, g)
                if math.isfinite(out.delta):
                    errs.append(abs(out.delta - (math.log(actual) - math.log(prefix[-1]))))
            if errs:
                err = float(np.mean(errs))
                if err < best_err:
                    best_err, best_g = err, g
        self.trend_weight = best_g


class SupplySeasonAgent:
    """Kenyan harvest-calendar seasonal adjustment from training-only month-of-year indices.

    For each (commodity, pricetype): the deviation of log price from its centred 13-month mean,
    averaged by calendar month over the training prefixes (pooled across markets). Months with too
    few pooled observations fall back to their harvest-phase average (long harvest / short harvest /
    lean), which is where the calendar enters. Forecast = expected seasonal move from origin month
    to target month.
    """

    name = "supply_season"

    def __init__(self):
        self.table = {}

    def fit(self, train, rows, horizon):
        groups = {}
        for key, arr in train.items():
            ly = np.full(len(arr), np.nan)
            pos = arr > 0
            ly[pos] = np.log(arr[pos])
            centred = pd.Series(ly).rolling(13, center=True, min_periods=7).mean().to_numpy()
            dev = ly - centred
            moy = np.arange(len(arr)) % 12
            groups.setdefault((key[0], key[3]), []).append((moy, dev))
        self.table = {}
        for g, parts in groups.items():
            moy = np.concatenate([p[0] for p in parts])
            dev = np.concatenate([p[1] for p in parts])
            ok = np.isfinite(dev)
            month_mean = np.full(12, np.nan)
            month_cnt = np.zeros(12, dtype=int)
            for m in range(12):
                sel = ok & (moy == m)
                month_cnt[m] = int(sel.sum())
                if sel.any():
                    month_mean[m] = float(dev[sel].mean())
            phase_val = {}
            for name, months in HARVEST_PHASES.items():
                vals = [month_mean[m - 1] for m in months if np.isfinite(month_mean[m - 1])]
                phase_val[name] = float(np.mean(vals)) if vals else float("nan")
            table = month_mean.copy()
            for m in range(12):
                if month_cnt[m] < MIN_MONTH_OBS or not np.isfinite(table[m]):
                    table[m] = phase_val[PHASE_OF_MONTH[m + 1]]
            self.table[g] = table

    def forecast(self, key, hist, target):
        o = len(hist) - 1
        table = self.table.get((key[0], key[3]))
        if table is None or not finite_pos(hist[o]):
            return _nan_out(self.name, "no_calendar_index")
        s_t = table[target % 12]
        s_o = table[o % 12]
        if not (np.isfinite(s_t) and np.isfinite(s_o)):
            return _nan_out(self.name, "no_calendar_index")
        return AgentOutput(self.name, float(s_t - s_o), 0.5,
                           {"phase": PHASE_OF_MONTH[target % 12 + 1]})


def _band(r) -> dict:
    return {"n": int(len(r)), "ratio_p10": float(np.quantile(r, 0.10)),
            "ratio_p50": float(np.quantile(r, 0.50)), "ratio_p90": float(np.quantile(r, 0.90))}


class RiskBandAgent:
    """Interval width from past residuals: quantiles of actual / point-forecast ratios.

    Pooled per (commodity, pricetype) when at least min_n residuals exist, else per commodity,
    else over everything. Same ratio convention as pipeline/retrain.py error_bands.
    """

    name = "risk_band"

    def __init__(self, min_n: int = 30):
        self.min_n = int(min_n)
        self.by_pair, self.by_commodity, self.all_band = {}, {}, None

    def fit(self, pool):
        """pool: list of (commodity, pricetype, ratio) from training-period residuals only."""
        self.by_pair, self.by_commodity, self.all_band = {}, {}, None
        if not pool:
            return
        com = np.array([p[0] for p in pool], dtype=object)
        pt = np.array([p[1] for p in pool], dtype=object)
        r = np.array([p[2] for p in pool], dtype=float)
        self.all_band = _band(r)
        for c in sorted(set(com)):
            mc = com == c
            self.by_commodity[c] = _band(r[mc])
            for p in sorted(set(pt[mc])):
                mp = mc & (pt == p)
                if mp.sum() >= self.min_n:
                    self.by_pair[(c, p)] = _band(r[mp])

    def quantiles(self, commodity, pricetype):
        band = self.by_pair.get((commodity, pricetype)) or self.by_commodity.get(commodity) or self.all_band
        if band is None:
            return None
        return band["ratio_p10"], band["ratio_p90"]


# ----------------------------------------------------------------- narrative (LLM, optional)
NARRATIVE_SCHEMA = {
    "type": "object",
    "required": ["direction", "confidence", "reasons"],
    "properties": {
        "direction": {"type": "string", "enum": ["up", "flat", "down"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reasons": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
    },
}


def bounded_nudge(direction: str, confidence: float) -> float:
    """Fractional move of the aggregate, always within [-MAX_NUDGE, +MAX_NUDGE]."""
    if direction not in DIRECTION_SIGN:
        raise ValueError(f"unknown direction {direction!r}")
    c = float(confidence)
    if not math.isfinite(c):
        c = 0.0
    c = min(1.0, max(0.0, c))
    return MAX_NUDGE * DIRECTION_SIGN[direction] * c


def build_narrative_prompt(key, hist, target) -> str:
    """Prompt built only from prices in ``hist`` (the as-of prefix); no dates after the origin."""
    last = [float(x) for x in hist[-12:] if math.isfinite(x)]
    o = len(hist) - 1
    return (
        f"Series: {key[0]} ({key[3]}) in {key[1]}, unit {key[2]}, KES.\n"
        f"Last observed monthly prices (oldest to newest, up to the forecast origin): {last}\n"
        f"Forecast target: the price {target - o} month(s) after the last observed month, "
        f"calendar month {MONTHS[target % 12]}.\n"
        "Judge only the direction of the price move relative to the last observed price. "
        "Do not use any information not shown here."
    )


class NarrativeAgent:
    """Optional LLM agent. Returns a bounded multiplicative nudge, never a price."""

    name = "narrative"

    def __init__(self, provider):
        self.provider = provider

    def nudge(self, key, hist, target):
        out = self.provider.complete_json(build_narrative_prompt(key, hist, target), NARRATIVE_SCHEMA)
        n = bounded_nudge(out["direction"], out["confidence"])
        detail = {"direction": out["direction"], "confidence": float(out["confidence"]),
                  "reasons": list(out["reasons"]), "nudge_pct": round(100 * n, 4),
                  "provider": getattr(self.provider, "name", "?"),
                  "fallback_reason": getattr(self.provider, "last_fallback_reason", None)}
        return 1.0 + n, detail


# ----------------------------------------------------------------- aggregation
def weighted_median(values, weights):
    """Weighted median (lower/upper midpoint on an exact tie). NaN if nothing usable."""
    v = np.asarray(values, dtype=float)
    w = np.asarray(weights, dtype=float)
    ok = np.isfinite(v) & np.isfinite(w) & (w > 0)
    if not ok.any():
        return float("nan")
    v, w = v[ok], w[ok]
    order = np.argsort(v, kind="mergesort")
    v, w = v[order], w[order]
    cw = np.cumsum(w)
    half = 0.5 * cw[-1]
    i = int(np.searchsorted(cw, half, side="left"))
    if np.isclose(cw[i], half) and i + 1 < len(v):
        return float(0.5 * (v[i] + v[i + 1]))
    return float(v[i])
