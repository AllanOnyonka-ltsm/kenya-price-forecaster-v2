#!/usr/bin/env python3
"""Self-healing monthly retraining for the WFP Kenya crop-price forecaster (DRAFT, not wired to the repo).

Flow: acquire CSV -> validate vs previous snapshot -> walk-forward backtest
(champion naive_last vs challenger rf_logratio) -> paired month-clustered bootstrap
-> write champion.json (naive_last only; RF is never auto-promoted).

Exit codes:
  0  updated   champion.json written (refreshed bands / new data_through)
  1  unchanged data_through is not newer than the previous champion; nothing written
  2  validation failed; champion.json untouched
  3  unexpected error; report.md and metrics.json are still written

Run: python pipeline/retrain.py --csv <file> --previous-snapshot <file> --out <dir> --window-months 24 [--dry-run]
Omit --csv to download from HDX.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import shutil
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import joblib  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.ensemble import RandomForestRegressor  # noqa: E402

from features import (RATIO_COLUMNS, build_features, fit_encoders, monthly_panel,  # noqa: E402
                      ratio_features, ratio_target, read_wfp_csv, TRACKED_COMMODITIES, KEY)
from validate import validate_snapshot  # noqa: E402
from latest_prices import LATEST_PRICES_FILE, build_latest_prices, write_latest_prices  # noqa: E402

EXIT_UPDATED, EXIT_UNCHANGED, EXIT_VALIDATION, EXIT_ERROR = 0, 1, 2, 3

CKAN_PACKAGE_URL = "https://data.humdata.org/api/3/action/package_show?id=wfp-food-prices-for-kenya"
RESOURCE_NAME = "Kenya - Food Prices"
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
DOWNLOAD_TIMEOUT_S = 60
DOWNLOAD_ATTEMPTS = 3
USER_AGENT = "market-forecaster-retrain/draft"

RF_PARAMS = dict(n_estimators=100, max_depth=15, min_samples_split=5, random_state=42, n_jobs=-1)
MIN_HISTORY_MONTHS = 12          # training history required before the first test month
MODEL_FILE = "random_forest_price_model.pkl"
PRICETYPE_BAND_MIN_N = 30        # per (commodity, pricetype) groups with fewer rows get no band
CHAMPION_FILE = "champion.json"
CANDIDATE_DIR = "candidate"
SNAPSHOT_FILE = "wfp_snapshot.csv.gz"
HUMAN_REVIEW_FLAG = "HUMAN REVIEW: challenger beat champion"


class DownloadTooLarge(Exception):
    """Not retried: the size cap is deterministic."""


# ---------------------------------------------------------------- download
def resolve_csv_url(opener=urllib.request.urlopen, timeout=DOWNLOAD_TIMEOUT_S) -> str:
    req = urllib.request.Request(CKAN_PACKAGE_URL, headers={"User-Agent": USER_AGENT})
    with opener(req, timeout=timeout) as r:
        payload = json.loads(r.read().decode("utf-8"))
    if not payload.get("success"):
        raise RuntimeError("CKAN package_show returned success=false")
    for res in payload["result"]["resources"]:
        if str(res.get("name", "")).strip().lower() == RESOURCE_NAME.lower() and res.get("url"):
            url = res["url"]
            if urllib.parse.urlparse(url).scheme != "https":
                raise RuntimeError(f"refusing non-https resource URL: {url}")
            return url
    raise RuntimeError(f"resource named {RESOURCE_NAME!r} not found in CKAN package")


def stream_to_file(url, dest, opener=urllib.request.urlopen, timeout=DOWNLOAD_TIMEOUT_S,
                   max_bytes=MAX_DOWNLOAD_BYTES) -> int:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    tmp = dest + ".part"
    total = 0
    with opener(req, timeout=timeout) as r, open(tmp, "wb") as fh:
        while True:
            chunk = r.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise DownloadTooLarge(f"download exceeded {max_bytes} bytes")
            fh.write(chunk)
    os.replace(tmp, dest)
    return total


def download_csv(dest_dir, opener=urllib.request.urlopen, attempts=DOWNLOAD_ATTEMPTS, sleep=time.sleep):
    """Resolve the resource through CKAN and stream it. Retries 3x with exponential backoff."""
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, "wfp_food_prices_ken.csv")
    last = None
    for attempt in range(1, attempts + 1):
        try:
            url = resolve_csv_url(opener)
            size = stream_to_file(url, dest, opener)
            return dest, size, url
        except DownloadTooLarge:
            raise
        except (OSError, RuntimeError, ValueError) as e:  # URLError and timeouts are OSError
            last = e
            if attempt < attempts:
                sleep(5 * 2 ** (attempt - 1))
    raise RuntimeError(f"download failed after {attempts} attempts: {last}")


# ---------------------------------------------------------------- metrics
def ape(pred, y):
    return np.abs(np.asarray(pred) - np.asarray(y)) / np.asarray(y)


def summarize(pred, y) -> dict:
    a = ape(pred, y)
    return {"n": int(len(a)), "mape": round(float(a.mean() * 100), 3),
            "median_ape": round(float(np.median(a) * 100), 3),
            "p90_ape": round(float(np.quantile(a, 0.9) * 100), 3)}


def paired_bootstrap(T: pd.DataFrame, challenger="pred_rf", champion="pred_naive",
                     n_boot=2000, seed=0) -> dict:
    """Month-clustered paired bootstrap of MAPE(challenger) - MAPE(champion), in percentage points.

    Negative = challenger better. Months are resampled with replacement so that the
    within-month correlation of errors across series is kept.
    """
    y = T["price"].to_numpy()
    dd = ape(T[challenger], y) - ape(T[champion], y)
    months = T["per_i"].to_numpy()
    uniq = np.unique(months)
    groups = [dd[months == m] for m in uniq]
    rng = np.random.default_rng(seed)
    draws = np.empty(n_boot)
    for i in range(n_boot):
        pick = rng.integers(0, len(uniq), len(uniq))
        draws[i] = np.concatenate([groups[j] for j in pick]).mean() * 100
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return {"point_pp": round(float(dd.mean() * 100), 3), "ci95_lo_pp": round(float(lo), 3),
            "ci95_hi_pp": round(float(hi), 3), "n_boot": int(n_boot), "n_months": int(len(uniq))}


def _band(r) -> dict:
    return {"n": int(len(r)), "ratio_p10": round(float(np.quantile(r, 0.10)), 4),
            "ratio_p50": round(float(np.quantile(r, 0.50)), 4),
            "ratio_p90": round(float(np.quantile(r, 0.90)), 4)}


def error_bands(T: pd.DataFrame, pred_col: str) -> dict:
    """ratio = actual / prediction, pooled per canonical commodity, plus _ALL."""
    r = (T["price"] / T[pred_col]).to_numpy()
    bands = {}
    commodities = T["commodity"].to_numpy()
    for c in sorted(set(commodities)):
        bands[c] = _band(r[commodities == c])
    bands["_ALL"] = _band(r)
    return bands


def error_bands_by_pricetype(T: pd.DataFrame, pred_col: str, min_n: int = PRICETYPE_BAND_MIN_N) -> dict:
    """Same ratio as error_bands, pooled per (canonical commodity, pricetype), from the same rows.

    The per-commodity bands have no minimum n, so the minimum here is a new rule: a group with
    fewer than min_n rows is skipped (no band is emitted for it, and none is invented).
    Only (commodity, pricetype) pairs that occur in T appear in the output.
    """
    r = (T["price"] / T[pred_col]).to_numpy()
    commodities = T["commodity"].to_numpy()
    pricetypes = T["pricetype"].to_numpy()
    bands = {}
    for c in sorted(set(commodities)):
        for pt in sorted(set(pricetypes[commodities == c])):
            mask = (commodities == c) & (pricetypes == pt)
            if mask.sum() < min_n:
                continue  # too few rows for a stable band
            bands.setdefault(c, {})[pt] = _band(r[mask])
    return bands


# ---------------------------------------------------------------- walk-forward
def walk_forward(feats: pd.DataFrame, encoders: dict, window: int, retrain_every: int) -> pd.DataFrame:
    """Expanding-window walk-forward. Challenger retrains every `retrain_every` months.

    Training rows: per_i < first month of the block (target fully observed before the block).
    """
    months = np.sort(feats["per_i"].unique())
    maxm = int(months[-1])
    test = list(range(maxm - window + 1, maxm + 1))
    blocks = [test[i:i + retrain_every] for i in range(0, len(test), retrain_every)]
    X = ratio_features(feats, encoders).to_numpy()
    y_lr = ratio_target(feats)
    per_i = feats["per_i"].to_numpy()
    parts = []
    for b in blocks:
        te = np.isin(per_i, b)
        tr = per_i < b[0]
        if tr.sum() == 0 or te.sum() == 0:
            continue
        model = RandomForestRegressor(**RF_PARAMS).fit(X[tr], y_lr[tr])
        part = feats.loc[te].copy()
        part["pred_naive"] = part["lag_1"]
        part["pred_rf"] = part["lag_1"] * np.exp(model.predict(X[te]))
        parts.append(part)
    if not parts:
        raise ValueError("no walk-forward block had training history")
    return pd.concat(parts, ignore_index=True)


def fit_final_rf(feats: pd.DataFrame, encoders: dict):
    X = ratio_features(feats, encoders).to_numpy()
    return RandomForestRegressor(**RF_PARAMS).fit(X, ratio_target(feats))


# ---------------------------------------------------------------- champion contract
def make_champion(model_type, model_file, data_through, window, bt_metrics, bands, created_at=None,
                  bands_by_pricetype=None) -> dict:
    created_at = created_at or dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    return {
        "schema_version": 2,
        "model_type": model_type,
        "model_file": model_file,
        "created_at": created_at,
        "data_through": data_through,
        "backtest": {
            "window_months": int(window),
            "n": int(bt_metrics["n"]),
            "mape": float(bt_metrics["mape"]),          # percent, same unit as backtest.py MAPE
            "naive_mape": float(bt_metrics["naive_mape"]),
            "median_ape": float(bt_metrics["median_ape"]),
        },
        "error_bands": bands,
        "error_bands_by_pricetype": bands_by_pricetype if bands_by_pricetype is not None else {},
    }


def check_champion_contract(obj) -> list[str]:
    """Returns a list of contract violations (empty list = valid). Used before every write."""
    errs = []
    if not isinstance(obj, dict):
        return ["not an object"]
    if obj.get("schema_version") not in (1, 2):
        errs.append("schema_version must be 1 or 2")
    if obj.get("model_type") not in ("naive_last", "random_forest"):
        errs.append("model_type must be naive_last or random_forest")
    if obj.get("model_file") is not None and not isinstance(obj.get("model_file"), str):
        errs.append("model_file must be null or string")
    if obj.get("model_type") == "naive_last" and obj.get("model_file") is not None:
        errs.append("naive_last must have model_file null")
    if obj.get("model_type") == "random_forest" and obj.get("model_file") != MODEL_FILE:
        errs.append(f"random_forest must have model_file {MODEL_FILE}")
    try:
        dt.datetime.fromisoformat(str(obj.get("created_at")))
    except ValueError:
        errs.append("created_at is not ISO 8601")
    dtr = obj.get("data_through")
    if not (isinstance(dtr, str) and len(dtr) == 7 and dtr[4] == "-" and dtr[:4].isdigit() and dtr[5:].isdigit()):
        errs.append("data_through must be YYYY-MM")
    bt = obj.get("backtest", {})
    for k in ("window_months", "n", "mape", "naive_mape", "median_ape"):
        if k not in bt:
            errs.append(f"backtest.{k} missing")
    bands = obj.get("error_bands", {})
    if "_ALL" not in bands:
        errs.append("error_bands._ALL missing")
    for name, b in bands.items():
        for k in ("n", "ratio_p10", "ratio_p50", "ratio_p90"):
            if k not in b:
                errs.append(f"error_bands.{name}.{k} missing")
    # Optional in schema 2; checked whenever present.
    if "error_bands_by_pricetype" in obj:
        by_pt = obj["error_bands_by_pricetype"]
        if not isinstance(by_pt, dict):
            errs.append("error_bands_by_pricetype must be an object")
        else:
            for name, per_type in by_pt.items():
                if not isinstance(per_type, dict):
                    errs.append(f"error_bands_by_pricetype.{name} must be an object")
                    continue
                for pt, b in per_type.items():
                    for k in ("n", "ratio_p10", "ratio_p50", "ratio_p90"):
                        if not isinstance(b, dict) or k not in b:
                            errs.append(f"error_bands_by_pricetype.{name}.{pt}.{k} missing")
    return errs


# ---------------------------------------------------------------- report / metrics
def _jsonable(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp, dt.date)):
        return str(o)
    return str(o)


def render_report(st: dict) -> str:
    L = []
    add = L.append
    add("# WFP Kenya price model: monthly retrain report")
    add("")
    add(f"- **Exit code:** {st.get('exit_code')}")
    add(f"- **Decision:** {st.get('decision', '-')}")
    if st.get("human_review"):
        add(f"- **{st['human_review']}** (RF is never auto-promoted; see pipeline/README.md)")
    add(f"- **Run started (UTC):** {st.get('started_utc')}  |  **runtime:** {st.get('runtime_s')} s")
    add(f"- **Data source:** {st.get('source', '-')}")
    add(f"- **data_through:** {st.get('data_through', '-')}  (previous: {st.get('prev_data_through', '-')})")
    add(f"- **Dry run:** {st.get('dry_run')}")
    add("")
    ds = st.get("data_summary")
    if ds:
        add("## Data summary")
        for k, v in ds.items():
            add(f"- {k}: {v}")
        add("")
    v = st.get("validation")
    if v:
        add("## Validation")
        add(f"- ok: **{v['ok']}**")
        for p in v["problems"]:
            add(f"- PROBLEM: {p}")
        for w in v["warnings"]:
            add(f"- warning: {w}")
        add("- stats: " + ", ".join(f"{k}={v['stats'][k]}" for k in v["stats"]))
        add("")
    bt = st.get("backtest_settings")
    if bt:
        add("## Backtest")
        add(f"- window: {bt['window_months']} months, challenger retrained every {bt['retrain_every']} months")
        add(f"- series key: {', '.join(KEY)}; tracked commodities: {', '.join(TRACKED_COMMODITIES)}")
        add("")
    m = st.get("metrics")
    if m:
        add("## Metrics (walk-forward, all test rows)")
        add("| method | n | MAPE % | median APE % | P90 APE % |")
        add("|---|---|---|---|---|")
        for k in ("naive_last", "rf_logratio"):
            r = m[k]
            add(f"| {k} | {r['n']} | {r['mape']} | {r['median_ape']} | {r['p90_ape']} |")
        add("")
    pc = st.get("per_commodity")
    if pc:
        add("## Per commodity (MAPE %)")
        add("| commodity | n | naive_last | rf_logratio |")
        add("|---|---|---|---|")
        for r in pc:
            add(f"| {r['commodity']} | {r['n']} | {r['naive_last']} | {r['rf_logratio']} |")
        add("")
    ppt = st.get("per_pricetype")
    if ppt:
        add("## Per pricetype (MAPE %; series key includes pricetype, so Retail and Wholesale never mix)")
        add("| pricetype | n | naive_last | rf_logratio |")
        add("|---|---|---|---|")
        for r in ppt:
            add(f"| {r['pricetype']} | {r['n']} | {r['naive_last']} | {r['rf_logratio']} |")
        add("")
    bs = st.get("bootstrap")
    if bs:
        add("## Paired month-clustered bootstrap: MAPE(rf_logratio) - MAPE(naive_last)")
        add(f"- point estimate: {bs['point_pp']} pct-pts; 95% CI [{bs['ci95_lo_pp']}, {bs['ci95_hi_pp']}] "
            f"({bs['n_boot']} resamples, {bs['n_months']} months)")
        add("- rule: challenger wins only if CI upper bound < 0. Negative = challenger better.")
        add("")
    bands = st.get("bands")
    if bands:
        add("## Champion error bands (naive_last, ratio = actual / prediction)")
        add("| commodity | n | p10 | p50 | p90 |")
        add("|---|---|---|---|---|")
        for k, b in bands.items():
            add(f"| {k} | {b['n']} | {b['ratio_p10']} | {b['ratio_p50']} | {b['ratio_p90']} |")
        add("")
    bands_pt = st.get("bands_by_pricetype")
    if bands_pt:
        add(f"## Champion error bands by pricetype (groups with n < {PRICETYPE_BAND_MIN_N} skipped)")
        add("| commodity | pricetype | n | p10 | p50 | p90 |")
        add("|---|---|---|---|---|---|")
        for c, per_type in bands_pt.items():
            for pt, b in per_type.items():
                add(f"| {c} | {pt} | {b['n']} | {b['ratio_p10']} | {b['ratio_p50']} | {b['ratio_p90']} |")
        add("")
    if st.get("error"):
        add("## Error")
        add("```")
        add(st["error"].rstrip())
        add("```")
        add("")
    add("## Rollback")
    add("If a bad champion ships: restore `price_api/artifacts/champion.previous.json` over `champion.json`.")
    add("")
    return "\n".join(L)


def write_report(out, st):
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "report.md"), "w", encoding="utf-8") as f:
        f.write(render_report(st))
    with open(os.path.join(out, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2, default=_jsonable)


# ---------------------------------------------------------------- main flow
def _read_previous_through(args, prev_raw):
    if args.previous_champion and os.path.exists(args.previous_champion):
        with open(args.previous_champion, encoding="utf-8") as f:
            return json.load(f).get("data_through"), "previous champion.json"
    if prev_raw is not None and len(prev_raw):
        p = monthly_panel(prev_raw)
        if len(p):
            last = p["per"].max()
            return f"{last.year}-{last.month:02d}", "previous snapshot"
    return None, None


def _run(args, st) -> int:
    if args.csv:
        csv_path = args.csv
        st["source"] = f"local file {args.csv}"
    else:
        csv_path, size, url = download_csv(os.path.join(args.out, "input"))
        st["source"] = url
        st["download_bytes"] = size

    new_raw = read_wfp_csv(csv_path)
    prev_raw = read_wfp_csv(args.previous_snapshot) if args.previous_snapshot else None

    val = validate_snapshot(new_raw, prev_raw)
    st["validation"] = val
    if not val["ok"]:
        st["decision"] = "VALIDATION FAILED: champion.json left untouched"
        return EXIT_VALIDATION

    flag_stats = {}
    panel = monthly_panel(new_raw, flag_stats)
    if panel.empty:
        val["problems"].append("no usable rows after filtering (KES, actual, tracked commodities, price > 0)")
        val["ok"] = False
        st["decision"] = "VALIDATION FAILED: champion.json left untouched"
        return EXIT_VALIDATION
    last = panel["per"].max()
    first = panel["per"].min()
    data_through = f"{last.year}-{last.month:02d}"
    st["data_through"] = data_through
    st["data_summary"] = {
        "rows_raw": len(new_raw),
        "rows_usable_panel": len(panel),
        "series_key": "(" + ", ".join(KEY) + ")",
        "series_count": int(panel.groupby(KEY).ngroups),
        "tracked_commodities_present": sorted(panel["commodity"].unique().tolist()),
        "panel_range": f"{first} .. {last}",
        "priceflag_rows_used_in_panel": flag_stats,
    }

    if not args.dry_run:
        _write_latest_prices(new_raw, args.out, st)

    prev_through, prev_src = _read_previous_through(args, prev_raw)
    st["prev_data_through"] = f"{prev_through} ({prev_src})" if prev_through else "none"
    if prev_through and data_through <= prev_through:
        st["decision"] = "NO NEW DATA: data_through is not newer than the previous champion; nothing written"
        return EXIT_UNCHANGED

    window = args.window_months
    need = window + MIN_HISTORY_MONTHS
    span = (last.year * 12 + last.month) - (first.year * 12 + first.month) + 1
    if span < need:
        val["problems"].append(f"insufficient history: {span} months < window + {MIN_HISTORY_MONTHS}")
        val["ok"] = False
        st["decision"] = "VALIDATION FAILED: insufficient history; champion.json left untouched"
        return EXIT_VALIDATION

    encoders = fit_encoders(panel)
    feats = build_features(panel)
    T = walk_forward(feats, encoders, window, args.retrain_every)
    st["backtest_settings"] = {"window_months": window, "retrain_every": args.retrain_every}

    m_naive = summarize(T["pred_naive"], T["price"])
    m_rf = summarize(T["pred_rf"], T["price"])
    st["metrics"] = {"naive_last": m_naive, "rf_logratio": m_rf}
    pc = []
    for c, g in T.groupby("commodity"):
        pc.append({"commodity": c, "n": len(g),
                   "naive_last": summarize(g["pred_naive"], g["price"])["mape"],
                   "rf_logratio": summarize(g["pred_rf"], g["price"])["mape"]})
    st["per_commodity"] = pc
    pt = []
    for ptype, g in T.groupby("pricetype"):
        pt.append({"pricetype": ptype, "n": len(g),
                   "naive_last": summarize(g["pred_naive"], g["price"])["mape"],
                   "rf_logratio": summarize(g["pred_rf"], g["price"])["mape"]})
    st["per_pricetype"] = pt

    bs = paired_bootstrap(T, n_boot=args.bootstrap_n)
    st["bootstrap"] = bs
    rf_wins = bs["ci95_hi_pp"] < 0

    bands_naive = error_bands(T, "pred_naive")
    st["bands"] = bands_naive
    bands_pt_naive = error_bands_by_pricetype(T, "pred_naive")
    st["bands_by_pricetype"] = bands_pt_naive

    champion = make_champion("naive_last", None, data_through, window, {**m_naive, "naive_mape": m_naive["mape"]},
                             bands_naive, bands_by_pricetype=bands_pt_naive)
    errs = check_champion_contract(champion)
    if errs:
        raise RuntimeError(f"champion.json violates contract: {errs}")

    candidate = None
    if rf_wins:
        st["human_review"] = HUMAN_REVIEW_FLAG
        cand_dir = os.path.join(args.out, CANDIDATE_DIR)
        final = fit_final_rf(feats, encoders)
        bands_rf = error_bands(T, "pred_rf")
        candidate = make_champion("random_forest", MODEL_FILE, data_through, window,
                                  {**m_rf, "naive_mape": m_naive["mape"]}, bands_rf,
                                  bands_by_pricetype=error_bands_by_pricetype(T, "pred_rf"))
        if check_champion_contract(candidate):
            raise RuntimeError("candidate violates contract")
        st["candidate_bands"] = bands_rf
        if not args.dry_run:
            os.makedirs(cand_dir, exist_ok=True)
            joblib.dump({"model": final, "encoders": encoders, "columns": RATIO_COLUMNS,
                         "target": "log(price/lag_1)", "data_through": data_through},
                        os.path.join(cand_dir, MODEL_FILE))
            with open(os.path.join(cand_dir, "champion_candidate.json"), "w", encoding="utf-8") as f:
                json.dump(candidate, f, indent=2)

    if args.dry_run:
        st["decision"] = ("DRY RUN: would update champion.json (naive_last, refreshed bands)"
                          + ("; challenger beat champion, HUMAN REVIEW" if rf_wins else "")
                          + "; no files written except report")
        return EXIT_UPDATED

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, CHAMPION_FILE), "w", encoding="utf-8") as f:
        json.dump(champion, f, indent=2)
    if args.previous_champion and os.path.exists(args.previous_champion):
        shutil.copyfile(args.previous_champion, os.path.join(args.out, "champion.previous.json"))
    _write_snapshot(csv_path, os.path.join(args.out, SNAPSHOT_FILE))
    st["decision"] = ("UPDATED: champion.json written (naive_last, refreshed bands, data_through "
                      f"{data_through})" + ("; challenger beat champion, HUMAN REVIEW, see candidate/"
                                            if rf_wins else "; challenger did not beat champion"))
    return EXIT_UPDATED


def _write_latest_prices(raw, out, st):
    """Side output for the API's nearby-market prices, written once the snapshot has validated.
    The workflow copies it only on exit 0. A failure is recorded in the report and never changes the exit code."""
    try:
        payload = build_latest_prices(raw)
        write_latest_prices(os.path.join(out, LATEST_PRICES_FILE), payload)
        st["latest_prices"] = {"n_entries": payload["n_entries"], "data_through": payload["data_through"]}
    except Exception:  # noqa: BLE001 - side output only
        st["latest_prices_error"] = traceback.format_exc()


def _write_snapshot(src, dst):
    """Store the validated CSV gzipped as the snapshot the next run compares against."""
    if src.endswith(".gz"):
        shutil.copyfile(src, dst)
        return
    with open(src, "rb") as fi, gzip.open(dst, "wb") as fo:
        shutil.copyfileobj(fi, fo)


def run(args) -> int:
    t0 = time.time()
    st = {"started_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
          "dry_run": bool(args.dry_run), "window_months": args.window_months}
    try:
        code = _run(args, st)
    except Exception:  # noqa: BLE001 - any unexpected failure is exit 3, report still written
        st["error"] = traceback.format_exc()
        st["decision"] = "UNEXPECTED ERROR: champion.json left untouched"
        code = EXIT_ERROR
    st["exit_code"] = code
    st["runtime_s"] = round(time.time() - t0, 1)
    try:
        write_report(args.out, st)
    except OSError as e:
        print(f"could not write report: {e}", file=sys.stderr)
    print(f"[retrain] exit={code} decision={st.get('decision')}")
    return code


def build_arg_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--csv", help="local WFP CSV (.csv or .csv.gz). Omit to download from HDX.")
    p.add_argument("--previous-snapshot", help="last accepted CSV snapshot to validate against")
    p.add_argument("--previous-champion", help="current price_api/artifacts/champion.json (rollback copy + no-new-data check)")
    p.add_argument("--out", required=True, help="output directory")
    p.add_argument("--window-months", type=int, default=60,
                   help="test window in months (default 60; short windows give noisy bands)")
    p.add_argument("--retrain-every", type=int, default=3, help="months between challenger retrains (3-6)")
    p.add_argument("--bootstrap-n", type=int, default=2000)
    p.add_argument("--dry-run", action="store_true", help="never write champion.json or candidate files")
    return p


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.window_months < 1 or args.retrain_every < 1:
        print("window-months and retrain-every must be >= 1", file=sys.stderr)
        return EXIT_ERROR
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
