# Kenya crop price forecaster

A prototype API that forecasts next month's market price for six crops in Kenya, using real WFP market price data.

**Live demo (no setup):** http://allanbisonga.me/kenya-price-forecaster-v2/
The demo is a static snapshot: 35 forecasts for November 2026 from 342 price series in 177 markets, produced by this API's own code.

## Status: prototype

- The served model is `naive_last`: next month's price equals the latest observed price. In a walk-forward test over 1,508 predictions, none of six machine-learning variants (Random Forest and gradient boosting) beat it ([RETRAINING_RESEARCH.md](RETRAINING_RESEARCH.md)), so the legacy Random Forest is not used for forecasts. Latest backtest of the served model: 2,021 forecasts over 60 months, median absolute error 9.7%, mean 15.6% (`price_api/artifacts/champion.json`).
- Forecast ranges are the 10th to 90th percentile of past forecast errors for each crop and price type.
- Crops: tomatoes, onions, potatoes, kale, cabbage, bananas. Prices are in the unit WFP records (for example a 64 kg crate of wholesale tomatoes); On the `integrate/ui-swarm` branch, `/predict` also reports that unit and a per-kg value where the unit has a fixed weight.
- Data: WFP VAM food prices for Kenya (HDX), monthly, through October 2026.
- Not built yet: the Twilio/WhatsApp adapter, user sessions, live data feeds, production hardening.

## Endpoints

| Endpoint | What it does |
|---|---|
| `POST /predict` | Next-month forecast with an 80% range and a plausibility flag |
| `POST /micro-market` | Markets within a radius, with the latest WFP price if it is at most 3 months old |
| `POST /recommendations` | SELL / HOLD / MONITOR from rules; message text from Gemini, with a template fallback |
| `POST /webhook` | Classifies a farmer's WhatsApp text (English or Swahili) into an intent; rules first, Gemini fallback |
| `POST /format` | Formats a forecast as SMS/WhatsApp text |
| `POST /explainability` | Feature importances of the legacy Random Forest (not a per-prediction explanation) |
| `POST /feedback`, `GET /impact-stats` | Stores farmer feedback in SQLite and summarises it |
| `GET /health` | Model and data status |

Request and response shapes: [SCHEMAS.md](SCHEMAS.md) and [price_api/API_ENDPOINTS.md](price_api/API_ENDPOINTS.md).

## Run it locally

```bash
git clone https://github.com/AllanOnyonka-ltsm/kenya-price-forecaster-v2.git
cd kenya-price-forecaster-v2/price_api
pip install -r requirements.txt
uvicorn app:app --reload
```

Then open http://localhost:8000/docs. Run from `price_api/`, because model paths are relative.

`GEMINI_API_KEY` is optional. Without it, `/webhook` uses rules only and `/recommendations` uses templates.

Example:

```bash
curl -X POST http://localhost:8000/predict -H "Content-Type: application/json" -d '{
  "date": "2026-11-15", "admin1": "North Eastern", "market": "Hagadera (Daadab)",
  "commodity": "potatoes", "pricetype": "Retail", "previous_month_price": 80.0}'
```

## Tests

```bash
cd price_api
python -m unittest discover -p "test_*_unit.py"
```

The retraining pipeline has its own tests; see [pipeline/README.md](pipeline/README.md).

## Repository layout

- `price_api/`: the FastAPI app, predictor, data lookups and unit tests
- `price_api/artifacts/`: served model config (`champion.json`), latest prices, legacy Random Forest
- `pipeline/`: draft monthly retraining pipeline that proposes a new `champion.json`
- [RETRAINING_RESEARCH.md](RETRAINING_RESEARCH.md): why the naive model is served, with backtest details
- [INTEGRATION_GUIDE.md](INTEGRATION_GUIDE.md): how a Node.js/Twilio layer would call the API

The `integrate/ui-swarm` branch, still in progress, adds a React web UI, an experimental multi-agent forecasting "swarm" (it currently loses to the naive baseline), the demo page source in `demo/`, and a research report on improvements.

## Deploying

The API runs under gunicorn with the uvicorn worker, started from `price_api/`:

```bash
gunicorn -w 1 -k uvicorn.workers.UvicornWorker app:app --bind 0.0.0.0:$PORT
```

`/feedback` writes SQLite to `FEEDBACK_DB_PATH` (default `price_api/data/feedback.db`). On hosts with an ephemeral disk, such as Render's free plan, stored feedback is lost on every restart.

## How this was built

Parts were AI assisted.

## License

No license file yet. All rights reserved until one is added.
