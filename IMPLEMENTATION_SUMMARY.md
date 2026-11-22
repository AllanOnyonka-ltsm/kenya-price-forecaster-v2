# Implementation Summary

This describes what the API does today (`price_api/app.py`). It replaces an earlier summary of the first endpoint refactor. That summary described placeholder logic and hard-coded statistics, which have been removed.

## Endpoints

| Method | Path | Current behaviour |
|---|---|---|
| GET | `/` | Service name, endpoint list, supported commodities and markets (read from code). |
| POST | `/predict` | Forecast from the champion in `price_api/artifacts/champion.json`, with an 80% band. See "Forecast and bands" below. |
| GET | `/predict` | Returns a JSON hint to use POST. It is not a forecast. |
| POST | `/webhook` | Classifies a farmer's text message. Rules run first. Gemini is the fallback when no rule matches; `via_llm` in the response says which was used. |
| POST | `/recommendations` | Returns `sell`, `hold` or `monitor` with urgency, trend and reasons, from the predicted and previous price, the interval and the confidence. `farmer_message` is written by Gemini (`gemini_client.py`). If Gemini returns nothing, a fixed template is used. |
| POST | `/micro-market` | Lists nearby markets within a radius (default 50 km, at most 5) by straight-line (haversine) distance between WFP market coordinates. Each market gets its latest WFP price (`estimated_price`, plus `price_per_kg` for KG units, `price_date`, `price_unit`, `price_pricetype`) only when that observation is at most 3 months before the price file's `data_through`; otherwise `null` with a `price_note`. Source: `artifacts/latest_prices.json` from `pipeline/latest_prices.py`. |
| POST | `/format` | Builds sms, whatsapp or bulletin text from a `prediction_data` object. |
| POST | `/explainability` | Lists the RandomForest's global feature importances, grouped into factors. These are **not** per-prediction attributions. When the champion is `naive_last`, the forecast is last month's price and the factor list is for reference only. |
| GET | `/health` | `status` is `ok` or `degraded`. Also reports the champion type, source and `data_through`, whether the RandomForest loaded, and whether the feedback database is writable. |
| POST | `/feedback` | Saves one feedback record to SQLite. Ratings outside 1 to 5 return 400. A save failure returns 503, and nothing is reported as stored. |
| GET | `/impact-stats` | Computed from stored feedback. Fields with no supporting data are `null`. See "Feedback and impact stats" below. |

## Forecast and bands

- **Champion.** `price_api/artifacts/champion.json` (schema version 2; schema 1 files still load) names the model that serves `/predict`. The file in this repo sets `model_type` to `naive_last`, so the forecast is the previous month's price. Its `data_through` is `2026-10`.
- **Interval.** For each crop the file has an empirical band, `error_bands`, with ratios `ratio_p10`, `ratio_p50` and `ratio_p90`. The 80% range is `[predicted x ratio_p10, predicted x ratio_p90]`. Schema 2 adds `error_bands_by_pricetype` (per crop and price type, groups with at least 30 backtest rows; the current file has Wholesale bands for most crops and Retail bands for Bananas and Potatoes (Irish)). Lookup order: crop + `pricetype` from the request, then crop, then `_ALL`, then default; `/predict` reports the level used in `band_source`. A crop with no band of its own uses `_ALL`. If the champion file is missing or invalid, the API uses a built-in default band (0.75 to 1.35) and reports the reason in `/health`.
- **Fallback.** If the champion is a RandomForest and it fails to load or returns a bad value, the API uses the naive forecast and sets `fallback_reason`. Responses include `model_used` so callers can see which model answered.
- **Confidence.** `confidence_from_band` in `predictor.py`: 100 times (1 minus half the interval width relative to the forecast), clamped to 0 to 100.
- **Backtest recorded in the file.** A 60-month walk-forward test with n = 2,021 predictions. Naive MAPE is 15.6%, median APE is 9.7%. The file does not record a RandomForest MAPE. [`RETRAINING_RESEARCH.md`](RETRAINING_RESEARCH.md) reports a different subset (Wholesale only, 1,508 predictions, naive 17.39%, best machine-learning variant 18.55%), so the two sets of numbers should not be mixed.

The API serves forecasts for six crops only: tomatoes, onions (dry), potatoes (Irish), kale, cabbage and bananas. Alias names such as `tomato` or `banana` are accepted. The list in `GET /` is the source of truth.

## Feedback and impact stats

- **Storage.** `/feedback` writes to SQLite at the path in the environment variable `FEEDBACK_DB_PATH`. If that variable is not set, the default is `price_api/data/feedback.db`. On hosts with ephemeral disks, point the variable at a persistent disk.
- **`/impact-stats` fields.**
  - `total_users`: distinct `user_id` values in feedback.
  - `average_accuracy`: 1 minus the mean absolute percentage error, from feedback rows that have both `predicted_price` and `actual_price`. Null when there are no such rows.
  - `user_satisfaction`: mean `usefulness_rating`. Null when none is given.
  - `total_predictions` and `cost_savings_estimate`: always null. Predictions are not logged, and cost savings cannot be measured.
  - Extra fields: `feedback_count`, `average_accuracy_rating`, `mape_pct`, `price_comparisons`.
- **Removed.** The earlier hard-coded values (average accuracy, prediction counts, user counts, satisfaction and cost savings) are gone from the API. Do not quote them.

## Explainability

`/explainability` reads the RandomForest's global importances (`feature_importance.py`). It groups them into five factors plus "Other", and it labels each factor's importance. The explanation text is generic. It is not a SHAP value and not an attribution for a single prediction.

The text for the RandomForest path in `app.py` includes a fixed "about 91%" figure. That figure is not computed at request time, but it matches the model's real importance for `price_ma_3` (0.9074, see [`RETRAINING_RESEARCH.md`](RETRAINING_RESEARCH.md)).

## Why the forecast is naive

The RandomForest was trained with target leakage, so its earlier accuracy claims were wrong. In an honest walk-forward test, last month's price beat every machine-learning variant tested. The evidence and method are in [`RETRAINING_RESEARCH.md`](RETRAINING_RESEARCH.md), section 1 and section 3.

## Monthly retraining

[`pipeline/README.md`](pipeline/README.md) describes the monthly job. The workflow is `.github/workflows/retrain.yml`, scheduled for the 6th of each month at 06:00 UTC, with a manual dry-run option.

- It validates the new WFP snapshot against the last accepted one.
- It backtests a challenger and writes `champion.json`, which is always `naive_last`, with refreshed error bands.
- It never auto-promotes a RandomForest. A winning RandomForest goes to `candidate/` with a human-review flag.

The pipeline README marks the pipeline as draft. Check its status there before relying on it.

## Known limits

- Forecasts are based only on the previous month's price in the default champion. The API has no weather, fuel, exchange-rate or harvest inputs.
- `/micro-market` prices are WFP's latest observations, not forecasts. Many markets have no observation within 3 months, so their price is `null`.
- The champion path is relative (`artifacts/champion.json`). Run `uvicorn app:app` from inside `price_api/`. From any other directory, the API loads the default champion and `/health` reports `degraded`.
- `/recommendations` depends on Gemini for its message text. The rules for `action` and `urgency` do not depend on Gemini.

## Testing

See [`TEST_VALIDATION_REPORT.md`](TEST_VALIDATION_REPORT.md) for the current suites, commands and counts. In short:

```bash
cd price_api
python -m unittest test_geo_unit test_predictor_unit test_feedback_store_unit test_feature_importance_unit test_intent_router_unit

cd ..
python -m unittest discover -s pipeline -t pipeline
```

## Documentation

- [`price_api/API_ENDPOINTS.md`](price_api/API_ENDPOINTS.md) and [`SCHEMAS.md`](SCHEMAS.md) describe request and response shapes.
- [`README.md`](README.md) is the project overview.
- The FastAPI interactive docs at `/docs` are generated from the code.

## Open items

- Per-prediction explanations (for example SHAP values) are not implemented.
- Real prices for other markets are not in the API.
- Promoting a RandomForest needs the feature builder updated to match its inputs, and a human review. See [`pipeline/README.md`](pipeline/README.md), "Promoting a challenger by hand".
- Feedback is stored but not read by the retraining pipeline.
