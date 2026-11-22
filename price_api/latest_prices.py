"""Latest WFP price lookup for /micro-market (stdlib only, so it can be unit-tested without the model).

Reads artifacts/latest_prices.json, written by pipeline/latest_prices.py. A missing, corrupt or
wrong-schema file disables the feature: load_latest_prices returns (None, problem) and never raises.

A price is shown only when its month is at most MAX_MONTHS_OLD months before the file's data_through.
Nothing is interpolated or forecast here.
"""

import datetime as dt
import json
import math
import re
from typing import Dict, List, Optional, Tuple

MAX_MONTHS_OLD = 3
_PRICETYPE_PREFERENCE = ("Wholesale", "Retail")  # tie-break order when no pricetype is requested


def _valid_month(s) -> bool:
    if not isinstance(s, str) or len(s) != 7 or s[4] != "-":
        return False
    return s[:4].isdigit() and s[5:].isdigit() and 1 <= int(s[5:]) <= 12


def _valid_date(s) -> bool:
    if not isinstance(s, str):
        return False
    try:
        dt.date.fromisoformat(s)
    except ValueError:
        return False
    return True


def _month_index(s: str) -> int:
    return int(s[:4]) * 12 + int(s[5:7]) - 1


def _pricetype_rank(pricetype: str) -> Tuple[int, str]:
    if pricetype in _PRICETYPE_PREFERENCE:
        return (_PRICETYPE_PREFERENCE.index(pricetype), pricetype)
    return (len(_PRICETYPE_PREFERENCE), pricetype)


_KG_UNIT = re.compile(r"^\s*(\d+(?:\.\d+)?)?\s*KG\s*$", re.IGNORECASE)


def price_per_kg(price: float, unit: Optional[str]) -> Optional[float]:
    """Per-kg price for WFP units of the form 'KG' or '<n> KG' (e.g. a 64 KG crate). None for
    units with no fixed weight ('Head', 'Unit', 'Bunch'): those are never converted."""
    m = _KG_UNIT.match(unit or "")
    if not m:
        return None
    kg = float(m.group(1)) if m.group(1) else 1.0
    return round(price / kg, 2) if kg > 0 else None


def _no_price(note: str) -> Dict[str, object]:
    return {"estimated_price": None, "price_per_kg": None, "price_date": None, "price_unit": None,
            "price_pricetype": None, "price_note": note}


class LatestPrices:
    """Index of the latest WFP observation per (market, commodity) and pricetype."""

    def __init__(self, data_through: str, index: Dict[Tuple[str, str], List[dict]], skipped: int = 0):
        self.data_through = data_through
        self._index = index
        self.skipped_rows = skipped

    def estimate(self, market: str, commodity: str, pricetype: Optional[str] = None) -> Dict[str, object]:
        """Price fields for one market. estimated_price is None unless the observation is fresh enough.

        pricetype given: only that pricetype is considered.
        pricetype None: the latest observation across pricetypes; ties on date go to Wholesale,
        then Retail, then alphabetical order.
        """
        rows = self._index.get((market, commodity), [])
        scope = ""
        if pricetype:
            rows = [r for r in rows if r["pricetype"] == pricetype]
            scope = f" with pricetype {pricetype}"
        if not rows:
            return _no_price(f"no WFP observation for this market/commodity{scope}")

        latest_date = max(r["date"] for r in rows)
        best = min((r for r in rows if r["date"] == latest_date),
                   key=lambda r: _pricetype_rank(r["pricetype"]))
        obs_month = best["date"][:7]
        if _month_index(self.data_through) - _month_index(obs_month) > MAX_MONTHS_OLD:
            return _no_price(
                f"latest WFP observation is from {obs_month}, older than {MAX_MONTHS_OLD} months "
                f"before data through {self.data_through}")

        return {"estimated_price": best["price"], "price_per_kg": price_per_kg(best["price"], best["unit"]),
                "price_date": best["date"],
                "price_unit": best["unit"], "price_pricetype": best["pricetype"],
                "price_note": f"Latest WFP observation ({best['pricetype']}, {best['date']}); not a forecast."}


def parse_latest_prices(obj) -> Tuple[Optional[LatestPrices], Optional[str]]:
    """Validate a decoded latest_prices.json. Bad individual rows are skipped and counted."""
    if not isinstance(obj, dict):
        return None, "top level is not a JSON object"
    if obj.get("schema_version") != 1:
        return None, "schema_version must be 1"
    data_through = obj.get("data_through")
    if not _valid_month(data_through):
        return None, "data_through must be YYYY-MM"
    rows = obj.get("prices")
    if not isinstance(rows, list):
        return None, "prices must be a list"

    index: Dict[Tuple[str, str], List[dict]] = {}
    skipped = 0
    for r in rows:
        try:
            market, commodity, pricetype = r["market"], r["commodity"], r["pricetype"]
            price = float(r["price"])
            date = r["date"]
            if not (isinstance(market, str) and isinstance(commodity, str) and isinstance(pricetype, str)):
                raise ValueError("key fields must be strings")
            if not _valid_date(date) or not math.isfinite(price) or price <= 0:
                raise ValueError("bad date or price")
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        index.setdefault((market, commodity), []).append(
            {"pricetype": pricetype, "price": price, "unit": r.get("unit"), "date": date})
    return LatestPrices(data_through, index, skipped), None


def load_latest_prices(path: str) -> Tuple[Optional[LatestPrices], Optional[str]]:
    """Returns (store, None) on success, or (None, reason) when the feature must be disabled."""
    try:
        with open(path, encoding="utf-8") as f:
            obj = json.load(f)
    except FileNotFoundError:
        return None, f"file not found: {path}"
    except (OSError, ValueError) as e:
        return None, f"unreadable: {type(e).__name__}: {e}"
    try:
        return parse_latest_prices(obj)
    except Exception as e:  # noqa: BLE001 - the feature is optional; never crash the API on load
        return None, f"invalid: {type(e).__name__}: {e}"
