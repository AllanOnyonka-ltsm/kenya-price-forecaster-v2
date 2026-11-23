"""The swarm: agents + inverse-error weights + weighted median + risk bands + optional narrative nudge.

Usage (walk-forward block): ``Swarm(...).fit(train, horizon, cut)`` where ``train`` maps series key
to the price array (fit() truncates it at dense index ``cut``, the last month whose price is known),
then ``forecast(key, series, target)`` for each target whose origin ``target - horizon`` is <= cut.
"""
from __future__ import annotations

import math

import numpy as np

from .agents import (NarrativeAgent, RiskBandAgent, SupplySeasonAgent, TrendSeasonalAgent,
                     finite_pos, weighted_median)


def _training_rows(train, horizon, cut, window):
    """(key, origin prefix, target, actual) for every observed target in (cut - window, cut]."""
    rows = []
    lo = max(horizon, cut - window + 1)
    for key, arr in train.items():
        for t in range(lo, cut + 1):
            if finite_pos(arr[t]) and finite_pos(arr[t - horizon]):
                rows.append((key, arr[: t - horizon + 1], t, float(arr[t])))
    return rows


def _agent_weights(agents, rows):
    """Weight = 1 / MAE of each agent's log delta on training rows; normalised to sum 1."""
    raw = {}
    for agent in agents:
        errs = []
        for key, prefix, t, actual in rows:
            out = agent.forecast(key, prefix, t)
            if math.isfinite(out.delta):
                errs.append(abs(out.delta - (math.log(actual) - math.log(prefix[-1]))))
        mae = float(np.mean(errs)) if errs else float("nan")
        raw[agent.name] = 1.0 / max(mae, 1e-9) if math.isfinite(mae) else 0.0
    total = sum(raw.values())
    if total <= 0:
        return {a.name: 1.0 / len(agents) for a in agents}
    return {k: v / total for k, v in raw.items()}


class Swarm:
    def __init__(self, agents=None, narrative=None, fit_window=60, residual_window=60, min_band_n=30):
        self.agents = list(agents) if agents is not None else [TrendSeasonalAgent(), SupplySeasonAgent()]
        self.narrative = narrative                  # NarrativeAgent or None (off by default)
        self.fit_window = int(fit_window)
        self.residual_window = int(residual_window)
        self.band = RiskBandAgent(min_n=min_band_n)
        self.weights = {}
        self.horizon = None
        self.cut = None

    def fit(self, train, horizon, cut):
        horizon, cut = int(horizon), int(cut)
        if horizon < 1 or horizon > 12:
            raise ValueError("horizon must be between 1 and 12")
        self.horizon, self.cut = horizon, cut
        train = {k: np.asarray(v[: cut + 1], dtype=float) for k, v in train.items()}
        widest = max(self.fit_window, self.residual_window)
        rows = _training_rows(train, horizon, cut, widest)
        fit_rows = [r for r in rows if r[2] > cut - self.fit_window]
        for agent in self.agents:
            agent.fit(train, fit_rows, horizon)
        self.weights = _agent_weights(self.agents, fit_rows)
        pool = []
        for key, prefix, t, actual in rows:
            if t <= cut - self.residual_window:
                continue
            price, _, _, _ = self._point(key, prefix, t)
            if finite_pos(price):
                pool.append((key[0], key[3], actual / price))
        self.band.fit(pool)
        return self

    def _point(self, key, hist, target):
        outs = [a.forecast(key, hist, target) for a in self.agents]
        deltas = [o.delta for o in outs]
        ws = [self.weights.get(o.name, 0.0) for o in outs]
        d = weighted_median(deltas, ws)
        fallback = not math.isfinite(d)
        if fallback:
            d = 0.0                                  # no agent had a view: naive last value
        return float(hist[-1]) * math.exp(d), outs, d, fallback

    def forecast(self, key, series, target):
        """Forecast for dense index ``target``. ``series`` may be the full array: only months up to
        the origin (target - horizon) are passed to the agents, so later months cannot influence it."""
        if self.horizon is None:
            raise RuntimeError("call fit() first")
        origin = int(target) - self.horizon
        if origin < 0 or origin >= len(series):
            raise ValueError("origin is outside the series")
        hist = np.asarray(series[: origin + 1], dtype=float)
        if not finite_pos(hist[origin]):
            raise ValueError("origin price is not observed")
        price, outs, d, fallback = self._point(key, hist, target)
        mult, narrative_detail = 1.0, None
        if self.narrative is not None:
            mult, narrative_detail = self.narrative.nudge(key, hist, target)
        point = price * mult
        q = self.band.quantiles(key[0], key[3])
        lower = point * q[0] if q else float("nan")
        upper = point * q[1] if q else float("nan")
        return {
            "point": point,
            "lower": lower,
            "upper": upper,
            "log_delta": d,
            "fallback": fallback,
            "agent_deltas": {o.name: o.delta for o in outs},
            "narrative": narrative_detail,
        }
