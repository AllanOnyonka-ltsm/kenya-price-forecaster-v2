# CropEx API Schemas — Integration Reference

Request and response schemas for the endpoints in `price_api/app.py`. Field names were checked against the Pydantic models and handlers in `app.py`, `enhanced_endpoints.py`, `intent_router.py`, `predictor.py`, `feedback_store.py` and `geo.py`. Behaviour notes are in [price_api/API_ENDPOINTS.md](price_api/API_ENDPOINTS.md). Background on the forecast model: [RETRAINING_RESEARCH.md](RETRAINING_RESEARCH.md).

---

## Webhook (Intent Routing)

**Endpoint:** `POST /webhook`
**Purpose:** Classify WhatsApp messages into structured intents (GREETING, PRICE_QUERY, SELL_ORDER, BUY_ORDER, UNKNOWN)

### Request

```json
{
  "from_number": "+254712345678",
  "body": "price of mahindi"
}
```

| Field | Type | Required | Example | Notes |
|-------|------|----------|---------|-------|
| `from_number` | string | ✅ | `"+254712345678"` | WhatsApp number (any format) |
| `body` | string | ✅ | `"price of mahindi"` | User message (English or Swahili) |

### Response (Success — 200 OK)

```json
{
  "intent": "PRICE_QUERY",
  "symbol": "MAIZE",
  "quantity": null,
  "unit": null,
  "via_llm": false,
  "sms": null
}
```

| Field | Type | Values | Notes |
|-------|------|--------|-------|
| `intent` | enum | `GREETING`, `PRICE_QUERY`, `SELL_ORDER`, `BUY_ORDER`, `UNKNOWN` | What the farmer is asking for |
| `symbol` | string | `MAIZE`, `TOMATO`, `POTATO`, `BEANS`, `WHEAT`, `RICE`, `SORGHUM`, `ONION`, `CASSAVA`, or `null` | Canonical crop symbol for C++ engine |
| `quantity` | float | `null` or number | Quantity for SELL_ORDER/BUY_ORDER (from "50 bags") |
| `unit` | string | `"bags"`, `"kg"`, `"tonnes"`, `"crates"`, or `null` | Unit of quantity. Defaults to `"bags"` for orders with a quantity but no unit (`intent_router.py`) |
| `via_llm` | bool | `true`, `false` | `true` when the Gemini fallback produced the result (rules did not match) |
| `sms` | string | `null` or helpful text | Fallback SMS for GREETING and for UNKNOWN or incomplete messages |

### Response Examples

#### 1. Greeting (returns menu)
```json
{
  "intent": "GREETING",
  "symbol": null,
  "quantity": null,
  "unit": null,
  "via_llm": false,
  "sms": "👋 *CropEx* — Your Smart Market / Soko Lako\n━━━━━━━━━━━━━━\n... [full menu: MENU_TEXT in intent_router.py]"
}
```

#### 2. Price Query (no SMS — route to C++)
```json
{
  "intent": "PRICE_QUERY",
  "symbol": "TOMATO",
  "quantity": null,
  "unit": null,
  "via_llm": false,
  "sms": null
}
```

#### 3. Sell Order
```json
{
  "intent": "SELL_ORDER",
  "symbol": "MAIZE",
  "quantity": 50.0,
  "unit": "bags",
  "via_llm": false,
  "sms": null
}
```

#### 4. Unknown Input (helpful prompt)
```json
{
  "intent": "UNKNOWN",
  "symbol": null,
  "quantity": null,
  "unit": null,
  "via_llm": true,
  "sms": "❓ Sijui hiyo / I didn't understand.\n\n👋 *CropEx* — Your Smart Market / Soko Lako\n...[full menu]"
}
```

### Integration Flow (for Node/Twilio)

```
Twilio Webhook (form-encoded)
    ↓
Parse: from = "+254...", body = "price of mahindi"
    ↓
POST to Python /webhook
    ↓
Receive: { intent: "PRICE_QUERY", symbol: "MAIZE", ... }
    ↓
If sms → Send SMS via Twilio
If intent → Pass to C++ engine
```

---

## Price Prediction

**Endpoint:** `POST /predict`
**Purpose:** Next-month price forecast for a commodity. The served model is the naive champion (`champion.json`, `model_type` `naive_last`): next month's price equals `previous_month_price`.

### Request

```json
{
  "date": "2025-12-05",
  "admin1": "Nairobi",
  "market": "Wakulima (Nairobi)",
  "commodity": "tomatoes",
  "pricetype": "retail",
  "previous_month_price": 58.2,
  "price_3_months_ago": 55.0,
  "price_6_months_ago": 52.0,
  "price_ma_3": 55.5,
  "price_ma_6": 54.0,
  "price_vol_6": 2.5
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `date` | string (YYYY-MM-DD) | ✅ | Forecast date |
| `admin1` | string | ✅ | Region/county (e.g., "Nairobi") |
| `market` | string | ✅ | Market name (see [Supported Markets](#supported-markets)); exact or partial, case-insensitive |
| `commodity` | string | ✅ | `tomato`, `tomatoes`, `onion`, `onions`, `potato`, `potatoes`, `kale`, `cabbage`, `banana`, `bananas` (case-insensitive) |
| `pricetype` | string | ✅ | `"retail"` or `"wholesale"` |
| `previous_month_price` | float | ✅ | Last known price (KES/kg). Must be a positive number |
| `price_3_months_ago` | float | ❌ | Accepted. Only used if the champion is a Random Forest. Otherwise ignored |
| `price_6_months_ago` | float | ❌ | As above |
| `price_ma_3` | float | ❌ | As above |
| `price_ma_6` | float | ❌ | As above |
| `price_vol_6` | float | ❌ | As above |

### Response

The values below are computed from `champion.json` for Tomatoes at `previous_month_price` 58.2 (naive forecast; band ratios p10 0.758, p90 1.2465; unreasonable threshold 64 for tomatoes).

```json
{
  "commodity": "tomatoes",
  "market": "Wakulima (Nairobi)",
  "date": "2025-12-05",
  "prediction_per_kg": 58.2,
  "unit": "kg",
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

| Field | Type | Notes |
|-------|------|-------|
| `prediction_per_kg` | float | Forecast price (KES/kg). With the current champion, equals `previous_month_price` |
| `confidence_pct` | int | 0–100. Computed from the relative width of the band, not a probability |
| `lower_bound` | float | `prediction_per_kg × ratio_p10` for the crop in `champion.json` |
| `upper_bound` | float | `prediction_per_kg × ratio_p90` for the crop. The bounds form an 80% range from the backtest |
| `unreasonable` | bool | `true` when the prediction is above a fixed per-crop threshold in `app.py` (`CROP_THRESHOLDS`) |
| `error_margin` | string | `"+-X.XX"` KES: the larger distance from the prediction to either bound |
| `model_used` | string | `naive_last` (current champion) or `random_forest` |
| `fallback_reason` | string or null | Set only if a configured Random Forest failed and the naive forecast was used |

### Supported Markets

The market names accepted by `market` and `region` (from the coordinates table in `app.py`, `MARKET_COORDS`; 40 markets):

```
Dagahaley (Daadab), Dandora (Nairobi), Eldoret town (Uasin Gishu),
Ethiopia (Kakuma), Garissa town (Garissa), Hagadera (Daadab),
HongKong (Kakuma), IFO (Daadab), Illbissil Food Market (Kajiado),
Kaanwa (Tharaka Nithi), Kakuma 2, Kakuma 3, Kakuma 4,
Kalahari (Mombasa), Kalobeyei (Village 1), Kalobeyei (Village 2),
Kalobeyei (Village 3), Kangemi (Nairobi), Karatina (Nyeri),
Kathonzweni (Makueni), Kawangware (Nairobi), Kibra (Nairobi),
Kibuye (Kisumu), Kisumu, Kitengela (Kajiado), Kitui,
Kongowea (Mombasa), Lodwar town, Lomut (West Pokot),
Makutano (West Pokot), Marigat town (Baringo), Mathare (Nairobi),
Mogadishu (Kakuma), Mukuru (Nairobi), Nairobi, Nakuru,
Takaba (Mandera), Tala Centre Market (Machakos), Wakulima (Nairobi),
Wakulima (Nakuru)
```

### Supported Commodities

```
banana, bananas, cabbage, kale, onion, onions, potato, potatoes, tomato, tomatoes
```

(`/recommendations` and `/micro-market` accept `bananas`, `cabbage`, `kale`, `onion`, `onions`, `potatoes`, `tomatoes`.)

---

## Recommendations

**Endpoint:** `POST /recommendations`
**Purpose:** Rule-based sell / hold / monitor action, with a farmer message

### Request

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

| Field | Type | Notes |
|-------|------|-------|
| `commodity` | string | `bananas`, `cabbage`, `kale`, `onion`, `onions`, `potatoes`, `tomatoes` |
| `market` | string | Market name |
| `admin1` | string | Region/county |
| `pricetype` | string | `"retail"` or `"wholesale"` |
| `predicted_price` | float | From `/predict` response (`prediction_per_kg`) |
| `previous_price` | float | Last known price. Must be greater than 0 |
| `lower_bound` | float | From `/predict` response |
| `upper_bound` | float | From `/predict` response |
| `confidence_pct` | float | From `/predict` response |
| `unreasonable` | bool | From `/predict` response |

### Response

Shape only. `reasons`, `rationale` and `farmer_message` text is generated by code or Gemini and varies.

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

| Field | Type | Values | Notes |
|-------|------|--------|-------|
| `action` | enum | `sell`, `hold`, `monitor` | What the farmer should do |
| `urgency` | enum | `immediate`, `soon`, `later`, `none` | Time sensitivity |
| `price_trend` | enum | `rising`, `falling`, `stable` | Direction of the predicted change |
| `trend_pct` | float | e.g., `20.0` | `(predicted_price − previous_price) / previous_price × 100`, 2 decimals |
| `model_confidence` | enum | `high`, `medium`, `low` | Derived from `confidence_pct` |
| `reasons` | list of string | | Rule-based explanations |
| `farmer_message` | string | | WhatsApp text. Generated by Gemini when `GEMINI_API_KEY` is set and the call succeeds; otherwise a fixed template |
| `rationale` | string | | Plain-text summary of the trend and interval |

---

## Micro-Market Forecasting

**Endpoint:** `POST /micro-market`
**Purpose:** List nearby markets by straight-line (haversine) distance, with each market's latest WFP price when that observation is at most 3 months old.

### Request

```json
{
  "commodity": "tomatoes",
  "region": "Nairobi",
  "radius_km": 30.0,
  "date": "2025-12-05"
}
```

| Field | Type | Notes |
|-------|------|-------|
| `commodity` | string | Must be one of the `/recommendations` commodities |
| `region` | string | Must resolve to a market name (400 otherwise) |
| `radius_km` | float | Optional. Default 50 |
| `date` | string | Accepted, not used by the handler |
| `pricetype` | string | Optional. `Retail` or `Wholesale`; default: most recent across price types |

### Response

Real output for the request above (WFP coordinates in `app.py`, prices from `artifacts/latest_prices.json`):

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

| Field | Type | Notes |
|-------|------|-------|
| `nearby_markets` | list | Up to 5 markets, nearest first. `distance_km` is straight-line, 1 decimal |
| `nearby_markets[].estimated_price` | float/null | Latest observed WFP price (not a forecast), in `price_unit`. `null` if older than 3 months before `price_data_through` or missing |
| `nearby_markets[].price_per_kg` | float/null | `estimated_price` divided by the weight for `KG` / `<n> KG` units; `null` otherwise |
| `nearby_markets[].price_date` / `price_unit` / `price_pricetype` | string/null | Date, WFP unit and price type of that observation |
| `nearby_markets[].price_note` | string | Why the price is shown or `null` |
| `price_data_through` | string/null | Latest month in the price file (`YYYY-MM`); `null` if the file is missing |
| `localized_forecast` | object | Always `{}` |
| `recommended_market` | string | Nearest market by distance (not price-based). Empty string if none in range |
| `market_comparison` | string | Text summary. Says how many markets have a recent price; prices are not compared |
| `data_source` | string | `"WFP market coordinates"` |

---

## Format (SMS/WhatsApp)

**Endpoint:** `POST /format`
**Purpose:** Format a prediction as text. The API does not send anything.

### Request

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
  "format_type": "whatsapp"
}
```

| Field | Type | Values | Notes |
|-------|------|--------|-------|
| `prediction_data` | object | | Keys used: `commodity`, `market`, `prediction_per_kg`, `date`, `previous_month_price`, `confidence_pct`, `note`, `lower_bound`, `upper_bound` (bulletin only). Missing keys get defaults |
| `format_type` | enum | `sms`, `whatsapp`, `bulletin` | Output channel. Other values return 400 |
| `language` | string | | Optional. Accepted, not used |

### Response (SMS)

```json
{
  "format_type": "sms",
  "formatted_message": "cabbage @ Wakulima (Nairobi): KES 115.5/kg on 2025-12-05. Prev: KES 100.0/kg",
  "character_count": 76,
  "estimated_cost": 0.5
}
```

### Response (WhatsApp)

For the request above (`format_type` `whatsapp`):

```json
{
  "format_type": "whatsapp",
  "formatted_message": "📊 *Market Price Forecast*\n\n🌾 Commodity: cabbage\n📍 Market: Wakulima (Nairobi)\n📅 Date: 2025-12-05\n\n💰 Predicted Price: *KES 115.5/kg*\n📉 Previous Price: KES 100.0/kg\n📊 Confidence: 90%\n\nPrediction within normal range.\n\n_Powered by CropEx_",
  "character_count": 233,
  "estimated_cost": 0.0
}
```

| Field | Type | Notes |
|-------|------|-------|
| `character_count` | int | `len(formatted_message)` |
| `estimated_cost` | float or null | Fixed in code: `0.5` (SMS), `0.0` (WhatsApp), `null` (bulletin). Not a measured cost |

---

## Explainability (XAI)

**Endpoint:** `POST /explainability`
**Purpose:** Global feature importances of the **legacy Random Forest**. The forecast itself is the naive champion, so these factors do not drive the number returned by `/predict`. Not SHAP and not per-prediction attributions.

### Request

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

Same fields as `/predict`.

### Response

Shape only. Importance values come from the Random Forest artifact and are not reproduced here.

```json
{
  "commodity": "tomatoes",
  "market": "Wakulima (Nairobi)",
  "predicted_price": "<float>",
  "top_influencing_factors": [
    {
      "factor": "3-Month Price Average",
      "importance": "<float>",
      "impact": "<Very High | High | Medium | Low | Minimal>",
      "description": "..."
    }
  ],
  "explanation_summary": "...",
  "confidence_factors": {
    "confidence_pct": "<float>",
    "dominant_feature": "previous_month_price (naive forecast)",
    "fields_estimated": [],
    "data_quality": "good",
    "prediction_reliability": "good"
  },
  "model_used": "naive_last",
  "fallback_reason": null
}
```

| Field | Type | Notes |
|-------|------|-------|
| `top_influencing_factors[].factor` | string | One of: `3-Month Price Average`, `Previous Month Price`, `Price Volatility`, `Seasonality`, `Market Location`, `Other` (`Other` only if non-zero) |
| `top_influencing_factors[].importance` | float | Sum of the Random Forest's `feature_importances_` for the factor's columns, 4 decimals |
| `top_influencing_factors[].impact` | string | From importance: `Very High` (≥ 0.5), `High` (≥ 0.2), `Medium` (≥ 0.05), `Low` (≥ 0.01), else `Minimal` |
| `confidence_factors.fields_estimated` | list | Price-history fields that were not supplied |
| `confidence_factors.data_quality` | string | `"good"` or `"degraded"` (degraded when any field is estimated) |
| `confidence_factors.prediction_reliability` | string | `"good"` or `"limited"` |
| `model_used` | string | `naive_last` for the current champion |
| `fallback_reason` | string or null | As for `/predict` |

If the Random Forest artifact cannot be loaded, `top_influencing_factors` is `[]`.

---

## Feedback

**Endpoint:** `POST /feedback`
**Purpose:** Store farmer feedback in SQLite (`FEEDBACK_DB_PATH`, default `price_api/data/feedback.db`). Stored rows feed `/impact-stats`.

### Request

```json
{
  "user_id": "farmer123",
  "prediction_id": "pred_001",
  "predicted_price": 65.5,
  "actual_price": 67.0,
  "accuracy_rating": 4,
  "usefulness_rating": 5,
  "comments": "Very helpful prediction!",
  "timestamp": "2025-12-06T10:30:00"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `user_id` | string | ❌ | Used for the distinct-user count |
| `prediction_id` | string | ❌ | Stored as given |
| `predicted_price` | float | ❌ | Used with `actual_price` for MAPE in `/impact-stats` |
| `actual_price` | float | ❌ | Used with `predicted_price` for MAPE. Rows with `actual_price` ≤ 0 are skipped for MAPE |
| `accuracy_rating` | int | ❌ | 1–5 (1=wrong, 5=perfect). Other values return 400 |
| `usefulness_rating` | int | ❌ | 1–5 (1=useless, 5=very helpful). Other values return 400 |
| `comments` | string | ❌ | Stored as given |
| `timestamp` | string | ❌ | Client timestamp, stored as `client_timestamp`. The server also stores its own UTC `created_at` |

### Response

```json
{
  "feedback_id": "FB-<8 hex characters>",
  "status": "success",
  "message": "Thank you for your feedback! Your input helps us improve our predictions.",
  "timestamp": "<client timestamp, or server time if none was sent>"
}
```

If the row cannot be saved, the API returns 503 and does not report success.

---

## Impact Statistics

**Endpoint:** `GET /impact-stats`
**Purpose:** Aggregates computed from stored feedback. Fields with no supporting data are `null`.

### Response

Shape shown for an empty feedback store:

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

| Field | Type | Notes |
|-------|------|-------|
| `total_predictions` | null | Always `null`. Predictions are not logged |
| `total_users` | int | Distinct `user_id` values in feedback |
| `average_accuracy` | float or null | `max(0, 1 − MAPE)`, 0–1 scale, over rows with `predicted_price` and `actual_price` > 0 |
| `total_markets_covered` | int | Number of entries in the market coordinates table (40 in the current code) |
| `commodities_tracked` | list | Sorted commodity names accepted by `/predict` |
| `user_satisfaction` | float or null | Mean `usefulness_rating` |
| `cost_savings_estimate` | null | Always `null`. Not measurable |
| `last_updated` | string | Latest stored feedback time, or the current time if none |
| `feedback_count` | int | Number of stored feedback rows |
| `average_accuracy_rating` | float or null | Mean `accuracy_rating` |
| `mape_pct` | float or null | Mean absolute percentage error in percent, 2 decimals |
| `price_comparisons` | int | Rows used for the MAPE |

---

## Health

**Endpoint:** `GET /health`
**Purpose:** Service and champion status

### Response

```json
{
  "status": "ok",
  "champion_model_type": "naive_last",
  "champion_source": "file",
  "data_through": "2026-10",
  "champion_load_problem": null,
  "rf_model_loaded": true,
  "rf_load_problem": null,
  "feedback_db_writable": true,
  "api_version": "2.0.0",
  "champion_schema_version": 2
}
```

| Field | Type | Notes |
|-------|------|-------|
| `status` | string | `"ok"` or `"degraded"`. Degraded if the champion failed to load, the feedback path is not writable, or a Random Forest champion did not load |
| `champion_source` | string | `"file"` when `champion.json` loaded, `"default"` otherwise |
| `data_through` | string | `YYYY-MM` the champion was built from |
| `champion_load_problem` | string or null | Reason the default champion was used |
| `rf_model_loaded` | bool | Loads the Random Forest on first call |
| `rf_load_problem` | string or null | Reason the Random Forest did not load |
| `feedback_db_writable` | bool | Whether the feedback path is writable (nothing is written by the check) |
| `api_version` | string | `"2.0.0"` |
| `champion_schema_version` | int | `2` (`1` for older files) |

---

## Error Responses

All endpoints return errors as JSON with a `detail` field:

```json
{
  "detail": "Commodity 'mango' not supported. Allowed: ['banana', 'bananas', 'cabbage', 'kale', 'onion', 'onions', 'potato', 'potatoes', 'tomato', 'tomatoes']"
}
```

**Common Status Codes:**
- `200` — Success
- `400` — Bad request (unsupported commodity, unknown market, invalid date or rating, non-positive price)
- `422` — Validation error (missing or malformed request fields)
- `503` — Service unavailable (feedback could not be saved, or statistics could not be read)

---

## Integration Checklist (for Node.js/Twilio)

- [ ] Parse Twilio form-encoded webhook (`From`, `Body`)
- [ ] Transform to `/webhook` JSON: `{ "from_number": from, "body": body }`
- [ ] Handle response, check `sms` field (send directly) vs `intent` field (route to C++)
- [ ] For PRICE_QUERY: call `/predict` with `previous_month_price` from C++, then `/recommendations` with the `/predict` result
- [ ] Format reply with `/format` endpoint if needed
- [ ] Send SMS via Twilio

---

**Questions?** Check the [API_ENDPOINTS.md](price_api/API_ENDPOINTS.md) or [README.md](README.md) for more details.
