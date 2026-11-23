# API Endpoints Documentation

This document describes the endpoints implemented in `price_api/app.py` (API version `2.0.0`). Request and response field names were checked against the Pydantic models and handler code in `app.py`, `enhanced_endpoints.py`, `predictor.py`, `feedback_store.py` and `geo.py`. For full request/response shapes see [SCHEMAS.md](../SCHEMAS.md).

## Base URL
`http://localhost:8000` (development)

## What the forecast is (current behaviour)

- **The served forecast is the naive champion.** `price_api/artifacts/champion.json` currently sets `model_type` to `naive_last`: the predicted price for next month is the last observed month's price (`previous_month_price`).
- **Error bands are empirical.** `lower_bound` and `upper_bound` are `predicted * ratio_p10` and `predicted * ratio_p90`, using per-crop ratios (actual / prediction) measured in the 60-month walk-forward backtest stored in `champion.json`. They are an 80% range from that backtest, not a guarantee for future months.
- **Backtest result in `champion.json`:** window 60 months, `n` = 2021 predictions, naive MAPE 15.593%, median APE 9.744%. The same file records `data_through` = `2026-10`.
- **Random Forest is not the served model.** The Random Forest was found to have target leakage in its inputs and did not beat the naive forecast in the honest backtest. `/explainability` still reports its feature importances, see below.
- Details and caveats: [RETRAINING_RESEARCH.md](../RETRAINING_RESEARCH.md). Monthly retrain pipeline (draft, not wired to the API): [pipeline/README.md](../pipeline/README.md).

---

## Endpoints

### 1. GET `/`
Root endpoint providing API information and available endpoints.

**Response (fields):**
- `message`, `endpoints` (map of path to description), `example_request`
- `supported_commodities`: sorted list of the names accepted by `/predict` (see [Supported Commodities](#supported-commodities))
- `supported_markets`: sorted list of the market names in the coordinates table (see [SCHEMAS.md](../SCHEMAS.md#supported-markets))

---

### 2. POST `/predict`
Next-month price forecast for one commodity at one market.

**Request Body:**
```json
{
  "date": "2025-12-05",
  "admin1": "Nairobi",
  "market": "Wakulima (Nairobi)",
  "commodity": "tomatoes",
  "pricetype": "retail",
  "previous_month_price": 58.2
}
```

Optional price-history fields (`price_3_months_ago`, `price_6_months_ago`, `price_ma_3`, `price_ma_6`, `price_vol_6`) are accepted. With the naive champion they do **not** change `prediction_per_kg` or `confidence_pct`. They are only used if the champion is a Random Forest.

**Response (illustrative, computed from `champion.json` for Tomatoes at `previous_month_price` 58.2):**
```json
{
  "commodity": "tomatoes",
  "market": "Wakulima (Nairobi)",
  "date": "2025-12-05",
  "prediction_per_kg": 58.2,
  "unit": "KG",
  "market_type": "retail",
  "previous_month_price": 58.2,
  "confidence_pct": 76,
  "error_margin": "+-14.35",
  "lower_bound": 44.12,
  "upper_bound": 72.55,
  "unreasonable": false,
  "note": "Prediction within normal range.",
  "model_used": "naive_last",
  "fallback_reason": null
}
```

**Field notes:**
- `prediction_per_kg`: the champion forecast. Despite the legacy name it is in `unit`, the WFP unit of the series (for example `"64 KG"` for wholesale tomatoes, `"KG"` for retail potatoes), the same unit as `previous_month_price`. `unit` is read from `artifacts/latest_prices.json`; it is `"unknown (...)"` when that file has no matching series.
- `price_per_kg` (optional): the forecast divided by the pack weight when `unit` is `KG` or `<n> KG`; `null` for units without a fixed weight (`Head`, `Unit`, `Bunch`).
- `lower_bound` / `upper_bound`: `prediction * ratio_p10` and `prediction * ratio_p90` for the crop's band in `champion.json` (`error_bands`). If the crop has no band, the `_ALL` band is used. If `champion.json` fails to load, a built-in default band of 0.75 to 1.35 is used.
- `confidence_pct`: an integer computed from the relative width of the band, `round(100 * (1 - (upper - lower) / (2 * prediction)))`, clamped to 0 to 100 (`predictor.py`, `confidence_from_band`). It describes band width, not a statistical probability.
- `error_margin`: `"+-"` followed by the larger of `upper - prediction` and `prediction - lower`, rounded to 2 decimals.
- `unreasonable`: true when the forecast is outside `plausible_min`–`plausible_max`: half the lowest to twice the highest latest WFP price observed across markets for the same commodity, price type and unit (`latest_prices.plausibility`, `PLAUSIBLE_FACTOR` 2.0, a tolerance choice). The `note` then gives the range. With no reference prices it is `false` and the note says the check was not run. (The old fixed `CROP_THRESHOLDS` compared KES prices with WFP pack sizes in kg and was removed.)
- `model_used`: `"naive_last"` for the current champion, or `"random_forest"` if the champion were a Random Forest and it loaded.
- `fallback_reason`: `null` normally. If the configured Random Forest fails at runtime, the naive forecast is returned and this field says why.

**Supported commodities for `/predict`:** see [Supported Commodities](#supported-commodities). `market` must match a known market name exactly, or partially (case-insensitive). If several names match, a Nairobi match is preferred.

---

### 3. POST `/recommendations`
Sell / hold / monitor recommendation based on the prediction.

**Purpose:** Turns a forecast and its band into a rule-based action (`enhanced_endpoints.py`, `_decision_logic`) and a farmer-facing message.

**Request Body:**
```json
{
  "commodity": "cabbage",
  "market": "Wakulima (Nairobi)",
  "admin1": "Nairobi",
  "pricetype": "retail",
  "predicted_price": 120.0,
  "previous_price": 100.0,
  "lower_bound": 105.0,
  "upper_bound": 135.0,
  "confidence_pct": 85.0,
  "unreasonable": false
}
```
Use the `predicted_price`, `lower_bound`, `upper_bound`, `confidence_pct` and `unreasonable` values returned by `/predict`. `previous_price` must be greater than 0.

**Response (illustrative shape; `reasons` and `farmer_message` text is generated by code or Gemini and will vary):**
```json
{
  "commodity": "cabbage",
  "market": "Wakulima (Nairobi)",
  "action": "hold",
  "urgency": "soon",
  "price_trend": "rising",
  "trend_pct": 20.0,
  "model_confidence": "high",
  "reasons": ["..."],
  "farmer_message": "...",
  "rationale": "..."
}
```

**Field values:**
- `action`: `sell`, `hold`, `monitor`
- `urgency`: `immediate`, `soon`, `later`, `none`
- `price_trend`: `rising`, `falling`, `stable`
- `trend_pct`: `(predicted_price - previous_price) / previous_price * 100`, rounded to 2 decimals
- `model_confidence`: `high`, `medium`, `low` (from `confidence_pct`)
- `farmer_message`: generated by Gemini (`gemini_client.py`) when `GEMINI_API_KEY` is set and the call succeeds. Otherwise a fixed template is returned.

**Supported commodities:** `cabbage`, `kale`, `onion`, `onions`, `potatoes`, `tomatoes`, `bananas`. The singular forms `tomato` and `potato` are **not** accepted here (they are accepted by `/predict`).

---

### 4. POST `/micro-market`
Lists nearby markets with straight-line distances and, where WFP has a recent observation, that market's latest observed price.

**Purpose:** Finds markets within a radius of a known market, using the coordinates of the markets in the WFP dataset.

**Request Body:**
```json
{
  "commodity": "tomatoes",
  "region": "Nairobi",
  "radius_km": 30.0,
  "date": "2025-12-05"
}
```
- `pricetype`: optional, `Retail` or `Wholesale`. Without it, the most recent observation across price types is used.
- `commodity`: one of the supported commodities for this endpoint (see `/recommendations`).
- `region`: must resolve to a known market name (exact or partial match). Otherwise the API returns 400.
- `radius_km`: optional, defaults to 50.
- `date`: accepted by the request model but not used by the handler.

**Response (real output for the request above, with `artifacts/latest_prices.json` built from the local WFP snapshot):**
```json
{
  "commodity": "tomatoes",
  "region": "Nairobi",
  "nearby_markets": [
    {"market_name": "Wakulima (Nairobi)", "distance_km": 1.6, "market_type": null, "estimated_price": null, "price_per_kg": null, "price_date": null, "price_unit": null, "price_pricetype": null, "price_note": "latest WFP observation is from 2025-06, older than 3 months before data through 2026-10"},
    {"market_name": "Kitengela (Kajiado)", "distance_km": 4.6, "market_type": null, "estimated_price": null, "price_per_kg": null, "price_date": null, "price_unit": null, "price_pricetype": null, "price_note": "latest WFP observation is from 2023-07, older than 3 months before data through 2026-10"},
    {"market_name": "Mathare (Nairobi)", "distance_km": 5.0, "market_type": null, "estimated_price": null, "price_per_kg": null, "price_date": null, "price_unit": null, "price_pricetype": null, "price_note": "no WFP observation for this market/commodity"},
    {"market_name": "Kibra (Nairobi)", "distance_km": 6.3, "market_type": null, "estimated_price": null, "price_per_kg": null, "price_date": null, "price_unit": null, "price_pricetype": null, "price_note": "no WFP observation for this market/commodity"},
    {"market_name": "Mukuru (Nairobi)", "distance_km": 7.0, "market_type": null, "estimated_price": null, "price_per_kg": null, "price_date": null, "price_unit": null, "price_pricetype": null, "price_note": "no WFP observation for this market/commodity"}
  ],
  "localized_forecast": {},
  "recommended_market": "Wakulima (Nairobi)",
  "market_comparison": "Found 5 market(s) within 30 km of Nairobi. None has a recent WFP price for tomatoes (see price_note).",
  "data_source": "WFP market coordinates",
  "price_data_through": "2026-10"
}
```

**Field notes:**
- `distance_km` is the great-circle (haversine) distance between market coordinates, not a road distance. Results are sorted nearest first and capped at 5.
- `estimated_price` is the **latest observed WFP price** for that market and commodity (not a forecast), in WFP's unit (`price_unit`, e.g. `64 KG` for a tomato crate). `price_per_kg` divides by the weight when the unit is `KG` or `<n> KG`; it is `null` for units like `Head`, `Unit` or `Bunch`.
- A price is shown only when its month is at most 3 months before `price_data_through` (the latest month in the snapshot). Otherwise the price fields are `null` and `price_note` says why. In the example, none of Nairobi's neighbours has a recent tomato price.
- Prices come from `price_api/artifacts/latest_prices.json`, written by `pipeline/latest_prices.py` (and by the monthly retrain). If the file is missing or invalid, all price fields are `null`.
- `market_type` is always `null`. Prices are not compared or ranked.
- `localized_forecast` is always an empty object.
- `recommended_market` is the nearest market by distance, not a price-based recommendation.

---

### 5. POST `/format`
Formats a prediction for SMS, WhatsApp or a printed bulletin. It does not send anything.

**Request Body:**
```json
{
  "prediction_data": {
    "commodity": "cabbage",
    "market": "Wakulima (Nairobi)",
    "prediction_per_kg": 115.5,
    "date": "2025-12-05",
    "previous_month_price": 100.0,
    "confidence_pct": 90,
    "lower_bound": 105,
    "upper_bound": 125,
    "note": "Prediction within normal range."
  },
  "format_type": "sms"
}
```

`language` is accepted by the request model but is not used.

**Format Types:**
- `sms`: one line. The API does not truncate or check a 160-character limit. `estimated_cost` is `0.5`, a fixed value in code.
- `whatsapp`: multi-line message with emojis. `estimated_cost` is `0.0`.
- `bulletin`: plain-text bulletin. `estimated_cost` is `null`.

**Response (SMS):**
```json
{
  "format_type": "sms",
  "formatted_message": "cabbage @ Wakulima (Nairobi): KES 115.5/kg on 2025-12-05. Prev: KES 100.0/kg",
  "character_count": 76,
  "estimated_cost": 0.5
}
```

**Response (WhatsApp):** the message is the template in `app.py` filled from `prediction_data`. With the same `prediction_data` and `format_type` set to `whatsapp`, `character_count` is 233. The full message is shown in [SCHEMAS.md](../SCHEMAS.md#format-smswhatsapp).

---

### 6. POST `/explainability`
Feature importances for the **legacy Random Forest**, not a live explanation of the forecast.

**Purpose:** Shows the Random Forest's global feature importances, grouped into factors.

**Request Body:** the same fields as `/predict` (`date`, `admin1`, `market`, `commodity`, `pricetype`, `previous_month_price`, plus the optional price-history fields).

**Response (shape; values come from the Random Forest artifact and are not reproduced here):**
```json
{
  "commodity": "tomatoes",
  "market": "Wakulima (Nairobi)",
  "predicted_price": "<float>",
  "top_influencing_factors": [
    {"factor": "3-Month Price Average", "importance": "<float>", "impact": "<label>", "description": "..."}
  ],
  "explanation_summary": "...",
  "confidence_factors": {
    "confidence_pct": "<float>",
    "dominant_feature": "previous_month_price (naive forecast)",
    "fields_estimated": ["..."],
    "data_quality": "good | degraded",
    "prediction_reliability": "good | limited"
  },
  "model_used": "naive_last",
  "fallback_reason": null
}
```

**Important:**
- The served forecast is the naive champion (`model_used` is `naive_last`). The `top_influencing_factors` describe the **Random Forest**, shown for reference only. They do not drive the forecast.
- The importances are the Random Forest's `feature_importances_`, summed into factor groups (`feature_importance.py`) and rounded to 4 decimals. Each factor has an `impact` label from its importance (`Very High`, `High`, `Medium`, `Low`, `Minimal`).
- If the Random Forest artifact cannot be loaded, `top_influencing_factors` is an empty list.
- The `description` strings are fixed text in `app.py`. The 3-month-average wording says "~91%", which matches the 0.9074 importance reported for the deployed model in [RETRAINING_RESEARCH.md](../RETRAINING_RESEARCH.md).
- Not SHAP. There are no per-prediction attributions.
- `dominant_feature` is `previous_month_price (naive forecast)` for the naive champion.

---

### 7. POST `/feedback`
Stores user feedback about a prediction.

**Purpose:** Collects accuracy and usefulness ratings and an optional actual price, and saves them to SQLite. Those stored rows feed `/impact-stats`.

**Request Body:**
```json
{
  "user_id": "farmer123",
  "prediction_id": "pred_001",
  "predicted_price": 65.5,
  "actual_price": 67.0,
  "accuracy_rating": 4,
  "usefulness_rating": 5,
  "comments": "Very helpful prediction!",
  "timestamp": "2025-12-05T11:08:00"
}
```

**Fields (all optional):**
- `predicted_price` and `actual_price`: when both are present and `actual_price` > 0, the row is used for the MAPE in `/impact-stats`. The prediction is whatever the client sends; the API does not check it against stored predictions.
- `accuracy_rating`, `usefulness_rating`: integers 1 to 5. Other values return 400.
- `timestamp`: optional client timestamp, stored as `client_timestamp`. The server stores its own UTC `created_at` for every row.

**Response:**
```json
{
  "feedback_id": "FB-<8 hex characters>",
  "status": "success",
  "message": "Thank you for your feedback! Your input helps us improve our predictions.",
  "timestamp": "<client timestamp, or server time if none was sent>"
}
```

**Storage:** SQLite. The file path is the environment variable `FEEDBACK_DB_PATH` if set; otherwise `price_api/data/feedback.db`. The directory is created if missing. On hosts with an ephemeral disk (for example Render), point `FEEDBACK_DB_PATH` at a persistent disk. If the save fails, the API returns 503 and does not report success.

---

### 8. GET `/impact-stats`
Aggregated statistics computed from stored feedback.

**Purpose:** Reports what the feedback table shows. Fields that have no supporting data are `null`. They are never filled with made-up numbers.

**Response (shape, with no feedback stored yet):**
```json
{
  "total_predictions": null,
  "total_users": 0,
  "average_accuracy": null,
  "total_markets_covered": 40,
  "commodities_tracked": ["banana", "bananas", "cabbage", "kale", "onion", "onions", "potato", "potatoes", "tomato", "tomatoes"],
  "user_satisfaction": null,
  "cost_savings_estimate": null,
  "last_updated": "<ISO timestamp>",
  "feedback_count": 0,
  "average_accuracy_rating": null,
  "mape_pct": null,
  "price_comparisons": 0
}
```

**Field notes:**
- `total_predictions`: always `null`. Predictions are not logged.
- `total_users`: count of distinct `user_id` values in stored feedback.
- `average_accuracy`: `max(0, 1 - MAPE)` on a 0 to 1 scale, from rows with `predicted_price` and `actual_price`. `null` when there are no such rows.
- `mape_pct`: mean of `|predicted - actual| / actual` in percent, rounded to 2 decimals. `null` when there are no such rows.
- `price_comparisons`: number of rows used for the MAPE.
- `user_satisfaction`: mean `usefulness_rating`, or `null`.
- `average_accuracy_rating`: mean `accuracy_rating`, or `null`.
- `total_markets_covered`: number of entries in the market coordinates table (40 in the current code).
- `commodities_tracked`: the sorted commodity names accepted by `/predict`.
- `cost_savings_estimate`: always `null`. It is not measured.
- `last_updated`: time of the latest stored feedback, or the current time if none.

If the statistics cannot be read, the API returns 503.

---

### 9. GET `/health`
Service and champion status.

**Response (fields):**
```json
{
  "status": "ok",
  "champion_model_type": "naive_last",
  "champion_source": "file",
  "data_through": "2026-10",
  "champion_load_problem": null,
  "rf_model_loaded": "<bool>",
  "rf_load_problem": "<string or null>",
  "feedback_db_writable": "<bool>",
  "api_version": "2.0.0",
  "champion_schema_version": 2
}
```

- `status` is `"degraded"` if the champion failed to load (`champion_source` is then `default`, and `champion_load_problem` says why), if the feedback database path is not writable, or if the champion is a Random Forest that did not load.
- `rf_model_loaded` loads the Random Forest artifact on the first call, which can make that first call slow.

---

### 10. POST `/webhook`
WhatsApp message intent classification, used by the Node.js/Twilio layer.

**Request:** `{ "from_number": "+254...", "body": "price of mahindi" }`

**Response:** `intent`, `symbol`, `quantity`, `unit`, `via_llm`, `sms`. Rules run first. The Gemini fallback runs only when the rules do not match, and `via_llm` is `true` when the Gemini result is used. Full shapes are in [SCHEMAS.md](../SCHEMAS.md#webhook-intent-routing).

---

## Supported Commodities

| Endpoint | Accepted names |
|----------|----------------|
| `/predict`, `/explainability`, `/impact-stats` (`commodities_tracked`) | `banana`, `bananas`, `cabbage`, `kale`, `onion`, `onions`, `potato`, `potatoes`, `tomato`, `tomatoes` |
| `/recommendations`, `/micro-market` | `cabbage`, `kale`, `onion`, `onions`, `potatoes`, `tomatoes`, `bananas` |

Names are case-insensitive. Canonical model names (for example `Potatoes (Irish)`, `Onions (dry)`) are used internally and are not accepted as request values.

## Error Responses

### 400 Bad Request
Returned when, for example:
- The commodity is not supported for that endpoint
- `format_type` is not `sms`, `whatsapp` or `bulletin`
- A rating is outside 1 to 5
- The market or region is not a known market name
- `date` is not in `YYYY-MM-DD` form
- `previous_month_price` or `previous_price` is not a positive number

```json
{
  "detail": "Commodity 'mango' not supported. Allowed: ['banana', 'bananas', 'cabbage', 'kale', 'onion', 'onions', 'potato', 'potatoes', 'tomato', 'tomatoes']"
}
```

### 422 Validation Error
Returned when the request body does not match the Pydantic model (for example, a required field is missing). The shape is FastAPI's standard validation error list, abbreviated here:
```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["body", "commodity"],
      "msg": "Field required"
    }
  ]
}
```

### 503 Service Unavailable
Returned by `/feedback` when the feedback cannot be saved, and by `/impact-stats` when the statistics cannot be read.

## Interactive Documentation

- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`

## Notes

### Current limitations
1. **Forecast model:** the served forecast is last month's price. It is not a learned model. See [RETRAINING_RESEARCH.md](../RETRAINING_RESEARCH.md).
2. **Micro-market:** nearby markets by straight-line distance. Prices are the latest WFP observations, shown only when at most 3 months old; many markets have none that recent.
3. **Explainability:** shows the legacy Random Forest's feature importances, not per-prediction attributions.
4. **Predictions are not logged,** so `total_predictions` is always `null`.
5. **Retraining:** a monthly retrain pipeline exists as a draft in [pipeline/README.md](../pipeline/README.md). It is not wired to the API.

### Security
- Request bodies are validated with Pydantic models.
- CORS allows all origins (`allow_origins=["*"]`). Restrict this in production.
- No authentication or authorization is implemented. Add it before production use.
