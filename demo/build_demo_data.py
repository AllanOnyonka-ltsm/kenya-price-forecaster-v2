"""Build demo/site/forecasts.json for the static GitHub Pages demo.

Runs the real price_api code (the same champion model, error bands, units and plausibility check
that POST /predict uses) over every series in price_api/artifacts/latest_prices.json. Nothing is
invented: each forecast starts from a real WFP observation.

A series gets a forecast only when its latest observation is at most MAX_MONTHS_OLD months before
the data's data_through month, the same freshness rule /micro-market uses. Older series are listed
with their last observed price and no forecast.

Run from the repo root:  python demo/build_demo_data.py
"""

import datetime as dt
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_DIR = os.path.join(ROOT, "price_api")
OUT = os.path.join(ROOT, "demo", "site", "forecasts.json")


def _month_index(s: str) -> int:
    return int(s[:4]) * 12 + int(s[5:7]) - 1


def _next_month(s: str) -> str:
    i = _month_index(s) + 1
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def main() -> int:
    os.chdir(API_DIR)  # the API loads artifacts/ relative to price_api/
    sys.path.insert(0, API_DIR)
    import app as A  # noqa: E402
    from latest_prices import MAX_MONTHS_OLD, price_per_kg  # noqa: E402

    with open(A.LATEST_PRICES_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    data_through = raw["data_through"]
    target = _next_month(data_through)

    # canonical WFP name -> name the API accepts (first key in COMMODITY_META order)
    api_name = {}
    for key, (canonical, _, _) in A.COMMODITY_META.items():
        api_name.setdefault(canonical, key)

    rows, skipped = [], 0
    for p in raw["prices"]:
        canonical = p["commodity"]
        if canonical not in api_name:
            skipped += 1
            continue
        fresh = _month_index(data_through) - _month_index(p["date"][:7]) <= MAX_MONTHS_OLD
        row = {
            "market": p["market"], "commodity": canonical, "pricetype": p["pricetype"],
            "unit": p.get("unit"), "currency": p.get("currency", "KES"),
            "last_price": p["price"], "last_price_per_kg": price_per_kg(p["price"], p.get("unit")),
            "last_date": p["date"], "forecast": None,
        }
        if fresh:
            req = A.PredictRequest(date=f"{target}-15", admin1="", market=p["market"],
                                   commodity=api_name[canonical], pricetype=p["pricetype"],
                                   previous_month_price=p["price"])
            fc = A._forecast(req, canonical)
            unit, check = A.unit_and_plausibility(p["market"], canonical, p["pricetype"], fc["predicted"])
            row["forecast"] = {
                "month": target,
                "price": round(fc["predicted"], 2),
                "lower": round(fc["lower"], 2),
                "upper": round(fc["upper"], 2),
                "price_per_kg": price_per_kg(fc["predicted"], unit) if unit else None,
                "confidence_pct": fc["confidence"],
                "model_used": fc["model_used"],
                "band_source": fc["band_source"],
                "unreasonable": check["unreasonable"],
                "note": check["note"],
            }
        rows.append(row)

    rows.sort(key=lambda r: (r["commodity"], r["market"], r["pricetype"]))
    out = {
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "source": raw.get("source"),
        "data_through": data_through,
        "forecast_month": target,
        "max_months_old": MAX_MONTHS_OLD,
        "champion_model": A.CHAMPION.get("model_type") if isinstance(A.CHAMPION, dict) else None,
        "backtest": A.CHAMPION.get("backtest") if isinstance(A.CHAMPION, dict) else None,
        "n_series": len(rows),
        "n_forecasts": sum(1 for r in rows if r["forecast"]),
        "skipped_unsupported": skipped,
        "series": rows,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print(f"wrote {OUT}: {out['n_series']} series, {out['n_forecasts']} forecasts for {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
