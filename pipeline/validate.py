"""Validate a freshly downloaded WFP Kenya CSV against the previous accepted snapshot.

validate_snapshot() never raises for bad data; it returns a structured result:
    {"ok": bool, "problems": [...], "warnings": [...], "stats": {...}}
Any entry in "problems" makes ok False (the pipeline then exits 2 and leaves champion.json alone).
"""
from __future__ import annotations

import pandas as pd

from features import RAW_TO_CANONICAL, REQUIRED_COLUMNS

MAX_ROW_SHRINK = 0.05           # WFP data is append-mostly: row count may not drop by more than 5%
MAX_BAD_VALUE_SHARE = 0.001     # unparseable dates / non-numeric prices above 0.1% are a problem
MIN_TRACKED_SHARE_RATIO = 0.5   # tracked-commodity share of rows may not fall below 50% of previous
DUPLICATE_KEY = ["date", "market", "commodity", "unit", "pricetype"]


def _max_date(df: pd.DataFrame):
    return pd.to_datetime(df["date"], format="%Y-%m-%d", errors="coerce").max()


def validate_snapshot(new: pd.DataFrame, prev: pd.DataFrame | None = None) -> dict:
    problems: list[str] = []
    warnings: list[str] = []
    stats: dict = {}

    missing = [c for c in REQUIRED_COLUMNS if c not in new.columns]
    if missing:
        problems.append(f"missing required columns: {missing}")
    if len(new) == 0:
        problems.append("no data rows")
    if problems:
        return _result(problems, warnings, stats)

    n_new = len(new)
    stats["rows_new"] = n_new

    # Previous snapshot comparisons (skipped with a warning if the previous file is unusable).
    have_prev = prev is not None and len(prev) > 0 and all(c in prev.columns for c in REQUIRED_COLUMNS)
    if prev is not None and len(prev) > 0 and not have_prev:
        warnings.append("previous snapshot is missing required columns; comparisons skipped")
    if have_prev:
        stats["rows_prev"] = len(prev)
        shrink = 1 - n_new / len(prev)
        stats["row_change_pct"] = round(-shrink * 100, 2)
        if shrink > MAX_ROW_SHRINK:
            problems.append(f"row count shrank {shrink:.1%} ({len(prev)} -> {n_new}); limit is {MAX_ROW_SHRINK:.0%}")

    # Dates.
    dates = pd.to_datetime(new["date"], format="%Y-%m-%d", errors="coerce")
    bad_dates = int(dates.isna().sum())
    stats["unparseable_dates"] = bad_dates
    if bad_dates / n_new > MAX_BAD_VALUE_SHARE:
        problems.append(f"{bad_dates} rows have unparseable dates")
    elif bad_dates:
        warnings.append(f"{bad_dates} rows have unparseable dates (ignored)")
    max_new = dates.max()
    if pd.isna(max_new):
        problems.append("no parseable dates")
    else:
        stats["max_date_new"] = str(max_new.date())
    if have_prev:
        max_prev = _max_date(prev)
        stats["max_date_prev"] = str(max_prev.date()) if pd.notna(max_prev) else None
        if pd.notna(max_new) and pd.notna(max_prev) and max_new < max_prev:
            problems.append(f"max date went backwards: {max_prev.date()} -> {max_new.date()}")

    # Prices: numeric, non-negative. Empty cells are reported as warnings only.
    raw_price = new["price"].astype(str)
    empty = raw_price.str.strip().eq("")
    num = pd.to_numeric(raw_price, errors="coerce")
    unparsed = int((num.isna() & ~empty).sum())
    negative = int((num < 0).sum())
    stats["empty_prices"] = int(empty.sum())
    stats["non_numeric_prices"] = unparsed
    stats["negative_prices"] = negative
    if unparsed / n_new > MAX_BAD_VALUE_SHARE:
        problems.append(f"{unparsed} non-numeric price values")
    if negative:
        problems.append(f"{negative} negative price values")
    if empty.sum():
        warnings.append(f"{int(empty.sum())} empty price cells (ignored)")

    # Tracked-commodity share must not collapse.
    tracked_new = new["commodity"].isin(list(RAW_TO_CANONICAL))
    share_new = float(tracked_new.mean())
    stats["tracked_rows_new"] = int(tracked_new.sum())
    stats["tracked_share_new"] = round(share_new, 4)
    if tracked_new.sum() == 0:
        problems.append("no rows for tracked commodities")
    if have_prev:
        tracked_prev = prev["commodity"].isin(list(RAW_TO_CANONICAL))
        share_prev = float(tracked_prev.mean())
        stats["tracked_share_prev"] = round(share_prev, 4)
        if share_prev > 0 and share_new < MIN_TRACKED_SHARE_RATIO * share_prev:
            problems.append(
                f"tracked-commodity share collapsed: {share_prev:.1%} -> {share_new:.1%} "
                f"(must stay >= {MIN_TRACKED_SHARE_RATIO:.0%} of previous)")

    # Duplicates are reported; the panel collapses them with a median.
    dups = int(new.duplicated(subset=DUPLICATE_KEY).sum())
    stats["duplicate_key_rows"] = dups
    if dups:
        warnings.append(f"{dups} duplicate rows on {tuple(DUPLICATE_KEY)} (panel uses median price)")

    return _result(problems, warnings, stats)


def _result(problems, warnings, stats) -> dict:
    return {"ok": not problems, "problems": problems, "warnings": warnings, "stats": stats}
