from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Optional, Any
from datetime import datetime
import joblib
import math
import numpy as np
import pandas as pd
import uuid
import os
from dotenv import load_dotenv
from enhanced_endpoints import (
    get_recommendations as _enhanced_recommendations,
    RecommendationRequest as EnhancedRecommendationRequest,
    RecommendationResponse as EnhancedRecommendationResponse,
)
from intent_router import parse_intent, build_response
from feature_importance import group_feature_importances, impact_label
from geo import apply_coord_overrides, markets_within_radius
from latest_prices import load_latest_prices, plausibility, price_per_kg
import feedback_store
from predictor import load_champion, Predictor, confidence_from_band
import threading

API_VERSION = "2.0.0"

# =========================
# LOAD ARTIFACTS (lazy; a missing or corrupt file never crashes the import)
# =========================
MODEL_PATH      = "artifacts/random_forest_price_model.pkl"
ENCODER_PATH    = "artifacts/label_encoders.pkl"
FEATURES_PATH   = "artifacts/feature_columns.pkl"
PREPROCESS_PATH = "artifacts/preprocessing_info.pkl"
CHAMPION_PATH   = "artifacts/champion.json"

class _LazyArtifact:
    """Loads on first use. A failure is stored in .problem and not retried."""
    def __init__(self, loader):
        self._loader = loader
        self._lock   = threading.Lock()
        self._done   = False
        self.value   = None
        self.problem = None

    def get(self):
        if not self._done:
            with self._lock:
                if not self._done:
                    try:
                        self.value = self._loader()
                    except Exception as exc:
                        self.problem = f"{type(exc).__name__}: {exc}"
                    self._done = True
        return self.value

def _load_encoders() -> dict:
    return {
        "label_encoders":  joblib.load(ENCODER_PATH),
        "feature_columns": joblib.load(FEATURES_PATH),
        "preprocess_info": joblib.load(PREPROCESS_PATH),
    }

def _load_rf() -> dict:
    enc = _ENCODERS.get()
    if enc is None:
        raise RuntimeError(f"encoders unavailable ({_ENCODERS.problem})")
    model = joblib.load(MODEL_PATH)
    # Grouped from the real model's importances; used only by /explainability.
    factors = group_feature_importances(model.feature_importances_, enc["feature_columns"])
    return {"model": model, "factor_importances": factors}

_ENCODERS = _LazyArtifact(_load_encoders)
_RF       = _LazyArtifact(_load_rf)

def _encoders() -> dict:
    enc = _ENCODERS.get()
    if enc is None:
        raise RuntimeError(f"label encoders unavailable ({_ENCODERS.problem})")
    return enc

def _factor_importances() -> list:
    """Factor importances from the RF, or [] when the RF is not loaded."""
    bundle = _RF.get()
    return bundle["factor_importances"] if bundle is not None else []

# Champion (naive_last by default). Never raises; problems are reported in /health.
CHAMPION = load_champion(CHAMPION_PATH)

# Latest WFP price per market/commodity/pricetype (for /micro-market). A missing or bad file
# disables the feature (LATEST_PRICES is None); it never stops the API from starting.
LATEST_PRICES_PATH = os.environ.get("LATEST_PRICES_PATH", "artifacts/latest_prices.json")
LATEST_PRICES, LATEST_PRICES_PROBLEM = load_latest_prices(LATEST_PRICES_PATH)

def _load_rf_predictor():
    """Model loader for Predictor: returns f(features) -> RF mean forecast."""
    bundle = _RF.get()
    if bundle is None:
        raise RuntimeError("random forest artifacts not loaded")
    rf_model = bundle["model"]

    def _predict(features: dict) -> float:
        req = PredictRequest(**features)
        X, _ = build_feature_vector(req)
        mean, _, _, _ = rf_confidence(rf_model, X)
        return float(mean)

    return _predict

PREDICTOR = Predictor(CHAMPION, model_loader=_load_rf_predictor)

# =========================
# BUSINESS RULES (LOCKED)
# =========================
# The old CROP_THRESHOLDS dict compared prices (KES) with numbers that were really WFP pack sizes in
# kg (64 for a tomato crate, 126 for a cabbage bag). /predict now checks plausibility against
# observed WFP prices instead (latest_prices.plausibility).
ALLOWED_COMMODITIES = {"cabbage", "kale", "onion", "onions", "potatoes", "tomatoes", "bananas"}
DEFAULT_MICRO_MARKET_RADIUS_KM = 50.0

# =========================
# MARKET COORDINATES LOOKUP
# =========================
# Coordinates from WFP's Kenya food price dataset (the model's training data).
MARKET_COORDS: dict[str, tuple[float, float]] = {
    "Dagahaley (Daadab)":              (0.19, 40.29),
    "Dandora (Nairobi)":               (-1.25, 36.90),
    "Eldoret town (Uasin Gishu)":      (0.52, 35.28),
    "Ethiopia (Kakuma)":               (3.72, 34.85),
    "Garissa town (Garissa)":          (-0.46, 39.64),
    "Hagadera (Daadab)":               (0.01, 40.37),
    "HongKong (Kakuma)":               (3.75, 34.84),
    "IFO (Daadab)":                    (0.11, 40.32),
    "Illbissil Food Market (Kajiado)": (-2.10, 36.79),
    "Kaanwa (Tharaka Nithi)":          (-0.32, 37.72),
    "Kakuma 2":                        (3.75, 34.82),
    "Kakuma 3":                        (3.77, 34.82),
    "Kakuma 4":                        (3.74, 34.81),
    "Kalahari (Mombasa)":              (-4.03, 39.63),
    "Kalobeyei (Village 1)":           (3.77, 34.73),
    "Kalobeyei (Village 2)":           (3.76, 34.75),
    "Kalobeyei (Village 3)":           (3.75, 34.76),
    "Kangemi (Nairobi)":               (-1.27, 36.74),
    "Karatina (Nyeri)":                (-0.48, 37.13),
    "Kathonzweni (Makueni)":           (-1.91, 37.73),
    "Kawangware (Nairobi)":            (-1.28, 36.74),
    "Kibra (Nairobi)":                 (-1.32, 36.78),
    "Kibuye (Kisumu)":                 (-0.09, 34.77),
    "Kisumu":                          (-0.10, 34.75),
    "Kitengela (Kajiado)":             (-1.29, 36.86),
    "Kitui":                           (-1.37, 38.02),
    "Kongowea (Mombasa)":              (-4.06, 39.66),
    "Lodwar town":                     (3.12, 35.60),
    "Lomut (West Pokot)":              (1.44, 35.57),
    "Makutano (West Pokot)":           (1.25, 35.08),
    "Marigat town (Baringo)":          (0.47, 35.98),
    "Mathare (Nairobi)":               (-1.26, 36.86),
    "Mogadishu (Kakuma)":              (3.72, 34.85),
    "Mukuru (Nairobi)":                (-1.30, 36.88),
    "Nairobi":                         (-1.28, 36.82),
    "Nakuru":                          (-0.28, 36.07),
    "Takaba (Mandera)":                (3.39, 40.23),
    "Tala Centre Market (Machakos)":   (-1.27, 37.32),
    "Wakulima (Nairobi)":              (-1.29, 36.83),
    "Wakulima (Nakuru)":               (-0.30, 36.08),
}

# Manual corrections to WFP's coordinates. EMPTY on purpose: add an entry only with a source
# you have checked (survey, official gazetteer, OpenStreetMap node, ...). Example shape:
#   "Some market (County)": {"lat": -1.23, "lon": 36.45, "source": "<URL or reference>",
#                            "note": "why WFP's value is wrong"},
# Suspected problems (unverified, so NOT applied): "Kitengela (Kajiado)" is listed by WFP at
# (-1.29, 36.86), essentially Nairobi, although Kitengela town is roughly 30 km south.
# Overrides also change the lat/lon features the legacy RandomForest sees in /predict.
MARKET_COORD_OVERRIDES: dict[str, dict] = {}
MARKET_COORDS = apply_coord_overrides(MARKET_COORDS, MARKET_COORD_OVERRIDES)

# =========================
# COMMODITY METADATA
# =========================
# Maps user-facing name → (canonical model name, unit string, category)
COMMODITY_META: dict[str, tuple[str, str, str]] = {
    "tomatoes": ("Tomatoes",         "64 KG",  "vegetables and fruits"),
    "tomato":   ("Tomatoes",         "64 KG",  "vegetables and fruits"),
    "onions":   ("Onions (dry)",     "13 KG",  "vegetables and fruits"),
    "onion":    ("Onions (dry)",     "13 KG",  "vegetables and fruits"),
    "potatoes": ("Potatoes (Irish)", "50 KG",  "cereals and tubers"),
    "potato":   ("Potatoes (Irish)", "50 KG",  "cereals and tubers"),
    "kale":     ("Kale",             "50 KG",  "vegetables and fruits"),
    "cabbage":  ("Cabbage",          "126 KG", "vegetables and fruits"),
    "bananas":  ("Bananas",          "Unit",   "cereals and tubers"),
    "banana":   ("Bananas",          "Unit",   "cereals and tubers"),
}

# =========================
# API SCHEMAS
# =========================
class PredictRequest(BaseModel):
    # Original required fields — unchanged, backward compatible
    date:                 str
    admin1:               str
    market:               str
    commodity:            str
    pricetype:            str
    previous_month_price: float

    # Optional price history — provide for better accuracy.
    # When omitted, estimated from previous_month_price (confidence is degraded).
    price_3_months_ago: Optional[float] = None   # price_lag_3
    price_6_months_ago: Optional[float] = None   # price_lag_6
    price_ma_3:         Optional[float] = None   # 3-month moving average (most important)
    price_ma_6:         Optional[float] = None   # 6-month moving average
    price_vol_6:        Optional[float] = None   # 6-month price std dev

class PredictResponse(BaseModel):
    commodity:            str
    market:               str
    date:                 str
    prediction_per_kg:    float
    unit:                 str
    market_type:          str
    previous_month_price: float
    confidence_pct:       float
    error_margin:         str
    lower_bound:          float
    upper_bound:          float
    unreasonable:         bool
    note:                 str
    # Added in 2.0.0 (optional, additive)
    model_used:           Optional[str] = None   # "naive_last" | "random_forest"
    fallback_reason:      Optional[str] = None   # None, or why the champion model was not used
    # Added (optional, additive): which error band was used, e.g. "Tomatoes/Wholesale", "Tomatoes", "_ALL"
    band_source:          Optional[str] = None
    # Added (optional, additive). prediction_per_kg keeps its legacy name but is in `unit`, the WFP
    # unit of the series (e.g. "64 KG" for wholesale tomatoes). price_per_kg is the per-kg value
    # when the unit has a fixed weight, else None. plausible_* is the range used for `unreasonable`.
    price_per_kg:         Optional[float] = None
    plausible_min:        Optional[float] = None
    plausible_max:        Optional[float] = None

class RecommendationRequest(BaseModel):
    commodity:       str
    market:          str
    admin1:          str
    predicted_price: float
    previous_price:  float
    pricetype:       str

class RecommendationResponse(BaseModel):
    commodity:       str
    market:          str
    recommendations: List[str]
    action_type:     str
    confidence:      str
    rationale:       str

class MicroMarketRequest(BaseModel):
    commodity: str
    region:    str
    radius_km: Optional[float] = DEFAULT_MICRO_MARKET_RADIUS_KM
    date:      str
    # Added (optional, additive): restrict the nearby price to one pricetype (Retail or Wholesale).
    pricetype: Optional[str] = None

class MicroMarketResponse(BaseModel):
    commodity:          str
    region:             str
    nearby_markets:     List[Dict[str, Any]]
    localized_forecast: Dict[str, float]
    recommended_market: str
    market_comparison:  str
    # Added (optional, additive): where the market coordinates come from.
    data_source:        Optional[str] = None
    # Added (optional, additive): month the nearby WFP prices run through (null when price data is off).
    price_data_through: Optional[str] = None

class FormatRequest(BaseModel):
    prediction_data: Dict
    format_type:     str
    language:        Optional[str] = "english"

class FormatResponse(BaseModel):
    format_type:       str
    formatted_message: str
    character_count:   int
    estimated_cost:    Optional[float] = None

class ExplainabilityRequest(BaseModel):
    # Same fields as PredictRequest so we can re-run the feature vector
    date:                 str
    admin1:               str
    market:               str
    commodity:            str
    pricetype:            str
    previous_month_price: float
    price_3_months_ago:   Optional[float] = None
    price_6_months_ago:   Optional[float] = None
    price_ma_3:           Optional[float] = None
    price_ma_6:           Optional[float] = None
    price_vol_6:          Optional[float] = None

class ExplainabilityResponse(BaseModel):
    commodity:                str
    market:                   str
    predicted_price:          float
    top_influencing_factors:  List[Dict[str, Any]]
    explanation_summary:      str
    confidence_factors:       Dict[str, Any]
    # Added in 2.0.0 (optional, additive)
    model_used:               Optional[str] = None
    fallback_reason:          Optional[str] = None

class FeedbackRequest(BaseModel):
    user_id:          Optional[str] = None
    prediction_id:    Optional[str] = None
    predicted_price:  Optional[float] = None   # what the API predicted; enables MAPE in /impact-stats
    actual_price:     Optional[float] = None
    accuracy_rating:  Optional[int] = None
    usefulness_rating: Optional[int] = None
    comments:         Optional[str] = None
    timestamp:        Optional[str] = None

class FeedbackResponse(BaseModel):
    feedback_id: str
    status:      str
    message:     str
    timestamp:   str

class WebhookBody(BaseModel):
    from_number: str
    body:        str

class ImpactStatsResponse(BaseModel):
    # None = no supporting data (never a made-up number).
    total_predictions:    Optional[int] = None    # predictions are not logged -> always None
    total_users:          int                     # distinct user_id values in feedback
    average_accuracy:     Optional[float] = None  # 1 - MAPE (0..1) from feedback with predicted+actual price
    total_markets_covered: int
    commodities_tracked:  List[str]
    user_satisfaction:    Optional[float] = None  # mean usefulness_rating (1-5)
    cost_savings_estimate: Optional[float] = None # not measurable -> always None
    last_updated:         str
    # Additional real metrics
    feedback_count:           int = 0
    average_accuracy_rating:  Optional[float] = None  # mean accuracy_rating (1-5)
    mape_pct:                 Optional[float] = None
    price_comparisons:        int = 0                 # feedback rows used for MAPE

# =========================
# INIT APP
# =========================
load_dotenv()  # Load GEMINI_API_KEY and other env vars from .env

app = FastAPI(title="CropEx Price Prediction API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# HELPER: RESOLVE MARKET
# =========================
def _resolve_market(name: str) -> str:
    """Exact match first, then partial. Raises 400 if unknown."""
    s = name.strip()
    if s in MARKET_COORDS:
        return s
    matches = [k for k in MARKET_COORDS if s.lower() in k.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        nairobi = [m for m in matches if "Nairobi" in m]
        return nairobi[0] if nairobi else matches[0]
    raise HTTPException(
        status_code=400,
        detail=f"Unknown market '{name}'. Valid markets: {sorted(MARKET_COORDS.keys())}"
    )

# =========================
# HELPER: BUILD FEATURE VECTOR
# =========================
def build_feature_vector(req: PredictRequest):
    """
    Builds the full 19-feature vector the model was trained on.
    Missing price history is estimated from previous_month_price.
    Returns (X, estimated_fields) so confidence can be adjusted.
    """
    p         = req.previous_month_price
    estimated = []

    # Price history — use provided values or estimate
    lag_1 = p
    lag_3 = req.price_3_months_ago if req.price_3_months_ago is not None else (estimated.append("price_lag_3") or p)
    lag_6 = req.price_6_months_ago if req.price_6_months_ago is not None else (estimated.append("price_lag_6") or p)
    ma_3  = req.price_ma_3         if req.price_ma_3         is not None else (estimated.append("price_ma_3")  or p)
    ma_6  = req.price_ma_6         if req.price_ma_6         is not None else (estimated.append("price_ma_6")  or p)
    vol_6 = req.price_vol_6        if req.price_vol_6        is not None else (estimated.append("price_vol_6") or 0.0)

    # Date features
    try:
        dt      = datetime.strptime(req.date, "%Y-%m-%d")
        year    = dt.year
        month   = dt.month
        quarter = (month - 1) // 3 + 1
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date '{req.date}'. Use YYYY-MM-DD.")

    sin_month = math.sin(2 * math.pi * month / 12)
    cos_month = math.cos(2 * math.pi * month / 12)

    # Market coords
    market_key = _resolve_market(req.market)
    lat, lon   = MARKET_COORDS[market_key]

    # Commodity metadata
    norm = req.commodity.strip().lower()
    if norm not in COMMODITY_META:
        raise HTTPException(
            status_code=400,
            detail=f"Commodity '{req.commodity}' not supported. Allowed: {list(COMMODITY_META.keys())}"
        )
    canonical, unit_str, category_str = COMMODITY_META[norm]

    # Label encoding
    def encode(col: str, val: str) -> int:
        enc = _encoders()["label_encoders"][col]
        try:
            return int(enc.transform([val])[0])
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid {col}: '{val}'. Valid: {list(enc.classes_)}"
            )

    pricetype_norm = req.pricetype.strip().capitalize()

    # Assemble in exact training order (confirmed from inspect_model.py):
    # [00]admin1 [01]market [02]latitude [03]longitude [04]category
    # [05]commodity [06]unit [07]pricetype [08]year [09]month
    # [10]quarter [11]sin_month [12]cos_month [13]price_lag_1
    # [14]price_lag_3 [15]price_lag_6 [16]price_ma_3 [17]price_ma_6 [18]price_vol_6
    vector = [
        encode("admin1",    req.admin1.strip().title()),
        encode("market",    market_key),
        lat,
        lon,
        encode("category",  category_str),
        encode("commodity", canonical),
        encode("unit",      unit_str),
        encode("pricetype", pricetype_norm),
        year,
        month,
        quarter,
        sin_month,
        cos_month,
        lag_1,
        lag_3,
        lag_6,
        ma_3,
        ma_6,
        vol_6,
    ]

    return np.array(vector, dtype=float).reshape(1, -1), estimated

# =========================
# HELPER: CONFIDENCE ADJUSTMENT
# =========================
def adjusted_confidence(base_conf: float, estimated_fields: list) -> tuple[float, str]:
    """
    Degrade confidence when price history fields were estimated.
    price_ma_3 missing is most damaging — it drives 90% of model signal.
    """
    penalty = 0.0
    notes   = []

    if "price_ma_3"  in estimated_fields: penalty += 25.0; notes.append("3-month avg estimated")
    if "price_ma_6"  in estimated_fields: penalty +=  5.0
    if "price_lag_3" in estimated_fields: penalty +=  5.0; notes.append("3-month lag estimated")
    if "price_lag_6" in estimated_fields: penalty +=  3.0
    if "price_vol_6" in estimated_fields: penalty +=  2.0

    adj  = max(base_conf - penalty, 40.0)
    note = ""
    if notes:
        note = (
            f"Confidence adjusted to {adj:.0f}% — "
            f"{', '.join(notes)} from previous_month_price. "
            f"Pass price history fields for a more accurate forecast."
        )
    return adj, note

# =========================
# HELPER: RF CONFIDENCE
# =========================
def rf_confidence(model, X):
    tree_preds = np.array([tree.predict(X)[0] for tree in model.estimators_])
    mean = tree_preds.mean()
    low  = np.percentile(tree_preds, 5)
    high = np.percentile(tree_preds, 95)
    return mean, low, high, 0.90

# =========================
# ROOT
# =========================
@app.get("/")
def root():
    return {
        "message": "CropEx — Kenyan Agro Market Price Prediction API",
        "endpoints": {
            "/predict":       "POST - Price prediction",
            "/recommendations": "POST - Actionable sell/hold recommendations",
            "/micro-market":  "POST - Localized market forecasting",
            "/format":        "POST - Format for SMS / WhatsApp / bulletin",
            "/explainability": "POST - AI prediction explanations",
            "/feedback":      "POST - Submit feedback on predictions",
            "/impact-stats":  "GET  - Aggregated impact statistics",
            "/health":        "GET  - Service and champion model status",
        },
        "example_request": {
            "date": "2025-12-05",
            "admin1": "Nairobi",
            "market": "Wakulima (Nairobi)",
            "commodity": "tomatoes",
            "pricetype": "retail",
            "previous_month_price": 58.2
        },
        "supported_commodities": sorted(COMMODITY_META.keys()),
        "supported_markets":     sorted(MARKET_COORDS.keys()),
    }

# =========================
# WEBHOOK (WhatsApp / CropEx)
# =========================
@app.post("/webhook")
async def webhook(payload: WebhookBody):
    """
    WhatsApp agricultural trading platform webhook.
    Intelligently routes intent to the C++ engine.
    
    Input: { "from_number": str, "body": str }
    Output: { "intent": str, "symbol": str|null, "quantity": float|null, "unit": str|null, "sms": str|null }
    """
    parsed   = await parse_intent(payload.body)
    response = build_response(parsed)
    return response

# =========================
# PREDICT
# =========================
@app.get("/predict")
def predict_info():
    return {
        "error":   "Method Not Allowed",
        "message": "Use POST /predict",
        "docs":    "/docs",
    }

def _estimated_fields(req) -> list:
    """Price-history fields the caller did not supply (same names as before)."""
    estimated = []
    if req.price_3_months_ago is None: estimated.append("price_lag_3")
    if req.price_6_months_ago is None: estimated.append("price_lag_6")
    if req.price_ma_3         is None: estimated.append("price_ma_3")
    if req.price_ma_6         is None: estimated.append("price_ma_6")
    if req.price_vol_6        is None: estimated.append("price_vol_6")
    return estimated

def _check_inputs(req) -> None:
    """Same 400 errors as before. Encoder checks run only when the encoders load,
    because the naive champion does not need them."""
    norm = req.commodity.strip().lower()
    if norm not in COMMODITY_META:
        raise HTTPException(
            status_code=400,
            detail=f"Commodity '{req.commodity}' not supported. Allowed: {sorted(COMMODITY_META.keys())}"
        )
    try:
        _encoders()
    except RuntimeError:
        try:
            datetime.strptime(req.date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid date '{req.date}'. Use YYYY-MM-DD.")
        _resolve_market(req.market)
        return
    build_feature_vector(req)

def _forecast(req, canonical: str) -> dict:
    """Run the Predictor and attach the confidence. Used by /predict and /explainability."""
    try:
        pricetype = (getattr(req, "pricetype", None) or "").strip() or None
        out = PREDICTOR.predict(req.model_dump(), req.previous_month_price, canonical, pricetype)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    pred, lo, hi = out["predicted_price"], out["lower"], out["upper"]
    estimated    = _estimated_fields(req)
    conf         = float(confidence_from_band(lo, hi, pred))
    conf_note    = ""
    if out["model_used"] == "random_forest":
        # The RF uses the price-history fields, so the old penalty still applies to it.
        # The penalty can only lower confidence, never raise it.
        penalised, conf_note = adjusted_confidence(conf, estimated)
        conf = min(conf, penalised)
    return {
        "predicted":       pred,
        "lower":           lo,
        "upper":           hi,
        "confidence":      conf,
        "conf_note":       conf_note,
        "estimated":       estimated,
        "model_used":      out["model_used"],
        "fallback_reason": out["fallback_reason"],
        "band_source":     out["band_source"],
    }

def unit_and_plausibility(market: str, canonical: str, pricetype: str, pred: float):
    """(unit, plausibility dict) for one forecast, from artifacts/latest_prices.json.
    Used by /predict and by demo/build_demo_data.py, so both give the same answer."""
    unit, ref = None, None
    if LATEST_PRICES is not None:
        unit = LATEST_PRICES.series_unit(market, canonical, pricetype)
        if unit:
            ref = LATEST_PRICES.reference_range(canonical, pricetype, unit)
    return unit, plausibility(pred, ref, unit)

@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest):
    norm = req.commodity.strip().lower()
    _check_inputs(req)
    canonical = COMMODITY_META[norm][0]
    fc = _forecast(req, canonical)

    pred         = fc["predicted"]
    lo, hi       = fc["lower"], fc["upper"]
    unit, check  = unit_and_plausibility(req.market, canonical, req.pricetype, pred)
    unreasonable = check["unreasonable"]
    note = check["note"] if unreasonable else (fc["conf_note"] or check["note"])

    return {
        "commodity":            req.commodity,
        "market":               req.market,
        "date":                 req.date,
        "prediction_per_kg":    round(pred, 2),
        "unit":                 unit or "unknown (same unit as previous_month_price)",
        "price_per_kg":         price_per_kg(pred, unit) if unit else None,
        "plausible_min":        check["plausible_min"],
        "plausible_max":        check["plausible_max"],
        "market_type":          req.pricetype,
        "previous_month_price": req.previous_month_price,
        "confidence_pct":       fc["confidence"],
        "error_margin":         f"+-{round(max(hi - pred, pred - lo), 2)}",
        "lower_bound":          round(lo, 2),
        "upper_bound":          round(hi, 2),
        "unreasonable":         unreasonable,
        "note":                 note,
        "model_used":           fc["model_used"],
        "fallback_reason":      fc["fallback_reason"],
        "band_source":          fc["band_source"],
    }

# =========================
# RECOMMENDATIONS
# =========================
# =========================
# RECOMMENDATIONS
# =========================
@app.post("/recommendations", response_model=EnhancedRecommendationResponse)
async def get_recommendations_endpoint(req: EnhancedRecommendationRequest):
    return await _enhanced_recommendations(req, ALLOWED_COMMODITIES)
# =========================
# MICRO-MARKET
# =========================
MICRO_MARKET_MAX_RESULTS = 5
MICRO_MARKET_DATA_SOURCE = ("WFP market coordinates" if not MARKET_COORD_OVERRIDES else
                            f"WFP market coordinates ({len(MARKET_COORD_OVERRIDES)} manually corrected)")
MICRO_MARKET_PRICE_NOTE  = "No recent price for this market in the API; check the market or WFP data."

@app.post("/micro-market", response_model=MicroMarketResponse)
def get_micro_market_forecast(req: MicroMarketRequest):
    """Real nearby markets (WFP coordinates). Each market gets the latest WFP observation for the
    commodity if it is at most 3 months older than the price data; otherwise estimated_price is
    null and price_note says why. Prices are never interpolated or forecast here."""
    commodity_normalized = req.commodity.strip().lower()
    if commodity_normalized not in ALLOWED_COMMODITIES:
        raise HTTPException(status_code=400,
            detail=f"Commodity '{req.commodity}' not supported.")

    origin = _resolve_market(req.region)   # 400 when the region is not a known market
    radius = req.radius_km if req.radius_km is not None else DEFAULT_MICRO_MARKET_RADIUS_KM
    try:
        found = markets_within_radius(origin, MARKET_COORDS, radius, limit=MICRO_MARKET_MAX_RESULTS)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    canonical = COMMODITY_META[commodity_normalized][0]
    pricetype = req.pricetype.strip().capitalize() if req.pricetype and req.pricetype.strip() else None
    nearby_markets = []
    for m in found:
        if LATEST_PRICES is None:
            price = {"estimated_price": None, "price_per_kg": None, "price_date": None, "price_unit": None,
                     "price_pricetype": None, "price_note": MICRO_MARKET_PRICE_NOTE}
        else:
            price = LATEST_PRICES.estimate(m["market"], canonical, pricetype)
        nearby_markets.append({"market_name": m["market"], "distance_km": m["distance_km"],
                               "market_type": None, **price})

    priced = sum(1 for m in nearby_markets if m["estimated_price"] is not None)
    if not nearby_markets:
        comparison = f"No markets within {radius:g} km of {origin}."
    elif LATEST_PRICES is None:
        comparison = (
            f"Found {len(nearby_markets)} market(s) within {radius:g} km of {origin}. "
            "No prices for these markets are available in this API, so they are not compared."
        )
    elif priced:
        comparison = (
            f"Found {len(nearby_markets)} market(s) within {radius:g} km of {origin}. "
            f"Latest WFP prices are shown for {priced} of them (see price_date). "
            "They are not compared or forecast."
        )
    else:
        comparison = (
            f"Found {len(nearby_markets)} market(s) within {radius:g} km of {origin}. "
            f"None has a recent WFP price for {req.commodity} (see price_note)."
        )

    return {
        "commodity": req.commodity,
        "region":    req.region,
        "nearby_markets": nearby_markets,
        "localized_forecast": {},
        # Nearest market by distance, not a price-based recommendation.
        "recommended_market": nearby_markets[0]["market_name"] if nearby_markets else "",
        "market_comparison":  comparison,
        "data_source":        MICRO_MARKET_DATA_SOURCE,
        "price_data_through": LATEST_PRICES.data_through if LATEST_PRICES is not None else None,
    }

# =========================
# FORMAT
# =========================
@app.post("/format", response_model=FormatResponse)
def format_for_users(req: FormatRequest):
    fmt = req.format_type.lower()
    if fmt not in ["sms", "whatsapp", "bulletin"]:
        raise HTTPException(status_code=400,
            detail="Invalid format_type. Allowed: 'sms', 'whatsapp', 'bulletin'")

    commodity  = req.prediction_data.get("commodity", "N/A")
    market     = req.prediction_data.get("market", "N/A")
    prediction = req.prediction_data.get("prediction_per_kg", 0)
    date       = req.prediction_data.get("date", "N/A")
    prev_price = req.prediction_data.get("previous_month_price", 0)
    conf       = req.prediction_data.get("confidence_pct", 90)
    note       = req.prediction_data.get("note", "")

    if fmt == "sms":
        msg  = f"{commodity} @ {market}: KES {prediction}/kg on {date}. Prev: KES {prev_price}/kg"
        cost = 0.50
    elif fmt == "whatsapp":
        msg = (
            f"*Market Price Forecast*\n\n"
            f"Commodity: {commodity}\n"
            f"Market: {market}\n"
            f"Date: {date}\n\n"
            f"Predicted Price: *KES {prediction}/kg*\n"
            f"Previous Price: KES {prev_price}/kg\n"
            f"Confidence: {conf}%\n\n"
            f"{note}\n\n"
            f"_Powered by CropEx_"
        )
        cost = 0.0
    else:
        msg = (
            f"\nMARKET PRICE BULLETIN\n{'=' * 50}\n\n"
            f"Commodity:          {commodity.upper()}\n"
            f"Market Location:    {market}\n"
            f"Forecast Date:      {date}\n\n"
            f"PRICE FORECAST\n{'=' * 50}\n"
            f"Predicted Price:    KES {prediction} per kg\n"
            f"Previous Price:     KES {prev_price} per kg\n"
            f"Price Range:        KES {req.prediction_data.get('lower_bound', 0)} - "
            f"{req.prediction_data.get('upper_bound', 0)} per kg\n"
            f"Confidence Level:   {conf}%\n\n"
            f"NOTES\n{'=' * 50}\n{note}\n\n"
            f"This forecast is provided as guidance only.\n"
            f"Report generated by CropEx Market Forecaster.\n"
        )
        cost = None

    return {
        "format_type":       fmt,
        "formatted_message": msg,
        "character_count":   len(msg),
        "estimated_cost":    cost,
    }

# =========================
# EXPLAINABILITY
# =========================
@app.post("/explainability", response_model=ExplainabilityResponse)
def get_explainability(req: ExplainabilityRequest):
    norm = req.commodity.strip().lower()
    if norm not in COMMODITY_META:
        raise HTTPException(status_code=400,
            detail=f"Commodity '{req.commodity}' not supported.")

    _check_inputs(req)
    fc        = _forecast(req, COMMODITY_META[norm][0])
    pred      = fc["predicted"]
    conf_adj  = fc["confidence"]
    estimated = fc["estimated"]
    naive     = fc["model_used"] == "naive_last"

    descriptions = {
        "3-Month Price Average": "Rolling 3-month average drives ~91% of model signal",
        "Previous Month Price":  f"Last recorded price of {req.previous_month_price} KES/kg",
        "Price Volatility":      "6-month std dev reflects market stability",
        "Seasonality":           "Month-of-year cyclical pattern (sin/cos encoded)",
        "Market Location":       f"{req.market} lat/lon and location features",
        "Other":                 "Longer price lags, 6-month average, commodity, unit, price type and year",
    }
    top_influencing_factors = [
        {"factor": factor, "importance": importance, "impact": impact_label(importance),
         "description": descriptions[factor]}
        for factor, importance in _factor_importances()
    ]

    if naive:
        # The forecast is last month's price. The factor list describes the RF (global
        # importances) and is shown for reference only; it does not drive this number.
        explanation_summary = (
            f"The forecast of KES {pred:.0f}/kg for {req.commodity} at {req.market} "
            "is last month's price (naive forecast, which beat the random forest in "
            "walk-forward backtesting). The factor importances describe the random forest "
            "and are for reference only."
        )
        dominant = "previous_month_price (naive forecast)"
    else:
        explanation_summary = (
            f"The forecast of KES {pred:.0f}/kg for {req.commodity} at {req.market} "
            f"is primarily driven by the 3-month rolling price average (~91% of model signal). "
            + (
                "price_ma_3 was estimated from previous_month_price — "
                "provide it directly for a more reliable forecast."
                if "price_ma_3" in estimated else
                "Full price history was provided — forecast reliability is good."
            )
        )
        dominant = "price_ma_3 (3-month moving average)"

    return {
        "commodity":               req.commodity,
        "market":                  req.market,
        "predicted_price":         round(pred, 2),
        "top_influencing_factors": top_influencing_factors,
        "explanation_summary":     explanation_summary,
        "confidence_factors": {
            "confidence_pct":        conf_adj,
            "dominant_feature":      dominant,
            "fields_estimated":      estimated,
            "data_quality":          "degraded" if estimated else "good",
            "prediction_reliability": "limited" if estimated else "good",
        },
        "model_used":              fc["model_used"],
        "fallback_reason":         fc["fallback_reason"],
    }

# =========================
# HEALTH
# =========================
def _feedback_db_writable() -> bool:
    """Checks the path feedback_store would use. Writes nothing."""
    try:
        path = os.path.abspath(feedback_store.get_db_path())
        if os.path.exists(path):
            return os.path.isfile(path) and os.access(path, os.W_OK)
        d = os.path.dirname(path)
        while not os.path.isdir(d):
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
        return os.path.isdir(d) and os.access(d, os.W_OK)
    except Exception:
        return False

@app.get("/health")
def health():
    rf_loaded = _RF.get() is not None   # loads the RF once, then cached
    writable  = _feedback_db_writable()
    champ_problem = CHAMPION.get("load_problem")
    degraded = (
        champ_problem is not None
        or not writable
        or (CHAMPION.get("model_type") == "random_forest" and not rf_loaded)
    )
    return {
        "status":                  "degraded" if degraded else "ok",
        "champion_model_type":     CHAMPION.get("model_type"),
        "champion_source":         CHAMPION.get("source"),
        "data_through":            CHAMPION.get("data_through"),
        "champion_load_problem":   champ_problem,
        "rf_model_loaded":         rf_loaded,
        "rf_load_problem":         _RF.problem,
        "feedback_db_writable":    writable,
        "api_version":             API_VERSION,
        "champion_schema_version": CHAMPION.get("schema_version"),
    }

# =========================
# FEEDBACK
# =========================
@app.post("/feedback", response_model=FeedbackResponse)
def collect_feedback(req: FeedbackRequest):
    timestamp = req.timestamp or datetime.now().isoformat()

    if req.accuracy_rating is not None and not (1 <= req.accuracy_rating <= 5):
        raise HTTPException(status_code=400, detail="accuracy_rating must be 1–5")
    if req.usefulness_rating is not None and not (1 <= req.usefulness_rating <= 5):
        raise HTTPException(status_code=400, detail="usefulness_rating must be 1–5")

    try:
        saved = feedback_store.save_feedback(
            user_id=req.user_id,
            prediction_id=req.prediction_id,
            predicted_price=req.predicted_price,
            actual_price=req.actual_price,
            accuracy_rating=req.accuracy_rating,
            usefulness_rating=req.usefulness_rating,
            comments=req.comments,
            client_timestamp=req.timestamp,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        # Don't pretend it was stored (e.g. read-only or missing disk).
        raise HTTPException(status_code=503, detail="Feedback could not be saved right now. Please retry later.")
    feedback_id = saved["feedback_id"]

    return {
        "feedback_id": feedback_id,
        "status":      "success",
        "message":     "Thank you for your feedback! Your input helps improve our predictions.",
        "timestamp":   timestamp,
    }

# =========================
# IMPACT STATS
# =========================
@app.get("/impact-stats", response_model=ImpactStatsResponse)
def get_impact_stats():
    try:
        s = feedback_store.compute_stats()
    except Exception:
        raise HTTPException(status_code=503, detail="Feedback statistics are unavailable right now.")
    return {
        "total_predictions":     None,  # predictions are not logged
        "total_users":           s["distinct_users"],
        "average_accuracy":      s["price_accuracy"],
        "total_markets_covered": len(MARKET_COORDS),
        "commodities_tracked":   sorted(COMMODITY_META.keys()),
        "user_satisfaction":     s["average_usefulness_rating"],
        "cost_savings_estimate": None,  # not measurable
        "last_updated":          s["last_feedback_at"] or datetime.now().isoformat(),
        "feedback_count":          s["feedback_count"],
        "average_accuracy_rating": s["average_accuracy_rating"],
        "mape_pct":                s["mape_pct"],
        "price_comparisons":       s["price_comparisons"],
    }