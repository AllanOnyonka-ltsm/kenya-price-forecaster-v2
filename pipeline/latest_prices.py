#!/usr/bin/env python3
"""Latest WFP observation per (market, commodity, pricetype) for the nearby-market prices in /micro-market.

Input is the frame from features.read_wfp_csv (the same loader the retrain uses). Filters match
features.monthly_panel: KES rows, priceflag 'actual' or 'aggregate', commodities mapped through
RAW_TO_CANONICAL, valid date, price > 0.

Output (latest_prices.json) is consumed by price_api/latest_prices.py. Canonical commodity names are
the COMMODITY_META values in price_api/app.py, and market names are WFP's, the same as MARKET_COORDS.

CLI: python pipeline/latest_prices.py --snapshot data/wfp_snapshot.csv.gz --out price_api/artifacts/latest_prices.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pandas as pd  # noqa: E402

from features import ACCEPTED_FLAGS, RAW_TO_CANONICAL, read_wfp_csv  # noqa: E402

SCHEMA_VERSION = 1
SOURCE = "WFP VAM food prices (HDX), local snapshot"
LATEST_PRICES_FILE = "latest_prices.json"
KEYS = ["market", "commodity", "pricetype"]
COLUMNS_NEEDED = ["date", "market", "commodity", "pricetype", "unit", "priceflag", "currency", "price"]


def _normalised(raw: pd.DataFrame) -> pd.DataFrame:
    """Rows the API can use: KES, accepted flags, tracked commodities, valid date, price > 0.
    Within a (series, date) that has any 'actual' row, 'aggregate' rows are dropped (as in monthly_panel)."""
    d = raw.loc[raw["currency"].eq("KES") & raw["priceflag"].isin(ACCEPTED_FLAGS)].copy()
    d["commodity"] = d["commodity"].map(RAW_TO_CANONICAL)
    d = d[d["commodity"].notna()].copy()
    d["date"] = pd.to_datetime(d["date"], format="%Y-%m-%d", errors="coerce")
    d["price"] = pd.to_numeric(d["price"], errors="coerce")
    d = d[d["date"].notna() & d["price"].gt(0)].copy()
    d["_actual"] = d["priceflag"].eq("actual")
    has_actual = d.groupby(KEYS + ["date"])["_actual"].transform("any")
    return d[d["_actual"] | ~has_actual].copy()


def build_latest_prices(df: pd.DataFrame, generated_at: str | None = None) -> dict:
    """Latest observation per (market, commodity, pricetype). See module docstring for the rules.

    Ties: when several rows share the latest date for a key, the median price is used. If those rows
    carry more than one unit, only the unit with the most rows is kept (ties: alphabetical), so
    prices in different units are never medianed together. The unit is reported with the price.
    """
    generated_at = generated_at or dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    if df is None or df.empty:
        months = []
        d = pd.DataFrame(columns=COLUMNS_NEEDED)
    else:
        missing = [c for c in COLUMNS_NEEDED if c not in df.columns]
        if missing:
            raise ValueError(f"snapshot is missing columns: {missing}")
        months = pd.to_datetime(df["date"], format="%Y-%m-%d", errors="coerce").dropna()
        d = _normalised(df)

    prices = []
    if not d.empty:
        for (market, commodity, pricetype), g in d.groupby(KEYS, sort=True):
            last = g["date"].max()
            g = g[g["date"].eq(last)]
            units = sorted(g["unit"].unique(), key=lambda u: (-int(g["unit"].eq(u).sum()), u))
            g = g[g["unit"].eq(units[0])]
            prices.append({
                "market": market,
                "commodity": commodity,
                "pricetype": pricetype,
                "price": float(g["price"].median()),
                "unit": units[0],
                "currency": str(g["currency"].iloc[0]),
                "date": last.strftime("%Y-%m-%d"),
            })

    # data_through is the latest month in the whole snapshot, not only the rows kept above.
    data_through = None
    if len(months):
        latest = months.max()
        data_through = f"{latest.year}-{latest.month:02d}"

    return {
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE,
        "generated_at": generated_at,
        "data_through": data_through,
        "n_entries": len(prices),
        "prices": prices,
    }


def write_latest_prices(out_path: str, payload: dict) -> None:
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    tmp = out_path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    os.replace(tmp, out_path)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Write the latest WFP price per market/commodity/pricetype.")
    p.add_argument("--snapshot", required=True, help="WFP snapshot CSV (.csv or .csv.gz)")
    p.add_argument("--out", required=True, help="output JSON path, e.g. price_api/artifacts/latest_prices.json")
    args = p.parse_args(argv)
    payload = build_latest_prices(read_wfp_csv(args.snapshot))
    write_latest_prices(args.out, payload)
    print(f"[latest_prices] wrote {args.out}: n_entries={payload['n_entries']} "
          f"data_through={payload['data_through']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
