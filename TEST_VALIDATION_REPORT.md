# Test Validation Report

**Last re-run:** 2026-11-22 (counts below were re-run for this document; see the commands in section 1).
**Earlier report:** the 2025-12-05 "12 of 12 passing" report is withdrawn. See section 4.

## 1. Current automated tests

| Suite | Where | Command (run from) | Result on 2026-11-22 |
|---|---|---|---|
| API unit tests | `price_api/test_*_unit.py` (7 modules) | `price_api`: `python -m unittest discover -s . -t . -p "test_*_unit.py"` | 140 tests, OK |
| Pipeline unit tests | `pipeline/test_pipeline_unit.py`, `pipeline/test_latest_prices_unit.py` | repo root: `python -m unittest discover -s pipeline -t pipeline` | 51 tests, OK (1 skipped unless `PRICE_API_APP` is set) |

Breakdown of the API unit tests by module: `test_predictor_unit` 59, `test_geo_unit` 19, `test_intent_router_unit` 19, `test_latest_prices_unit` 18, `test_feedback_store_unit` 9, `test_feature_importance_unit` 9, `test_coord_overrides_unit` 7. Pipeline: `test_pipeline_unit` 39, `test_latest_prices_unit` 12. Both suites also run in CI (`.github/workflows/tests.yml`) on every push and pull request.

Notes:

- The API unit suites import their modules directly (for example `predictor`, `geo`, `feedback_store`). They do not start the FastAPI server. The feedback tests use temporary directories, not `price_api/data/feedback.db`.
- The pipeline suite uses synthetic data and makes no network calls (see [`pipeline/README.md`](pipeline/README.md)). Its one skipped test is a cross-check of the canonical commodity names against `price_api/app.py`. It runs only when the environment variable `PRICE_API_APP` points at `price_api/app.py`. With that variable set, all 32 tests pass.
- Pipeline test runs use about 30 seconds on the machine used for this check.

## 2. Manual HTTP scripts (not part of the suites above)

These scripts send requests to a running server at `http://localhost:8000`. They were **not run** for this document, so no results are recorded here.

- `price_api/test_endpoints.py`: HTTP checks of the endpoints. Needs a running server.
- `price_api/test_gemini_recommendations.py`: checks `/recommendations` and `/webhook`. Needs a running server, and a `GEMINI_API_KEY` for the Gemini-backed paths. Without the key, the code falls back (see [`IMPLEMENTATION_SUMMARY.md`](IMPLEMENTATION_SUMMARY.md)).

## 3. Not covered by the automated suites

- Live HTTP behaviour of each endpoint, including status codes and response bodies.
- The real Gemini API responses (`/recommendations` message text and the `/webhook` LLM fallback).
- Loading the real RandomForest file (`random_forest_price_model.pkl`) for `/explainability`. No unit test loads it.
- Model accuracy. Accuracy is a separate question, covered by the backtest in [`RETRAINING_RESEARCH.md`](RETRAINING_RESEARCH.md), not by these tests.

## 4. Withdrawn: earlier report (2025-12-05)

The earlier report claimed 12 of 12 checks passing, with sample responses and "fully validated" conclusions. It is withdrawn for these reasons:

- It was written for the first version of the endpoints. Several of those endpoints have since changed (for example `/micro-market` no longer estimates prices, and `/explainability` now reports the RandomForest's importances).
- Its `/impact-stats` sample contained hard-coded values (prediction and user counts, average accuracy, satisfaction, cost savings). Those values were never measured and have been removed from the API. They are not reproduced here.
- Its "0 security vulnerabilities" and "deterministic behaviour" claims were not re-checked for the current code.
- Its test runner, `test_endpoints.py`, is a manual script (section 2), not an automated suite.

## 5. Related documents

- [`RETRAINING_RESEARCH.md`](RETRAINING_RESEARCH.md): backtest method and results, including the target-leakage finding.
- [`pipeline/README.md`](pipeline/README.md): the monthly retraining pipeline and its exit codes.
- [`IMPLEMENTATION_SUMMARY.md`](IMPLEMENTATION_SUMMARY.md): what each endpoint does now.
- [`INTEGRATION_GUIDE.md`](INTEGRATION_GUIDE.md): integration notes for the webhook flow.
