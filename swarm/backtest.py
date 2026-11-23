#!/usr/bin/env python3
"""Walk-forward backtest: swarm (LLM off) as a CHALLENGER against the naive_last champion.

Same protocol as pipeline/retrain.py: last `--window-months` months (default 60), block retrain every
`--retrain-every` months (default 3), training rows strictly before the first month of each block,
series key (commodity, market, unit, pricetype), monthly median prices, observed months only, and the
same metrics (summarize, paired month-clustered bootstrap, promotion rule). The h=1 rows are checked
against retrain.walk_forward row by row.

Extra horizons (3 and 6 months) use the same blocks: the forecast for month t is made from the
prices of month t-h, and the training cut moves back by h-1 so nothing after the origin is used.

Writes results/backtest_<date>.json and results/backtest_<date>.md and prints the verdict.
The champion (champion.json) is read for a reproduction check only and is never written.

Run from monorepo/swarm:  python backtest.py
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from crop_swarm.series import DEFAULT_SNAPSHOT, PIPELINE_DIR, load_panel, to_dense  # noqa: E402
from crop_swarm.swarm import Swarm  # noqa: E402

import features as pf  # noqa: E402  (monorepo/pipeline/features.py)
import retrain  # noqa: E402  (monorepo/pipeline/retrain.py, imported only; not modified)

CHAMPION_JSON = os.path.join(os.path.dirname(PIPELINE_DIR), "price_api", "artifacts", "champion.json")
RESULTS_DIR = os.path.join(HERE, "results")
METHODS = {
    "naive_last": "pred_naive",
    "seasonal_naive": "pred_seasonal_naive",
    "drift": "pred_drift",
    "swarm": "pred_swarm",
}
CHALLENGERS = ("seasonal_naive", "drift", "swarm")
DEFAULT_HORIZONS = (1, 3, 6)


def _ok(v):
    return v is not None and math.isfinite(float(v)) and float(v) > 0


def _scales(known):
    """In-sample MASE scales from known prices only: mean |diff| at lag 1 and lag 12."""
    out = []
    for lag in (1, 12):
        if len(known) <= lag:
            out.append(float("nan"))
            continue
        d = np.abs(known[lag:] - known[:-lag])
        d = d[np.isfinite(d)]
        out.append(float(d.mean()) if len(d) else float("nan"))
    return out


def _walk_rows(dense, blocks, horizon, swarm_kwargs):
    rows = []
    for b_idx, block in enumerate(blocks):
        cut_abs = block[0] - horizon                 # last month whose price is known at block start
        cut = cut_abs - dense.grid_start
        if cut < 0:
            continue
        train = {k: arr[: cut + 1] for k, arr in dense.series.items()}
        swarm = Swarm(**swarm_kwargs).fit(train, horizon, cut)
        scales = {k: _scales(v) for k, v in train.items()}
        for key, arr in dense.series.items():
            for t_abs in block:
                t = t_abs - dense.grid_start
                if t >= len(arr) or not _ok(arr[t]):
                    continue
                o = t - horizon
                if o < 0 or not _ok(arr[o]):
                    continue
                naive = float(arr[o])
                base = t - 12
                if 0 <= base <= o and _ok(arr[base]):
                    snaive, sn_fb = float(arr[base]), False
                else:
                    snaive, sn_fb = naive, True
                lag = o - 12
                if lag >= 0 and _ok(arr[lag]):
                    drift = naive * math.exp(horizon * (math.log(arr[o]) - math.log(arr[lag])) / 12.0)
                    dr_fb = False
                else:
                    drift, dr_fb = naive, True
                out = swarm.forecast(key, arr, t)
                s1, s12 = scales.get(key, (float("nan"), float("nan")))
                rows.append({
                    "commodity": key[0], "market": key[1], "unit": key[2], "pricetype": key[3],
                    "horizon": horizon, "block": b_idx, "per_i": int(t_abs), "price": float(arr[t]),
                    "origin_price": naive, "pred_naive": naive, "pred_seasonal_naive": snaive,
                    "pred_drift": drift, "pred_swarm": out["point"],
                    "lower": out["lower"], "upper": out["upper"],
                    "seasonal_fallback": sn_fb, "drift_fallback": dr_fb,
                    "swarm_fallback": out["fallback"], "scale_lag1": s1, "scale_lag12": s12,
                })
    return pd.DataFrame(rows)


def _mase(df, col, scale_col):
    m = df[scale_col].notna() & (df[scale_col] > 0)
    if not m.any():
        return {"value": None, "n": 0}
    err = (df.loc[m, col] - df.loc[m, "price"]).abs() / df.loc[m, scale_col]
    return {"value": round(float(err.mean()), 4), "n": int(m.sum())}


def _metrics(df, n_boot):
    methods = {}
    for name, col in METHODS.items():
        s = retrain.summarize(df[col], df["price"])
        methods[name] = {
            **s,
            "mase_vs_naive_scale": _mase(df, col, "scale_lag1"),
            "mase_vs_seasonal_naive_scale": _mase(df, col, "scale_lag12"),
        }
    methods["seasonal_naive"]["fallback_rows"] = int(df["seasonal_fallback"].sum())
    methods["drift"]["fallback_rows"] = int(df["drift_fallback"].sum())
    methods["swarm"]["fallback_rows"] = int(df["swarm_fallback"].sum())
    boot = {}
    for name in CHALLENGERS:
        boot[name] = retrain.paired_bootstrap(df, challenger=METHODS[name], champion="pred_naive",
                                              n_boot=n_boot, seed=0)
    within = (df["price"] >= df["lower"]) & (df["price"] <= df["upper"])
    band = {
        "swarm_80pct_coverage": round(float(within.mean() * 100), 2),
        "swarm_median_upper_over_lower": round(float((df["upper"] / df["lower"]).median()), 4),
        "swarm_coverage_by_commodity": {
            c: round(float(within[df["commodity"] == c].mean() * 100), 2) for c in sorted(df["commodity"].unique())
        },
    }
    return {"n": int(len(df)), "methods": methods, "bootstrap_vs_naive_last": boot, "swarm_bands": band}


def _champion_band_coverage(df):
    """In-sample champion bands (from champion.json), applied to naive_last rows. Reported for reference only."""
    try:
        with open(CHAMPION_JSON, encoding="utf-8") as f:
            champ = json.load(f)
    except (OSError, ValueError):
        return None
    bands = champ.get("error_bands", {})
    lo, hi = [], []
    for c, naive in zip(df["commodity"], df["pred_naive"]):
        b = bands.get(c, bands.get("_ALL"))
        lo.append(naive * b["ratio_p10"])
        hi.append(naive * b["ratio_p90"])
    lo, hi = np.array(lo), np.array(hi)
    within = (df["price"].to_numpy() >= lo) & (df["price"].to_numpy() <= hi)
    return {"champion_in_sample_80pct_coverage": round(float(within.mean() * 100), 2),
            "note": "champion bands are fitted on the same test rows (in-sample), so this is optimistic"}


def _parity(panel, feats, window, retrain_every, df1):
    """Row-by-row check that the h=1 rows are the rows retrain.walk_forward scores."""
    encoders = pf.fit_encoders(panel)
    T = retrain.walk_forward(feats, encoders, window, retrain_every)
    ref = T[["commodity", "market", "unit", "pricetype", "per_i", "pred_naive", "price"]].copy()
    mine = df1[["commodity", "market", "unit", "pricetype", "per_i", "pred_naive", "price"]].copy()
    merged = ref.merge(mine, on=["commodity", "market", "unit", "pricetype", "per_i"], how="outer",
                       suffixes=("_retrain", "_swarm"), indicator=True)
    both = merged["_merge"].eq("both")
    max_naive_diff = float((merged.loc[both, "pred_naive_retrain"] - merged.loc[both, "pred_naive_swarm"]).abs().max()) if both.any() else float("nan")
    max_price_diff = float((merged.loc[both, "price_retrain"] - merged.loc[both, "price_swarm"]).abs().max()) if both.any() else float("nan")
    return {
        "retrain_rows": int(len(ref)),
        "backtest_rows_h1": int(len(mine)),
        "rows_matched": int(both.sum()),
        "rows_only_in_retrain": int((merged["_merge"] == "left_only").sum()),
        "rows_only_in_backtest": int((merged["_merge"] == "right_only").sum()),
        "max_abs_naive_pred_diff": max_naive_diff,
        "max_abs_price_diff": max_price_diff,
        "identical": bool(len(ref) == len(mine) and both.all() and max_naive_diff == 0.0),
    }


def run_backtest(panel, window=60, retrain_every=3, horizons=DEFAULT_HORIZONS, n_boot=2000,
                 swarm_kwargs=None):
    swarm_kwargs = dict(swarm_kwargs or {})
    dense = to_dense(panel)
    feats = pf.build_features(panel)
    months = np.sort(feats["per_i"].unique())
    maxm = int(months[-1])
    test = list(range(maxm - window + 1, maxm + 1))
    blocks = [test[i:i + retrain_every] for i in range(0, len(test), retrain_every)]
    frames, results = {}, {}
    for h in horizons:
        df = _walk_rows(dense, blocks, h, swarm_kwargs)
        frames[h] = df
        results[h] = _metrics(df, n_boot)
        results[h]["champion_band_reference"] = _champion_band_coverage(df) if h == 1 else None
    parity = _parity(panel, feats, window, retrain_every, frames[1]) if 1 in frames else None
    champ_check = None
    try:
        with open(CHAMPION_JSON, encoding="utf-8") as f:
            cb = json.load(f)["backtest"]
        if 1 in results:
            nv = results[1]["methods"]["naive_last"]
            champ_check = {"champion_json_n": cb["n"], "champion_json_mape": cb["mape"],
                           "reproduced_n": nv["n"], "reproduced_mape": nv["mape"],
                           "reproduced": bool(cb["n"] == nv["n"] and abs(cb["mape"] - nv["mape"]) < 5e-4)}
    except (OSError, ValueError, KeyError):
        champ_check = None
    return {
        "settings": {"window_months": window, "retrain_every": retrain_every, "horizons": list(horizons),
                     "bootstrap_n": n_boot, "bootstrap_seed": 0, "swarm": {"llm": "off", **swarm_kwargs},
                     "test_month_range": [int(test[0]), int(test[-1])], "blocks": len(blocks)},
        "data": {"series_count": int(len(dense.series)), "panel_rows": int(len(panel)),
                 "panel_first_year": int(panel["per"].dt.year.min()),
                 "grid_start_month_index": dense.grid_start, "observed_months_max_index": maxm},
        "parity_with_retrain_h1": parity,
        "champion_reproduction_h1": champ_check,
        "results": {str(h): results[h] for h in results},
        "verdict": verdict(results.get(1)),
    }


def verdict(h1):
    """Pipeline rule (retrain.py / pipeline README): challenger wins only if the bootstrap CI upper bound < 0."""
    if h1 is None:
        return {"decision": "KEEP-CHAMPION", "reason": "no horizon-1 results"}
    bs = h1["bootstrap_vs_naive_last"]["swarm"]
    promote = bs["ci95_hi_pp"] < 0
    return {
        "decision": "PROMOTE" if promote else "KEEP-CHAMPION",
        "rule": "challenger wins only if the 95% CI upper bound of MAPE(swarm) - MAPE(naive_last) is below 0",
        "point_pp": bs["point_pp"], "ci95_lo_pp": bs["ci95_lo_pp"], "ci95_hi_pp": bs["ci95_hi_pp"],
        "n_months": bs["n_months"],
        "note": ("Even on PROMOTE, the swarm is a candidate only: human review is required (pipeline rule) and "
                 "champion.json is not changed by this script.") if promote else
                "champion.json stays naive_last.",
    }


def render_markdown(out, date_str):
    s = out["settings"]
    L = [f"# Crop-price swarm challenger: walk-forward backtest ({date_str})", ""]
    L.append(f"- Window: {s['window_months']} months, block retrain every {s['retrain_every']} months, "
             f"{s['blocks']} blocks, horizons {s['horizons']}")
    L.append(f"- Swarm: LLM off; agents trend_seasonal + supply_season; weighted median; risk bands from past residuals")
    L.append(f"- Bootstrap: month-clustered paired, {s['bootstrap_n']} resamples, seed {s['bootstrap_seed']}")
    L.append(f"- Series: {out['data']['series_count']}; panel rows: {out['data']['panel_rows']}")
    p = out.get("parity_with_retrain_h1")
    if p:
        L.append(f"- Parity with retrain.walk_forward (h=1): identical={p['identical']}, "
                 f"matched rows={p['rows_matched']} (retrain {p['retrain_rows']}, backtest {p['backtest_rows_h1']}), "
                 f"max |naive diff|={p['max_abs_naive_pred_diff']}")
    c = out.get("champion_reproduction_h1")
    if c:
        L.append(f"- Champion reproduction (naive_last, h=1): champion.json n={c['champion_json_n']} "
                 f"MAPE={c['champion_json_mape']}; reproduced here n={c['reproduced_n']} MAPE={c['reproduced_mape']}; "
                 f"reproduced={c['reproduced']}")
    L.append("")
    for h, r in out["results"].items():
        L.append(f"## Horizon {h} month(s): n = {r['n']}")
        L.append("")
        L.append("| method | n | MAPE % | median APE % | P90 APE % | MASE (naive scale) | MASE (seasonal-naive scale) | rows with fallback |")
        L.append("|---|---|---|---|---|---|---|---|")
        for name, m in r["methods"].items():
            fb = m.get("fallback_rows", "-")
            L.append(f"| {name} | {m['n']} | {m['mape']} | {m['median_ape']} | {m['p90_ape']} | "
                     f"{m['mase_vs_naive_scale']['value']} | {m['mase_vs_seasonal_naive_scale']['value']} | {fb} |")
        L.append("")
        L.append("Paired bootstrap, MAPE(method) minus MAPE(naive_last), percentage points (negative = method better):")
        L.append("")
        L.append("| method | point | 95% CI low | 95% CI high | months |")
        L.append("|---|---|---|---|---|")
        for name, b in r["bootstrap_vs_naive_last"].items():
            L.append(f"| {name} | {b['point_pp']} | {b['ci95_lo_pp']} | {b['ci95_hi_pp']} | {b['n_months']} |")
        L.append("")
        bd = r["swarm_bands"]
        L.append(f"Swarm 80% bands: coverage {bd['swarm_80pct_coverage']}% (target about 80%), "
                 f"median upper/lower ratio {bd['swarm_median_upper_over_lower']}.")
        L.append("")
    v = out["verdict"]
    L.append("## Verdict")
    L.append("")
    L.append(f"**{v['decision']}** (horizon 1, pipeline rule). Paired MAPE difference swarm minus naive_last: "
             f"{v['point_pp']} pp, 95% CI [{v['ci95_lo_pp']}, {v['ci95_hi_pp']}] over {v['n_months']} months.")
    L.append("")
    L.append(v["note"])
    L.append("")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot", default=DEFAULT_SNAPSHOT)
    ap.add_argument("--window-months", type=int, default=60)
    ap.add_argument("--retrain-every", type=int, default=3)
    ap.add_argument("--horizons", default="1,3,6")
    ap.add_argument("--bootstrap-n", type=int, default=2000)
    ap.add_argument("--out-dir", default=RESULTS_DIR)
    args = ap.parse_args(argv)
    horizons = tuple(int(x) for x in args.horizons.split(",") if x.strip())
    with open(args.snapshot, "rb") as f:
        snap_sha = hashlib.sha256(f.read()).hexdigest()
    panel = load_panel(args.snapshot)
    out = run_backtest(panel, window=args.window_months, retrain_every=args.retrain_every,
                       horizons=horizons, n_boot=args.bootstrap_n)
    out["data"]["snapshot_file"] = os.path.basename(args.snapshot)
    out["data"]["snapshot_sha256"] = snap_sha
    date_str = dt.date.today().isoformat()
    os.makedirs(args.out_dir, exist_ok=True)
    json_path = os.path.join(args.out_dir, f"backtest_{date_str}.json")
    md_path = os.path.join(args.out_dir, f"backtest_{date_str}.md")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    md = render_markdown(out, date_str)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    print(md)
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    print(f"VERDICT: {out['verdict']['decision']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
