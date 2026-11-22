"""Champion selection, fallback forecasting, error bands and confidence.

Stdlib + typing only at import time (no sklearn / joblib / numpy), so this module
imports and unit-tests without the ML stack.

Contract: artifacts/champion.json (written by build_champion.py or the retraining
pipeline):
    schema_version   1 or 2 (2 adds the optional error_bands_by_pricetype)
    model_type       "naive_last" | "random_forest"
    model_file       null for naive_last; "random_forest_price_model.pkl" for random_forest
    created_at       ISO-8601 string with a UTC offset
    data_through     "YYYY-MM"
    backtest         {window_months, n, mape, naive_mape, median_ape}
    error_bands      {"<canonical commodity>": {n, ratio_p10, ratio_p50, ratio_p90}, ..., "_ALL": {...}}
    error_bands_by_pricetype (optional, schema 2)
                     {"<canonical commodity>": {"<pricetype>": {n, ratio_p10, ratio_p50, ratio_p90}}}
where ratio = actual / prediction, so the 80% range is [pred*ratio_p10, pred*ratio_p90].

load_champion() never raises. Any problem yields the built-in SAFE DEFAULT champion
(source="default") and the reason is kept in champion["load_problem"].
"""

from __future__ import annotations

import copy
import json
import math
import re
import threading
from datetime import datetime
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

SCHEMA_VERSION = 2                  # written by the pipeline for new champions
SUPPORTED_SCHEMA_VERSIONS = (1, 2)  # schema 1 files (no error_bands_by_pricetype) still load
ALLOWED_MODEL_TYPES = ("naive_last", "random_forest")
RF_MODEL_FILE = "random_forest_price_model.pkl"
ALL_KEY = "_ALL"
REQUIRED_KEYS = ("schema_version", "model_type", "model_file", "created_at",
                 "data_through", "backtest", "error_bands")
BACKTEST_KEYS = ("window_months", "n", "mape", "naive_mape", "median_ape")
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

# Used when the champion file is unusable. Deliberately wide: a naive forecast with
# a 0.75x to 1.35x range is conservative compared with the backtested bands.
DEFAULT_BAND: Dict[str, Any] = {"n": 0, "ratio_p10": 0.75, "ratio_p50": 1.0, "ratio_p90": 1.35}


def _default_champion(problem: Optional[str]) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "model_type": "naive_last",
        "model_file": None,
        "created_at": None,
        "data_through": None,
        "backtest": {"window_months": 0, "n": 0, "mape": None, "naive_mape": None, "median_ape": None},
        "error_bands": {ALL_KEY: dict(DEFAULT_BAND)},
        "error_bands_by_pricetype": {},
        "source": "default",
        "load_problem": problem,
    }


def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _reject_constant(name: str):
    raise ValueError(f"non-finite JSON constant {name} is not allowed")


def _validate_band(name: str, band: Any) -> Optional[str]:
    if not isinstance(band, dict):
        return f"band '{name}' is not an object"
    if not (_is_int(band.get("n")) and band["n"] >= 0):
        return f"band '{name}': n must be a non-negative integer"
    for key in ("ratio_p10", "ratio_p50", "ratio_p90"):
        v = band.get(key)
        if not _is_num(v) or v <= 0:
            return f"band '{name}': {key} must be a finite positive number"
    if not (band["ratio_p10"] <= band["ratio_p50"] <= band["ratio_p90"]):
        return f"band '{name}': require ratio_p10 <= ratio_p50 <= ratio_p90"
    return None


def _validate_bands_by_pricetype(by_pt: Any) -> Optional[str]:
    if not isinstance(by_pt, dict):
        return "error_bands_by_pricetype is not an object"
    for commodity, per_type in by_pt.items():
        if commodity == ALL_KEY:
            return f"error_bands_by_pricetype must not contain {ALL_KEY!r}"
        if not isinstance(per_type, dict):
            return f"error_bands_by_pricetype '{commodity}' is not an object"
        for pricetype, band in per_type.items():
            if not pricetype.strip():
                return f"error_bands_by_pricetype '{commodity}' has an empty pricetype key"
            problem = _validate_band(f"{commodity}/{pricetype}", band)
            if problem:
                return problem
    return None


def _validate_champion(raw: Any) -> Optional[str]:
    """Return None if valid, else a short human-readable reason."""
    if not isinstance(raw, dict):
        return "top-level JSON value is not an object"
    missing = [k for k in REQUIRED_KEYS if k not in raw]
    if missing:
        return f"missing keys: {', '.join(missing)}"
    if not (_is_int(raw["schema_version"]) and raw["schema_version"] in SUPPORTED_SCHEMA_VERSIONS):
        return (f"unsupported schema_version {raw['schema_version']!r} "
                f"(expected one of {list(SUPPORTED_SCHEMA_VERSIONS)})")

    model_type = raw["model_type"]
    if model_type not in ALLOWED_MODEL_TYPES:
        return f"model_type {model_type!r} not in {list(ALLOWED_MODEL_TYPES)}"
    model_file = raw["model_file"]
    if model_type == "naive_last" and model_file is not None:
        return "model_file must be null for naive_last"
    if model_type == "random_forest" and model_file != RF_MODEL_FILE:
        return f"model_file must be {RF_MODEL_FILE!r} for random_forest"

    created = raw["created_at"]
    if not isinstance(created, str):
        return "created_at must be a string"
    try:
        parsed = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError:
        return "created_at is not ISO-8601"
    if parsed.tzinfo is None:
        return "created_at must include a UTC offset"

    data_through = raw["data_through"]
    if not (isinstance(data_through, str) and _MONTH_RE.match(data_through)):
        return "data_through must be 'YYYY-MM'"

    backtest = raw["backtest"]
    if not isinstance(backtest, dict):
        return "backtest is not an object"
    for key in BACKTEST_KEYS:
        if key not in backtest:
            return f"backtest missing {key}"
    if not (_is_int(backtest["window_months"]) and backtest["window_months"] >= 0):
        return "backtest.window_months must be a non-negative integer"
    if not (_is_int(backtest["n"]) and backtest["n"] >= 0):
        return "backtest.n must be a non-negative integer"
    for key in ("mape", "naive_mape", "median_ape"):
        if not (_is_num(backtest[key]) and backtest[key] >= 0):
            return f"backtest.{key} must be a finite non-negative number"

    bands = raw["error_bands"]
    if not isinstance(bands, dict) or not bands:
        return "error_bands must be a non-empty object"
    if ALL_KEY not in bands:
        return f"error_bands must contain {ALL_KEY!r}"
    for name, band in bands.items():
        problem = _validate_band(name, band)
        if problem:
            return problem
    if "error_bands_by_pricetype" in raw:  # optional in schema 2; validated whenever present
        problem = _validate_bands_by_pricetype(raw["error_bands_by_pricetype"])
        if problem:
            return problem
    return None


def load_champion(path: Optional[str]) -> Dict[str, Any]:
    """Load and validate champion.json. Never raises.

    Returns a dict with the contract keys plus:
      source        "file" when loaded, "default" for the built-in safe champion
      load_problem  None when loaded, else the reason the default was used
    """
    if path is None:
        return _default_champion("no champion path configured")
    try:
        # utf-8-sig: tolerate a byte-order mark (Windows PowerShell writes one).
        with open(path, "r", encoding="utf-8-sig") as fh:
            raw = json.load(fh, parse_constant=_reject_constant)
    except FileNotFoundError:
        return _default_champion(f"champion file not found: {path}")
    except OSError as exc:
        return _default_champion(f"cannot read champion file: {type(exc).__name__}")
    except ValueError as exc:  # includes json.JSONDecodeError
        return _default_champion(f"invalid champion JSON: {exc}")
    except Exception as exc:  # defensive: the loader must never take the API down
        return _default_champion(f"unexpected error reading champion: {type(exc).__name__}")

    problem = _validate_champion(raw)
    if problem:
        return _default_champion(problem)
    champion = copy.deepcopy(raw)
    champion["source"] = "file"
    champion["load_problem"] = None
    return champion


def _find_key(mapping: Mapping[str, Any], name: str) -> Optional[str]:
    """Key of mapping matching name exactly, else case-insensitively. _ALL is never a match."""
    if name in mapping and name != ALL_KEY:
        return name
    for key in mapping:
        if key != ALL_KEY and key.casefold() == name.casefold():
            return key
    return None


def _check_price(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("previous_month_price must be a number")
    v = float(value)
    if not math.isfinite(v) or v <= 0:
        raise ValueError(f"previous_month_price must be a positive finite number, got {value!r}")
    return v


ModelLoader = Callable[[], Callable[[Dict[str, Any]], float]]


class Predictor:
    """Produces the main forecast, its interval and the source that produced it.

    model_loader: zero-argument callable that returns a function f(features: dict) -> float
    (the random forest). It is only called when the champion is random_forest. Its result
    is cached after the first success. Any exception it raises (or a bad value) falls back
    to the naive forecast and is reported in fallback_reason.
    """

    def __init__(self, champion: Mapping[str, Any], model_loader: Optional[ModelLoader] = None):
        self.champion: Dict[str, Any] = dict(champion)
        self._loader = model_loader
        self._rf_fn: Optional[Callable[[Dict[str, Any]], float]] = None
        self._lock = threading.Lock()

    def _get_rf(self) -> Callable[[Dict[str, Any]], float]:
        if self._loader is None:
            raise RuntimeError("no model loader configured")
        if self._rf_fn is None:
            with self._lock:
                if self._rf_fn is None:
                    self._rf_fn = self._loader()
        return self._rf_fn

    def _band(self, commodity: str, pricetype: Optional[str] = None) -> Tuple[float, float, float, str]:
        """Return (ratio_p10, ratio_p50, ratio_p90, band_source).

        Lookup order: (commodity, pricetype) -> commodity -> _ALL -> default. Names match
        case-insensitively. band_source names the level used, e.g. "Tomatoes/Wholesale".
        """
        if self.champion.get("source") == "default":
            return DEFAULT_BAND["ratio_p10"], DEFAULT_BAND["ratio_p50"], DEFAULT_BAND["ratio_p90"], "default"
        bands = self.champion.get("error_bands") or {}
        by_pt = self.champion.get("error_bands_by_pricetype") or {}
        if pricetype:
            ckey = _find_key(by_pt, commodity)
            pkey = _find_key(by_pt[ckey], pricetype) if ckey is not None else None
            if pkey is not None:
                band = by_pt[ckey][pkey]
                return band["ratio_p10"], band["ratio_p50"], band["ratio_p90"], f"{ckey}/{pkey}"
        source = _find_key(bands, commodity)
        if source is None and ALL_KEY in bands:
            source = ALL_KEY
        if source is None:
            return DEFAULT_BAND["ratio_p10"], DEFAULT_BAND["ratio_p50"], DEFAULT_BAND["ratio_p90"], "default"
        band = bands[source]
        return band["ratio_p10"], band["ratio_p50"], band["ratio_p90"], source

    def predict(self, features: Dict[str, Any], previous_month_price: float, commodity: str,
                pricetype: Optional[str] = None) -> Dict[str, Any]:
        """Return predicted_price, lower, upper, model_used, fallback_reason, band_source.

        Raises ValueError if previous_month_price is not a positive finite number, commodity
        is not a string, or pricetype is neither None nor a string.
        """
        price = _check_price(previous_month_price)
        if not isinstance(commodity, str):
            raise ValueError("commodity must be a string")
        if pricetype is not None and not isinstance(pricetype, str):
            raise ValueError("pricetype must be a string or None")

        predicted = price
        model_used = "naive_last"
        fallback_reason = None
        if self.champion.get("model_type") == "random_forest":
            try:
                fn = self._get_rf()
                value = float(fn(dict(features or {})))
                if not math.isfinite(value) or value <= 0:
                    raise ValueError("forecast was not a positive finite number")
                predicted = value
                model_used = "random_forest"
            except Exception as exc:
                fallback_reason = (
                    f"random_forest unavailable ({type(exc).__name__}); "
                    "naive forecast used"
                )

        p10, _, p90, band_source = self._band(commodity, pricetype)
        return {
            "predicted_price": predicted,
            "lower": predicted * p10,
            "upper": predicted * p90,
            "model_used": model_used,
            "fallback_reason": fallback_reason,
            "band_source": band_source,
        }


def confidence_from_band(lower: float, upper: float, predicted: float) -> int:
    """Confidence 0-100 from the relative width of the interval.

    rel_half = (upper - lower) / (2 * predicted)
    confidence = round(100 * (1 - rel_half)), clamped to [0, 100].

    Example: band [0.75, 1.35] x pred gives rel_half = 0.30 and confidence 70.
    Monotonic: a wider interval never increases confidence. Invalid input gives 0.
    """
    if not (_is_num(lower) and _is_num(upper) and _is_num(predicted)):
        return 0
    if predicted <= 0 or upper < lower:
        return 0
    rel_half = (upper - lower) / (2.0 * predicted)
    score = 100.0 * (1.0 - rel_half)
    return int(max(0, min(100, math.floor(score + 0.5))))
