# Monthly retrain pipeline (price forecaster)

Status: **draft**. Nothing here is wired to `price_api/` yet. The workflow only opens a PR, and the
API keeps reading `champion.json` in the same format as before.

## What it does

Once a month the pipeline:

1. Gets the WFP Kenya food prices CSV (local `--csv`, or downloaded from HDX).
2. Validates it against the last accepted snapshot. Problems include: missing columns, row count
   down more than 5%, max date going backwards, non-numeric or negative prices, tracked-commodity
   share collapsing. Duplicates are reported as warnings.
3. Builds leak-free features (`features.py`). Each feature at month t uses only prices from months
   before t, and the series key is (commodity, market, unit, pricetype), as in `backtest.py`.
4. Runs a walk-forward backtest over the last `--window-months` (default 60; short windows gave noisy bands). The challenger
   `rf_logratio` is retrained every 3 months. The champion is `naive_last` (next month = last month).
5. Computes a paired, month-clustered bootstrap 95% CI for MAPE(challenger) minus MAPE(champion).
6. Writes `champion.json` (always `naive_last`, with refreshed error bands) and `report.md` /
   `metrics.json`.

**Promotion rule:** the challenger wins only if the CI upper bound is below 0. **A random forest is
never auto-promoted.** If it wins, the pipeline writes `candidate/champion_candidate.json` and
`candidate/random_forest_price_model.pkl` and puts `HUMAN REVIEW: challenger beat champion` in the
report. The current API feature builder was written for the old model, so a promoted RF would
silently mismatch it.

### Exit codes

| code | meaning | repo / champion.json |
|---|---|---|
| 0 | updated: champion.json written | copied into `price_api/artifacts/` by the workflow, PR opened |
| 1 | nothing new: data_through is not newer than the previous champion | nothing written |
| 2 | validation failed | untouched; report explains why |
| 3 | unexpected error | untouched; report still written with traceback |

Self-healing means a bad snapshot never replaces a good artifact. Exit 2 and exit 3 fail the
workflow run after the report is uploaded, so someone sees them.

## Files

- `pipeline/retrain.py`: CLI, backtest, bootstrap, bands, champion contract, exit codes, download.
- `pipeline/validate.py`: `validate_snapshot(new, prev)` returns `{ok, problems, warnings, stats}`.
- `pipeline/features.py`: leak-free features, canonical commodity mapping.
- `pipeline/test_pipeline_unit.py`: unit and CLI exit-code tests (synthetic data, no network).
- `pipeline/requirements.txt`: pinned to the versions tested.
- `.github/workflows/retrain.yml`: monthly schedule and manual dispatch.

## Run locally

From the repo root (Python 3.12):

```
pip install -r pipeline/requirements.txt

# Tests (no network; also cross-checks canonical names against the real app.py)
set PRICE_API_APP=price_api\app.py          # Windows; use export on bash
python -m unittest discover -s pipeline -t pipeline -v

# Retrain from a local CSV (first run: no previous snapshot)
python pipeline/retrain.py --csv path\to\wfp_food_prices_ken.csv --out out --window-months 60

# Normal monthly form: validate against the last accepted snapshot, then compare with the champion
python pipeline/retrain.py --previous-snapshot data\wfp_snapshot.csv.gz ^
    --previous-champion price_api\artifacts\champion.json --out out --window-months 60

# Download from HDX instead of --csv; --dry-run never writes champion.json or candidate files
python pipeline/retrain.py --out out --dry-run
```

`out/` holds `report.md`, `metrics.json`, and when exit is 0: `champion.json`,
`champion.previous.json` (when `--previous-champion` is given), `wfp_snapshot.csv.gz`, and
`candidate/` if the challenger won.

## One-time GitHub setup

1. **Actions enabled** for the repo (Settings → Actions → General → Actions permissions).
2. **Allow PR creation:** Settings → Actions → General → Workflow permissions →
   tick **"Allow GitHub Actions to create and approve pull requests"**. Without this `gh pr create`
   fails with a permissions error even though the workflow asks for `pull-requests: write`.
3. Workflow permissions: read/write is fine, since the workflow also declares `contents: write`
   and `pull-requests: write` explicitly.
4. Optional: branch protection on `main` (require PR review). The workflow only pushes
   `auto/retrain-YYYY-MM` branches and never pushes to `main`.
5. Test it with Actions → "Monthly price-model retrain" → Run workflow, `dry_run` = true first.

## Snapshot storage (tradeoff)

The pipeline compares each new download with the last accepted snapshot, so that file has to
persist between runs. Options:

- **Committed `data/wfp_snapshot.csv.gz` (chosen).** Versioned, auditable, survives forever. The
  gzipped file is about 250 KB (measured on the local file). Each accepted update adds one version
  to git history, roughly 3 MB a year, which is fine for a monthly job.
- `actions/cache`: rejected. Caches are evicted after 7 days without access, and this job runs
  once a month, so the snapshot would be lost almost every run.
- Workflow artifacts: rejected. Cross-workflow download is fiddly and artifacts expire.

The snapshot only moves forward when the PR is merged. If a PR is left unmerged, next month's
run compares against main's snapshot, which is still correct.

## Cadence

Monthly (6th, 06:00 UTC), after WFP's usual update. The challenger retrains every 3 months inside
the backtest (`--retrain-every 3`, allowed range 3 to 6). Runtime is about 9 seconds on the local
file with the 60-month default window.

## Commodity mapping

Canonical names are the `COMMODITY_META` values in `price_api/app.py`: Tomatoes, Onions (dry),
Potatoes (Irish), Kale, Cabbage, Bananas. WFP names map one-to-one, except:

- `Potatoes (Irish, red)` and `Potatoes (Irish, white)` are merged into `Potatoes (Irish)`.
- `Onions (red)` is **not** merged. It is a different product, and it is not tracked by the API.
- Everything else (maize, beans, milk, and so on) is dropped. The bands and metrics cover only the
  six tracked commodities, which keeps `_ALL` aligned with what the API serves.

Duplicate rows that the merge creates are collapsed to a median price per (series, month).

## Rollback

`price_api/artifacts/champion.previous.json` is the champion from before the last accepted update.
To roll back, copy it over `champion.json` and open a PR. Nothing else needs to change, since the
champion contract is the same for both files.

## Promoting a challenger by hand

Only do this after the API's feature builder is updated to match the RF inputs.

1. Download the `retrain-out-<run_id>` artifact from the workflow run and take `candidate/`.
2. Check `candidate/champion_candidate.json` (`model_type` `random_forest`, `model_file`
   `random_forest_price_model.pkl`) and the bootstrap block in `report.md`.
3. Copy `random_forest_price_model.pkl` to `price_api/artifacts/`. It is a joblib dict:
   `model`, `encoders`, `columns` (`features.RATIO_COLUMNS`), `target` `log(price/lag_1)`. The
   prediction is `lag_1 * exp(model.predict(X))`.
4. Copy `champion_candidate.json` over `champion.json`, then change the API to build the ratio
   features (`features.ratio_features`) and to apply `lag_1 * exp(...)`.
5. Keep the previous champion as `champion.previous.json`.

## Caveats

- **Price flags.** Rows with `priceflag` `actual` or `aggregate` are used (KES only). `actual,aggregate`
  and other values are excluded. If a (series, month) has both, `aggregate` is dropped and the
  monthly value is the median of the `actual` rows. Counts of rows used per flag are in
  `report.md` under the data summary. The current file has no same-month overlap for the tracked
  crops, so the preference rule does not fire on it today; a unit test covers it.
- **Evaluation set size.** A 60-month window gives about 2,000 test rows across the six tracked
  commodities. The bands are still a snapshot of recent behaviour, not a guarantee.
- **Pricetype is part of the series key**, so Retail and Wholesale are never mixed in one series.
  The report shows n and MAPE per pricetype. Wholesale rows dominate the test set.
- **Bands pool units and pricetypes.** Ratios are scale-free, so this is intended. It does not
  separate Retail from Wholesale.
- **Encoders are refit on the current panel** (vocabulary only). The old artifacts' encoders are
  not used.
- **Reported MAPE is a percentage** (for example 18.6), the same unit as `backtest.py`'s `MAPE`
  column. `error_bands` ratios are fractions (actual / prediction).
