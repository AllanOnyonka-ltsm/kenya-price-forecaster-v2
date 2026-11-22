"""Leak-free features for the WFP Kenya monthly crop-price forecaster.

Ported from the backtest that established the results (backtest/common.py and backtest.py):
  * series key = (commodity, market, unit, pricetype), as in backtest.py
  * monthly calendar grid per series; missing months are NaN, not skipped
  * every feature for month t is computed from prices observed in months < t
    (shift(1) BEFORE rolling), so the target price at t never leaks into its own features
  * rf_logratio target = log(price_t / lag_1), features are ratios to lag_1

Deliberate differences from the backtest:
  * LabelEncoders are fitted on the current panel instead of loaded from the old model
    artifacts (vocabulary only, no target information).
  * Potato variants are merged and only the six tracked commodities are kept (see RAW_TO_CANONICAL).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.preprocessing import LabelEncoder

# Series key used by backtest.py.
KEY = ["commodity", "market", "unit", "pricetype"]

# Columns the pipeline needs. Validation fails if any is missing.
REQUIRED_COLUMNS = [
    "date", "admin1", "market", "latitude", "longitude", "category", "commodity",
    "unit", "priceflag", "pricetype", "currency", "price",
]

# Canonical names = COMMODITY_META values in price_api/app.py.
TRACKED_COMMODITIES = ("Bananas", "Cabbage", "Kale", "Onions (dry)", "Potatoes (Irish)", "Tomatoes")

# WFP raw commodity name -> canonical name. Anything not listed is ignored.
# "Potatoes (Irish, red)" and "Potatoes (Irish, white)" are potato variants and are merged
# into "Potatoes (Irish)". "Onions (red)" is a different product and is deliberately NOT merged.
RAW_TO_CANONICAL = {
    "Bananas": "Bananas",
    "Cabbage": "Cabbage",
    "Kale": "Kale",
    "Onions (dry)": "Onions (dry)",
    "Tomatoes": "Tomatoes",
    "Potatoes (Irish)": "Potatoes (Irish)",
    "Potatoes (Irish, red)": "Potatoes (Irish)",
    "Potatoes (Irish, white)": "Potatoes (Irish)",
}

RATIO_CATEGORICALS = ["commodity", "market", "pricetype", "unit", "admin1"]
RATIO_COLUMNS = ["log_lag1", "r3", "r6", "rma3", "rma6", "rvol", "sin", "cos", "lat", "lon", "year"] + RATIO_CATEGORICALS


def read_wfp_csv(path) -> pd.DataFrame:
    """Read a WFP export as strings. Accepts .csv or .csv.gz; drops HXL '#' tag rows.

    An empty file returns an empty frame with no columns (validation reports it).
    """
    try:
        raw = pd.read_csv(path, dtype=str, keep_default_na=False, compression="infer")
    except pd.errors.EmptyDataError:
        return pd.DataFrame()
    if "date" in raw.columns:
        raw = raw[~raw["date"].str.startswith("#")].reset_index(drop=True)
    return raw


ACCEPTED_FLAGS = ("actual", "aggregate")


def monthly_panel(raw: pd.DataFrame, flag_stats: dict | None = None) -> pd.DataFrame:
    """KES rows with priceflag 'actual' or 'aggregate' (price > 0), tracked commodities only.
    Within a (series, month) that has any 'actual' row, 'aggregate' rows are dropped. The monthly
    value is the median of what remains. Pass a dict as flag_stats to receive row counts by flag.
    'actual,aggregate' and other flag values are excluded."""
    d = raw.loc[raw["currency"].eq("KES") & raw["priceflag"].isin(ACCEPTED_FLAGS)].copy()
    d["commodity"] = d["commodity"].map(RAW_TO_CANONICAL)
    d = d[d["commodity"].notna()].copy()
    d["date"] = pd.to_datetime(d["date"], format="%Y-%m-%d", errors="coerce")
    d["price"] = pd.to_numeric(d["price"], errors="coerce")
    d["latitude"] = pd.to_numeric(d["latitude"], errors="coerce")
    d["longitude"] = pd.to_numeric(d["longitude"], errors="coerce")
    d = d[d["date"].notna() & d["price"].gt(0)].copy()
    d["per"] = d["date"].dt.to_period("M")
    # Prefer 'actual' over 'aggregate' for the same series and month.
    d["_actual"] = d["priceflag"].eq("actual")
    month_has_actual = d.groupby(KEY + ["per"])["_actual"].transform("any")
    dropped = int((~d["_actual"] & month_has_actual).sum())
    d = d[d["_actual"] | ~month_has_actual].copy()
    if flag_stats is not None:
        flag_stats.update({
            "rows_used_actual": int(d["_actual"].sum()),
            "rows_used_aggregate": int((~d["_actual"]).sum()),
            "aggregate_rows_dropped_actual_present": dropped,
        })
    panel = (
        d.groupby(KEY + ["per"], sort=False)
        .agg(price=("price", "median"), date=("date", "max"), admin1=("admin1", "first"),
             category=("category", "first"), latitude=("latitude", "first"),
             longitude=("longitude", "first"))
        .reset_index()
    )
    return panel


def build_features(panel: pd.DataFrame) -> pd.DataFrame:
    """Leak-free monthly features. Returns observed rows that have a prior-month price (lag_1).

    Feature rule: every feature at month t uses prices from months < t only.
    """
    parts = []
    for k, t in panel.groupby(KEY, sort=False):
        s = t.set_index("per")["price"].sort_index()
        grid = pd.period_range(s.index.min(), s.index.max(), freq="M")
        s = s.reindex(grid)
        past = s.shift(1)  # shift BEFORE rolling: the window ends at t-1
        f = pd.DataFrame(
            {
                "price": s,
                "lag_1": past,
                "lag_3": s.shift(3),
                "lag_6": s.shift(6),
                "lag_12": s.shift(12),
                "ma_3": past.rolling(3, min_periods=1).mean(),
                "ma_6": past.rolling(6, min_periods=1).mean(),
                "vol_6": past.rolling(6, min_periods=2).std(),
            },
            index=grid,
        )
        f = f[f["price"].notna()].copy()
        for c, v in zip(KEY, k):
            f[c] = v
        f["per"] = f.index
        parts.append(f)
    f = pd.concat(parts, ignore_index=True)
    meta = panel[KEY + ["per", "admin1", "category", "latitude", "longitude", "date"]]
    f = f.merge(meta, on=KEY + ["per"], how="left")
    f["month"] = f["per"].dt.month
    f["quarter"] = (f["month"] - 1) // 3 + 1
    f["sin_month"] = np.sin(2 * np.pi * f["month"] / 12)
    f["cos_month"] = np.cos(2 * np.pi * f["month"] / 12)
    f["per_i"] = f["per"].dt.year * 12 + f["per"].dt.month - 1
    # Same API-style fill as backtest.py: missing deeper history falls back to lag_1; vol to 0.
    for c in ["lag_3", "lag_6", "ma_3", "ma_6"]:
        f[c] = f[c].fillna(f["lag_1"])
    f["vol_6"] = f["vol_6"].fillna(0.0)
    f = f[f["lag_1"].notna()].reset_index(drop=True)
    return f


def fit_encoders(panel: pd.DataFrame) -> dict:
    """Vocabulary-only label encoders for the categorical columns."""
    enc = {}
    for c in RATIO_CATEGORICALS:
        vals = panel[c].astype(str).fillna("unknown")
        enc[c] = LabelEncoder().fit(vals)
    return enc


def ratio_features(f: pd.DataFrame, encoders: dict) -> pd.DataFrame:
    """Feature matrix for the rf_logratio challenger (all inputs are past-only)."""
    lat = f["latitude"].fillna(0.0)
    lon = f["longitude"].fillna(0.0)
    X = pd.DataFrame(
        {
            "log_lag1": np.log(f["lag_1"]),
            "r3": f["lag_3"] / f["lag_1"],
            "r6": f["lag_6"] / f["lag_1"],
            "rma3": f["ma_3"] / f["lag_1"],
            "rma6": f["ma_6"] / f["lag_1"],
            "rvol": f["vol_6"] / f["lag_1"],
            "sin": f["sin_month"],
            "cos": f["cos_month"],
            "lat": lat,
            "lon": lon,
            "year": f["per"].dt.year,
        },
        index=f.index,
    )
    for c in RATIO_CATEGORICALS:
        X[c] = encoders[c].transform(f[c].astype(str).fillna("unknown"))
    return X[RATIO_COLUMNS]


def ratio_target(f: pd.DataFrame) -> np.ndarray:
    return np.log(f["price"] / f["lag_1"]).to_numpy()
