Status: DONE

# Overnight log (2026-11-22)

## MORNING SUMMARY
**All 7 backlog tasks are done**, uncommitted for your review. Tests: 140 API + 51 pipeline, all pass
(baseline 103 + 32). Full details per task below.

Done:
1. Docs honesty pass: fake stats, latency and accuracy claims removed from 6 docs; they describe
   current behaviour and link RETRAINING_RESEARCH.md and pipeline/README.md.
2. CI: `.github/workflows/tests.yml` (both suites, push + PR, Python 3.12); `price_api/requirements.txt`
   pinned to the tested versions.
3. `/micro-market` now shows each nearby market's latest real WFP price (plus per-kg where the unit
   allows) only if it's at most 3 months old; `latest_prices.json` built from the local snapshot and
   refreshed by the monthly retrain.
4. Per-pricetype bands (schema 2 champion, backward-compatible); champion.json regenerated with
   identical backtest/bands plus the new key.
5. README "Deploying feedback storage" (Render disk at /var/data + FEEDBACK_DB_PATH); render.yaml untouched.
6. `MARKET_COORD_OVERRIDES` mechanism, empty, validated, tested; suspects listed, no values changed.
7. `pipeline/EXPERIMENTS.md` plan for external features (no data downloaded).

Needs your decision or check:
- **Unsaved edits in VS Code:** `intent_router.py` had unsaved buffer changes and the Quick Open box held
  text I didn't type ("im trying"). Someone else seems to have been typing in that window. I left both
  alone; the file on disk is unchanged. Please look before saving anything there.
- **winctl:** keystrokes weren't reliable (first try typed into app.py's buffer, closed unsaved; second
  try found the window in use), so every edit was applied by verified file copy. See the scorecard.
- **The "16.6% best RF" figure** isn't recorded anywhere in the repo, so docs don't use it. The repo
  has champion.json (n = 2,021, naive 15.6%) and RETRAINING_RESEARCH.md (1,508 Wholesale rows, naive
  17.39% vs RF 18.55%). Decide which run is the reference, and record the RF number if you have it.
- **Coordinates to verify:** Kitengela (Kajiado) sits at Nairobi's point (strong suspect);
  Ethiopia/Mogadishu (Kakuma) share one point; Hola (Tana River) has no coordinates in WFP's file.
  Add a sourced override to fix one.
- **Render:** the pinned requirements will apply on the next deploy (Python 3.12.1 there); watch the
  first build. A persistent disk is a paid-plan choice: doc only.
- **retrain.yml** got one extra guarded line, copying `latest_prices.json` on exit 0 (needed for monthly
  refresh). Small wart: on exit 1 the retrain writes it to `out/` while the report says "nothing written".
- New behaviour to be aware of: a malformed `error_bands_by_pricetype` entry makes the API fall back to
  the default champion (same rule as other bad bands); `/predict` now returns `band_source`.

Not done / not verified: no real GitHub Actions run; no clean `pip install` from scratch (would
download); manual HTTP scripts (`test_endpoints.py`, `test_gemini_recommendations.py`) not run.


## winctl scorecard
| file | method | matched source | retries | seconds | failure type |
|---|---|---|---|---|---|
| price_api/requirements.txt | winctl, then copy | y (copy) | 1 | ~60 | Ctrl+P didn't open Quick Open; path typed into the app.py buffer (closed unsaved, repo verified vs snapshot) |
| .github/workflows/tests.yml | copy | y | 0 | <5 | (copy chosen after the failure above) |
| README.md, price_api/API_ENDPOINTS.md, SCHEMAS.md, TEST_VALIDATION_REPORT.md, IMPLEMENTATION_SUMMARY.md, INTEGRATION_GUIDE.md (Task 1) | copy | y | 0 | <5 | - |
| pipeline/EXPERIMENTS.md (Task 7) | copy | y | 0 | <5 | - |
| README.md (Task 5) | copy | y | 0 | <5 | - |
| .github/workflows/retrain.yml (Task 3) | winctl, then copy | y (copy) | 0 | ~30 to abort | Quick Open opened but already held someone else's text; aborted before typing, no edits by me |
| Task 3 code + JSON + 4 docs (12 files) | copy | y | 0 | <5 | - |
| Task 6: price_api/geo.py, app.py, test_coord_overrides_unit.py | scripted edit (copy-equivalent), verified equal to the tested scratch copy | y | 0 | <5 | winctl not attempted: VS Code window in use by someone else |
| Task 4: pipeline/retrain.py, test_pipeline_unit.py, price_api/predictor.py, app.py, test_predictor_unit.py, artifacts/champion.json | copy | y | 0 | <5 | winctl not attempted (same reason) |
| Final docs (TEST_VALIDATION_REPORT, IMPLEMENTATION_SUMMARY, SCHEMAS, API_ENDPOINTS) | copy | y | 0 | <5 | - |

## Setup
- Baseline tests: price_api 103 OK, pipeline 32 OK.
- Pre-run snapshot of the working tree (no .git) saved outside the repo for reverts:
  `C:\Users\bison\AppData\Local\Temp\claude\1903e24540b89858\4f5513d2-f49a-40b1-a9a0-582e3d715886\scratchpad\baseline\repo_baseline.tar`
- Usage at start: 5-hour window 28%.

## Note on applying edits (winctl / VS Code)
- First winctl attempt (Task 2): Ctrl+P did not open Quick Open; the typed path went into the open
  `app.py` editor buffer as stray characters (never saved). I closed that tab with "Don't Save" and
  confirmed every repo file is byte-identical to the pre-run snapshot. Because keystrokes were not
  landing reliably, all edits tonight are applied by file copy (the documented fallback) and verified
  with `cmp`. VS Code reloads changed files from disk, so they're visible there.

## Task 2: CI workflow + pinned requirements (done)
- `price_api/requirements.txt`: pinned every package to the version installed in the venv the tests
  pass with (Python 3.12.10, `pip check` clean): fastapi 0.165.0, uvicorn[standard] 0.54.0,
  gunicorn 27.0.1, joblib 1.6.0, numpy 2.5.5, pandas 3.1.1, scikit-learn 1.9.1, pydantic 2.14.4,
  shap 0.54.1, python-dotenv 1.2.4, httpx 0.28.1. Same pins as `pipeline/requirements.txt` for the
  shared four.
- New `.github/workflows/tests.yml`: on push and pull_request, Python 3.12, installs both requirement
  files, runs every `price_api/test_*_unit.py` (discover pattern, so new test modules are picked up;
  `test_endpoints.py`/`test_gemini_recommendations.py` excluded: they need a live server / API key)
  and the pipeline suite with `PRICE_API_APP` set. `FEEDBACK_DB_PATH` points at `runner.temp`.
- `retrain.yml` untouched (it already pins via `pipeline/requirements.txt`; nothing to share).
- Not verified: an actual GitHub Actions run, or a clean `pip install` from scratch (would download
  packages). Render's build uses `price_api/requirements.txt` with PYTHON_VERSION 3.12.1, so these pins
  also apply to the next deploy: worth watching the first build.
- Tests: price_api 103 OK, pipeline 32 OK.

## Task 1: Docs honesty pass (done)
Two Haiku drafts (3 files each) in a scratch copy, reviewed by me, then copied in.
- `README.md` (surgical): "production-ready" -> "prototype"; removed SHAP / feature-phone / latency
  ("~50ms", "~2s", "5 mins") / "farmer trust 10x" claims; architecture + features describe the naive
  champion, 80% bands, SQLite feedback, distance-only `/micro-market`, legacy-RF importances; added
  `FEEDBACK_DB_PATH`, new modules to the tree, links to `RETRAINING_RESEARCH.md` and `pipeline/README.md`.
- `price_api/API_ENDPOINTS.md`, `SCHEMAS.md` (rewritten): removed 15420 / 3847 / 0.842 / 2847500,
  "42 markets", latency lines, a non-existent `features` field; added `/health`, `/webhook`, 503s;
  field names checked against app.py line numbers; the `/predict` example is computed from
  champion.json (Tomatoes band p10 0.758 / p90 1.2465) on the docs' existing example input 58.2.
- `TEST_VALIDATION_REPORT.md`: old 2025-12-05 "12/12" report withdrawn with reasons; current suites,
  commands and per-module counts; manual scripts listed as not run.
- `IMPLEMENTATION_SUMMARY.md`, `INTEGRATION_GUIDE.md`: describe current behaviour; removed hard-coded
  stats, "production-ready", latency and hour estimates; fixed the `/predict` example (maize is not
  a supported crop).
- **Number I did NOT use:** the "best RF 16.6% MAPE" from my brief appears in no repo file. Docs cite
  only what the repo records: champion.json (60-month walk-forward, n = 2,021, naive MAPE 15.6%,
  median APE 9.7%, no RF figure) and RETRAINING_RESEARCH.md section 5.1 (1,508 Wholesale rows, naive
  17.39% vs best RF 18.55%). **Decision for you:** if 16.6% came from a pipeline run, record it in
  champion.json/the report so docs can cite it.
- Checked: the hard-coded "~91% of model signal" text in `/explainability` matches the legacy model's
  real `price_ma_3` importance (0.9074). Left as is; docs say it's fixed text that matches.
- Noticed, not changed (code): `enhanced_endpoints.py`/`intent_router.py` comments say "Claude"/"Haiku"
  but call Gemini; an unused SHAP `ExplainRequest` path in `enhanced_endpoints.py`.
- Tests: 103 + 32 OK (docs only).

## Task 7 (done early, independent): `pipeline/EXPERIMENTS.md`
- Plan only, no data downloaded. Gate described from `retrain.py` (`walk_forward`, month-clustered
  `paired_bootstrap` 2,000 draws seed 0, RF wins only if the 95% CI upper bound < 0, never
  auto-promoted). Leakage rules (publication lag/vintages), candidates: CHIRPS rainfall, EPRA pump
  prices, CBK KES/USD, FAO GIEWS / FEWS NET crop calendars, KNBS CPI, each "verify URL/licence".
  Proposed 1.0 pp minimum margin is labelled a proposal. Open questions include the baseline mismatch
  above and retrain cadence (pipeline README says every 3 months, RETRAINING_RESEARCH.md says 6).

## Task 5: Render persistence doc (done, docs only)
- README "Deployment" gains **Deploying feedback storage**: why the default path loses feedback on
  Render's ephemeral disk, attach a persistent disk at `/var/data`, set
  `FEEDBACK_DB_PATH=/var/data/feedback.db`, an *unapplied* `render.yaml` snippet (labelled "check
  Render's Blueprint docs"), WAL/busy-timeout note for 4 workers, single-instance and backup caveats.
  `render.yaml` not edited (paid disk = your call).
- Also fixed in the same section: the Heroku/Railway and Docker commands ran plain `gunicorn app:app`,
  which can't serve FastAPI; now `-k uvicorn.workers.UvicornWorker` (as render.yaml does), run from
  `price_api/`, Docker on python 3.12 with `price_api/requirements.txt`. Docker block marked untested.

## Task 3: Real nearby-market prices (done)
Haiku draft in scratch, reviewed; I added `price_per_kg`. Applied by copy.
- New `pipeline/latest_prices.py`: `build_latest_prices(df)` reuses `features.read_wfp_csv` and the same
  filters as `monthly_panel` (KES, priceflag actual/aggregate with actual preferred, tracked
  commodities via `RAW_TO_CANONICAL`, price > 0). Latest observation per (market, commodity,
  pricetype); same-date ties use the median **within the most common unit** (never across units).
  `data_through` = latest month in the whole snapshot. CLI writes the JSON.
- `pipeline/retrain.py`: writes `latest_prices.json` to `--out` after validation (non-dry-run);
  failures only go to the report, exit codes unchanged. Small wart: on exit 1 ("nothing new") the file
  is still written to `out/` but the report says "nothing written"; the workflow copies it only on exit 0.
- `.github/workflows/retrain.yml`: one guarded `cp out/latest_prices.json ...` line in the exit-0
  apply step (needed so the file refreshes monthly). Nothing else changed there.
- Generated `price_api/artifacts/latest_prices.json` from `data/wfp_snapshot.csv.gz`: 342 entries,
  data_through 2026-10. All 40 API markets have some entry; markets with an observation within 3 months,
  out of 40: Tomatoes 3, Onions 2, Potatoes 15, Kale 2, Cabbage 1, Bananas 12 (agent's count).
- API: new `price_api/latest_prices.py` (stdlib) + `/micro-market` changes in `app.py`. Optional request
  field `pricetype`. Per market: `estimated_price` (latest observed WFP price, not a forecast, in WFP's
  unit), `price_per_kg` (only for `KG`/`<n> KG` units, e.g. 64 KG crate / 64; null for Head/Unit/Bunch),
  `price_date`, `price_unit`, `price_pricetype`, `price_note`; top-level `price_data_through`. Shown only
  if the observation month is <= 3 months before data_through, else null + reason. Missing/corrupt file
  (or `LATEST_PRICES_PATH`) disables prices without crashing.
- Real check: Nairobi, 30 km, tomatoes/potatoes -> none of the 5 neighbours has a recent price
  (latest 2025-06 at Wakulima), so all null with notes. Docs (API_ENDPOINTS, SCHEMAS, README,
  IMPLEMENTATION_SUMMARY) updated with that real output.
- Tests: price_api 121 OK (+16 latest-prices endpoint/lookup, +2 price_per_kg), pipeline 44 OK (+12).
- winctl retry here: Quick Open opened, but it already contained text I hadn't typed ("im trying"), and
  `intent_router.py` showed unsaved edits that weren't mine. **Someone else appears to be typing in that
  VS Code window** (this probably also explains the earlier stray characters). I stopped touching
  VS Code, left that buffer and the Quick Open box alone, confirmed the files on disk match the
  snapshot plus my changes, and used copy. Please check the unsaved `intent_router.py` buffer in VS Code.

## Task 6: Coordinate overrides mechanism (done; no coordinate values changed)
- `price_api/geo.py`: new `apply_coord_overrides(coords, overrides)` returns a corrected copy. Each
  override needs `lat`, `lon` and a non-empty `source`; unknown market, missing source, non-finite or
  outside-Kenya values (bbox lat -5.0..5.5, lon 33.5..42.5, catches swapped lat/lon) raise ValueError
  at startup instead of silently moving a market.
- `price_api/app.py`: `MARKET_COORD_OVERRIDES: dict = {}` (EMPTY) right after `MARKET_COORDS`, documented
  with the entry shape and the Kitengela suspicion; `MARKET_COORDS` is passed through it, so
  `/micro-market`, market lookup and the legacy RF's lat/lon features all see the same coordinates.
  `data_source` reads "WFP market coordinates (N manually corrected)" only when overrides exist.
- New `price_api/test_coord_overrides_unit.py` (7 tests): validation cases plus a check (via `ast`,
  no model load) that the shipped table is empty.
- **Suspected bad coordinates, for you to verify (nothing changed):**
  1. **Kitengela (Kajiado)**: strong. WFP has (-1.29, 36.86) and files it under admin1/admin2
     Nairobi/Nairobi, i.e. Nairobi's location, although the name says Kajiado and the town is
     reportedly about 30 km south. It therefore shows as 4.6 km from Nairobi in `/micro-market`.
  2. **Ethiopia (Kakuma)** and **Mogadishu (Kakuma)**: share the exact point (3.72, 34.85). Probably
     sections of Kakuma camp given one coordinate, so their mutual distance (0 km) is meaningless.
  3. **Hola (Tana River)**: in WFP's markets file with no coordinates at all. Not an API market today.
  - Checked and fine: "Kaanwa (Tharaka Nithi)" is filed under Meru South (a Tharaka Nithi area);
    Kalobeyei villages are in Turkana; WFP's other shared points are Mombasa-area markets not in the API.
  - To fix one: add `"Kitengela (Kajiado)": {"lat": ..., "lon": ..., "source": "<reference>"}`.
- Tests: price_api 128 OK, pipeline 44 OK.

## Task 4: Per-pricetype bands (done)
Haiku draft in scratch, reviewed by me, merged with Task 6 (the Task 6 edit script was re-run on the
draft before copying, so neither change was lost). Applied by copy.
- `pipeline/retrain.py`: new `error_bands_by_pricetype(T, pred_col, min_n=30)`; new champions are
  `schema_version: 2` with `error_bands_by_pricetype` alongside the unchanged `error_bands`; the champion
  contract check accepts schema 1 and 2; report.md gets a by-pricetype table. The 30-row minimum is new
  (the per-commodity bands have none); groups below it are skipped, never filled in.
- `price_api/predictor.py`: accepts schema 1 and 2 (current-style schema-1 files load and behave as
  before); validates the new key (a bad entry rejects the file to the default champion, like other bad
  bands). Lookup: (commodity, pricetype) -> commodity -> `_ALL` -> default, case-insensitive.
- `price_api/app.py`: `/predict` passes the request's `pricetype`; `/predict` and `/explainability`
  responses gain an additive `band_source` (e.g. "Tomatoes/Wholesale", "Tomatoes", "_ALL", "default").
- `price_api/artifacts/champion.json` **regenerated** from the local snapshot
  (`retrain.py --csv data/wfp_snapshot.csv.gz`, no download). I verified `backtest`, `data_through`,
  `model_type`, `model_file` and `error_bands` are identical to the previous file; only `created_at`,
  `schema_version` (1 -> 2) and the new key differ. Bands present (n): Bananas Retail 280; Cabbage
  Wholesale 263; Kale Wholesale 273; Onions (dry) Wholesale 272; Potatoes (Irish) Retail 325 and
  Wholesale 289; Tomatoes Wholesale 319. (Cabbage Retail 26 and Tomatoes Retail 1 skipped as too small.)
- Docs: IMPLEMENTATION_SUMMARY (schema 2, lookup, `band_source`), API_ENDPOINTS/SCHEMAS
  (`champion_schema_version` 2), TEST_VALIDATION_REPORT (current counts, CI).
- Tests: price_api 140 OK (+12 predictor), pipeline 51 OK (+7).

## Final check (01:06)
- Full run: price_api 140 tests OK, pipeline 51 tests OK (baseline was 103 + 32).
- `git status --short`: modified .gitignore, IMPLEMENTATION_SUMMARY.md, INTEGRATION_GUIDE.md, README.md,
  SCHEMAS.md, TEST_VALIDATION_REPORT.md, price_api/API_ENDPOINTS.md, price_api/app.py,
  price_api/intent_router.py (that one only from yesterday's work, not tonight), price_api/requirements.txt;
  untracked: .github/, OVERNIGHT_LOG.md, RETRAINING_RESEARCH.md, data/, pipeline/, and new price_api
  files (champion.json, latest_prices.json, latest_prices.py, geo.py, predictor.py, feedback_store.py,
  feature_importance.py, test_*_unit.py). Nothing committed, pushed, stashed or branch-switched.
- No test feedback DB left behind (temp DBs deleted; no price_api/data/ folder created).
