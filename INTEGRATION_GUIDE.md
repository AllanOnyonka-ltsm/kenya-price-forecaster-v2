# Integration Guide

Notes for wiring the Twilio / Node.js side to the Python API. Request and response shapes are in [`SCHEMAS.md`](SCHEMAS.md). This guide describes the behaviour of the current code.

---

## TL;DR

The endpoints below are implemented. They have not been load-tested, and the Twilio to Node.js to Python path has not been verified end to end in this repo. Treat this as the starting point for integration testing.

The Python API provides:
- Intent parsing (`/webhook`): rule-based first, Gemini as a fallback
- Price forecasts (`/predict`): the champion model in `price_api/artifacts/champion.json`, with an 80% range per crop
- Recommendations (`/recommendations`): a sell, hold or monitor decision, with a message written by Gemini or a fixed template
- Formatting helpers (`/format`) for SMS, WhatsApp and bulletin text

---

## What Is on This Branch

### 1. Intent parser (`/webhook`)
- **Input:** `{ "from_number": str, "body": str }`, the farmer's WhatsApp message.
- **Output:** `{ "intent": str, "symbol": str|null, "quantity": float|null, "unit": str|null, "via_llm": bool, "sms": str|null }`
- **Logic:** rule-based parse first. If no rule matches, a Gemini call classifies the text (`via_llm: true`). If Gemini gives no usable answer, the intent is `UNKNOWN`.
- **Intents:** `GREETING`, `PRICE_QUERY`, `SELL_ORDER`, `BUY_ORDER`, `UNKNOWN`.
- **Latency:** not measured. The rule path makes no network call. The Gemini path does.

Example (`from_number` is a placeholder):
```bash
curl -X POST http://localhost:8000/webhook \
  -H "Content-Type: application/json" \
  -d '{"from_number": "+254<number>", "body": "price of mahindi"}'
```

Response shape (values depend on the message):
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

### 2. Recommendations (`/recommendations`)
- **Input:** the commodity, market, admin1, pricetype, `predicted_price`, `previous_price`, `lower_bound`, `upper_bound`, `confidence_pct` and `unreasonable`. The last four come from a `/predict` response. Check `SCHEMAS.md` for the exact list.
- **Output:** `action` (`sell`, `hold` or `monitor`), `urgency`, `price_trend`, `trend_pct`, `model_confidence`, `reasons`, `rationale` and `farmer_message`.
- **`farmer_message`:** written by Gemini, with emoji and some Swahili. If Gemini returns nothing, the API uses a fixed template of the form `📊 <crop> @ <market>: KES <price>/kg forecast. Trend: <pct>%. Recommendation: *<ACTION>*.`
- The `action` and `urgency` values come from fixed rules in code, not from Gemini.

### 3. Forecast (`/predict`)
- **Input:** `date`, `admin1`, `market`, `commodity`, `pricetype`, `previous_month_price`. Price-history fields are optional.
- **Output:** `prediction_per_kg`, `lower_bound`, `upper_bound`, `confidence_pct`, `model_used`, `fallback_reason` and related fields.
- **Supported crops:** the six tracked crops only (tomatoes, onions, potatoes, kale, cabbage, bananas). `GET /` lists them. Maize, beans and the other crops in the table further down are **not** supported by `/predict`.
- With the current champion (`naive_last`), the forecast equals `previous_month_price`. The interval is the crop's historical error band. See [`IMPLEMENTATION_SUMMARY.md`](IMPLEMENTATION_SUMMARY.md) for details.

### 4. Formatting (`/format`)
- **Input:** `prediction_data` (the `/predict` output, or a subset of it) and `format_type` (`sms`, `whatsapp` or `bulletin`).
- **Output:** `formatted_message`, `character_count` and `estimated_cost` (set in code: a fixed value for SMS, 0 for WhatsApp, null for bulletin).

### 5. Feedback and stats (`/feedback`, `/impact-stats`)
- `/feedback` is saved to SQLite at `FEEDBACK_DB_PATH` (see Setup).
- `/impact-stats` is computed from saved feedback. Fields without data are `null`.

---

## Integration Flow (for Node.js)

```
Twilio Webhook (WhatsApp message)
    ↓
Parse form-encoded: From, Body, WaId, etc.
    ↓
POST to Python /webhook
  Input:  { "from_number": "+254<number>", "body": "<farmer text>" }
  Output: { "intent": "PRICE_QUERY", "symbol": "...", "sms": null, ... }
    ↓
If sms is set → send it to the farmer via Twilio
If intent is PRICE_QUERY → continue below
    ↓
Live price lookup (outside this API)
    ↓
POST to Python /predict
  Input:  { "date": "YYYY-MM-DD", "admin1": "...", "market": "...", "commodity": "tomatoes",
            "pricetype": "...", "previous_month_price": <float> }
  Output: { "prediction_per_kg": <float>, "lower_bound": <float>, "upper_bound": <float>,
            "confidence_pct": <float>, "unreasonable": <bool>, "model_used": "naive_last", ... }
    ↓
POST to Python /recommendations
  Input:  the /predict values above, plus the previous price and the fields listed in SCHEMAS.md
  Output: { "action": "...", "farmer_message": "...", ... }
    ↓
Send farmer_message to the farmer via Twilio
```

Replace the placeholders with real values. The `commodity` in the example is one of the six supported crops.

---

## Key Files

| File | Purpose |
|------|---------|
| [`price_api/app.py`](price_api/app.py) | Main FastAPI app and all endpoints |
| [`price_api/intent_router.py`](price_api/intent_router.py) | Intent parsing (rules, then Gemini) |
| [`price_api/enhanced_endpoints.py`](price_api/enhanced_endpoints.py) | Recommendation rules and message call |
| [`price_api/gemini_client.py`](price_api/gemini_client.py) | Gemini API helper |
| [`price_api/predictor.py`](price_api/predictor.py) | Champion loading, forecast and bands |
| [`price_api/feedback_store.py`](price_api/feedback_store.py) | Feedback storage and stats |
| [`SCHEMAS.md`](SCHEMAS.md) | Request and response shapes |
| [`price_api/test_gemini_recommendations.py`](price_api/test_gemini_recommendations.py) | Manual script for `/webhook` and `/recommendations` (needs a running server) |

---

## Setup (Python Side)

```bash
cd price_api
pip install -r requirements.txt

# Required for the Gemini-backed paths (otherwise the fallbacks are used)
export GEMINI_API_KEY="your-gemini-api-key"

# Optional: where /feedback is stored (default: price_api/data/feedback.db)
export FEEDBACK_DB_PATH="/path/to/feedback.db"

# Start the server. Run from price_api/ so artifacts/champion.json is found.
uvicorn app:app --reload
```

Interactive docs: http://localhost:8000/docs. Check `GET /health` to confirm the champion loaded and the feedback database is writable.

---

## Supported Crops

**Intent parser (`/webhook`) symbols:**

| English | Swahili | Symbol |
|---|---|---|
| Maize | Mahindi | `MAIZE` |
| Tomatoes | Nyanya | `TOMATO` |
| Potatoes | Viazi | `POTATO` |
| Beans | Maharagwe | `BEANS` |
| Wheat | Ngano | `WHEAT` |
| Rice | Mchele / Wali | `RICE` |
| Sorghum | Mtama | `SORGHUM` |
| Onions | Vitunguu | `ONION` |
| Cassava | Muhogo | `CASSAVA` |

These symbols are for `/webhook` only. The forecasting endpoints (`/predict`, `/recommendations`, `/micro-market`) accept only the six tracked crops listed in `GET /`.

---

## Next Steps (for Node.js Integration)

1. Read [`SCHEMAS.md`](SCHEMAS.md) for the request and response specs.
2. Parse the Twilio webhook. Extract `From`, `Body` and `WaId`.
3. Call `POST /webhook` with the body.
4. Route on the response: if `sms` is set, send it. If `intent` is `PRICE_QUERY`, go to the forecast path.
5. For the forecast path: get a live price from your data source, call `/predict`, then `/recommendations`, and send `farmer_message`.

---

## Testing

The manual script needs a running server and `GEMINI_API_KEY`:

```bash
cd price_api
python test_gemini_recommendations.py
```

It checks `/recommendations` (including the Gemini message) and `/webhook`. No results are recorded in this guide. See [`TEST_VALIDATION_REPORT.md`](TEST_VALIDATION_REPORT.md) for the automated suites, which do not start the server.

---

## Open Items

1. **Bilingual SMS replies.** Translate the menu and prompts to Swahili. Not done.
2. **Twilio adapter.** Form-encoded to JSON and TwiML replies are not in this repo.
3. **Farmer session memory.** Not implemented.
4. **Live market prices.** The API does not fetch live prices. The caller supplies `previous_month_price`. The monthly pipeline ([`pipeline/README.md`](pipeline/README.md)) refreshes the error bands from WFP data, not the live price.
5. **Feedback for retraining.** Feedback is stored but not used by the pipeline.
