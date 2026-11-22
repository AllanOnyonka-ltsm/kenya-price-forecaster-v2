"""Unit tests for pipeline/latest_prices.py (synthetic frames, no network).

Run from the repo root:  python -m unittest discover -s pipeline -t pipeline -v
"""
import json
import os
import sys
import tempfile
import unittest

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import latest_prices as LP  # noqa: E402

HEADER = ["date", "admin1", "admin2", "market", "market_id", "latitude", "longitude", "category", "commodity",
          "commodity_id", "unit", "priceflag", "pricetype", "currency", "price", "usdprice"]


def row(date="2026-10-15", market="Nairobi", commodity="Tomatoes", unit="64 KG", priceflag="actual",
        pricetype="Retail", currency="KES", price="100"):
    return {"date": date, "admin1": "Nairobi", "admin2": "", "market": market, "market_id": "1",
            "latitude": "-1.28", "longitude": "36.82", "category": "x", "commodity": commodity,
            "commodity_id": "1", "unit": unit, "priceflag": priceflag, "pricetype": pricetype,
            "currency": currency, "price": price, "usdprice": ""}


def frame(rows):
    return pd.DataFrame([{h: r[h] for h in HEADER} for r in rows], columns=HEADER)


def entry(payload, market, commodity, pricetype):
    hits = [p for p in payload["prices"]
            if (p["market"], p["commodity"], p["pricetype"]) == (market, commodity, pricetype)]
    assert len(hits) == 1, hits
    return hits[0]


class BuildLatestPricesTests(unittest.TestCase):
    def test_latest_observation_is_chosen(self):
        df = frame([row(date="2026-03-15", price="80"), row(date="2026-09-15", price="95"),
                    row(date="2026-06-15", price="88")])
        p = LP.build_latest_prices(df, generated_at="2026-11-22T00:00:00+00:00")
        e = entry(p, "Nairobi", "Tomatoes", "Retail")
        self.assertEqual(e["price"], 95.0)
        self.assertEqual(e["date"], "2026-09-15")
        self.assertEqual(e["unit"], "64 KG")
        self.assertEqual(e["currency"], "KES")

    def test_each_pricetype_keeps_its_own_latest_row(self):
        df = frame([row(pricetype="Retail", price="120", date="2026-10-15"),
                    row(pricetype="Wholesale", price="90", date="2026-08-15")])
        p = LP.build_latest_prices(df)
        self.assertEqual(entry(p, "Nairobi", "Tomatoes", "Retail")["price"], 120.0)
        self.assertEqual(entry(p, "Nairobi", "Tomatoes", "Wholesale")["price"], 90.0)
        self.assertEqual(entry(p, "Nairobi", "Tomatoes", "Wholesale")["date"], "2026-08-15")

    def test_tie_on_latest_date_takes_median(self):
        df = frame([row(price="10"), row(price="40"), row(price="20"), row(date="2026-01-15", price="999")])
        p = LP.build_latest_prices(df)
        e = entry(p, "Nairobi", "Tomatoes", "Retail")
        self.assertEqual(e["price"], 20.0)
        self.assertEqual(e["date"], "2026-10-15")

    def test_tie_with_mixed_units_uses_majority_unit_not_a_mixed_median(self):
        df = frame([row(unit="KG", price="50"), row(unit="KG", price="52"), row(unit="Bunch", price="400")])
        e = entry(LP.build_latest_prices(df), "Nairobi", "Tomatoes", "Retail")
        self.assertEqual(e["unit"], "KG")
        self.assertEqual(e["price"], 51.0)

    def test_aggregate_dropped_when_actual_present_on_same_date(self):
        df = frame([row(priceflag="aggregate", price="300"), row(priceflag="actual", price="100")])
        self.assertEqual(entry(LP.build_latest_prices(df), "Nairobi", "Tomatoes", "Retail")["price"], 100.0)

    def test_non_kes_and_untracked_commodities_are_not_kept(self):
        df = frame([row(currency="USD", price="1"), row(commodity="Maize", price="5"), row(price="77")])
        p = LP.build_latest_prices(df)
        self.assertEqual([(x["market"], x["commodity"]) for x in p["prices"]], [("Nairobi", "Tomatoes")])
        self.assertEqual(p["prices"][0]["price"], 77.0)

    def test_output_is_sorted_by_market_commodity_pricetype(self):
        df = frame([row(market="Nakuru", commodity="Kale", unit="KG", pricetype="Retail"),
                    row(market="Nairobi", commodity="Tomatoes", pricetype="Wholesale"),
                    row(market="Nairobi", commodity="Tomatoes", pricetype="Retail"),
                    row(market="Nairobi", commodity="Kale", unit="KG", pricetype="Retail")])
        keys = [(x["market"], x["commodity"], x["pricetype"]) for x in LP.build_latest_prices(df)["prices"]]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(keys[0], ("Nairobi", "Kale", "Retail"))

    def test_data_through_is_latest_month_in_whole_snapshot(self):
        # A row for an untracked commodity in 2026-11 still moves data_through forward.
        df = frame([row(date="2026-10-15"), row(commodity="Maize", date="2026-11-03", price="5")])
        p = LP.build_latest_prices(df)
        self.assertEqual(p["data_through"], "2026-11")
        self.assertEqual(p["n_entries"], 1)
        self.assertEqual(p["schema_version"], 1)
        self.assertEqual(p["source"], LP.SOURCE)
        self.assertIn("generated_at", p)

    def test_n_entries_matches_prices(self):
        df = frame([row(market="A"), row(market="B"), row(market="B", pricetype="Wholesale")])
        p = LP.build_latest_prices(df)
        self.assertEqual(p["n_entries"], 3)
        self.assertEqual(len(p["prices"]), 3)

    def test_empty_input_gives_empty_payload(self):
        for df in (pd.DataFrame(), frame([])):
            p = LP.build_latest_prices(df)
            self.assertEqual(p["prices"], [])
            self.assertEqual(p["n_entries"], 0)
            self.assertIsNone(p["data_through"])

    def test_missing_column_is_an_error(self):
        with self.assertRaises(ValueError):
            LP.build_latest_prices(pd.DataFrame({"date": ["2026-10-15"]}))

    def test_cli_writes_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "snap.csv")
            frame([row(price="64")]).to_csv(src, index=False)
            out = os.path.join(tmp, "nested", "latest_prices.json")
            self.assertEqual(LP.main(["--snapshot", src, "--out", out]), 0)
            with open(out, encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["n_entries"], 1)
            self.assertEqual(data["data_through"], "2026-10")


if __name__ == "__main__":
    unittest.main()
