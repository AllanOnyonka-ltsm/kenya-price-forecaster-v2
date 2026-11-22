# External-feature experiments: plan

**Status:** PLAN ONLY. No experiments have been run. No external data has been downloaded.
**Target location:** `pipeline/EXPERIMENTS.md`
**Scope:** Kenyan crop prices (WFP, six tracked crops, Wholesale and Retail as stored).

Every number in this file comes from the repo files named in the text, or is marked as a
proposal or "to be confirmed".

---

## 1. Purpose and the rule

**Purpose.** Test whether external data (rainfall, fuel prices, exchange rate, harvest
calendar, food inflation) improves the challenger model enough to justify adopting it.

**The rule.** A feature group is adopted only if a challenger that uses it beats the naive
champion (`naive_last`) on the same walk-forward gate the pipeline already uses. The pipeline
never auto-promotes a model.

**How the gate works today** (`pipeline/retrain.py`):

- **Champion:** `naive_last`. Prediction = `lag_1` (last month's price), set in `walk_forward`
  as `part["pred_naive"] = part["lag_1"]`.
- **Challenger:** `rf_logratio`. A `RandomForestRegressor` fitted on `RATIO_COLUMNS`
  (`features.py`), target `log(price / lag_1)`, prediction `lag_1 * exp(model)`, set in
  `walk_forward`.
- **Training rule:** in `walk_forward`, rows used for training satisfy `per_i < b[0]`, where
  `b[0]` is the first month of the test block. No future rows are used.
- **Test window:** the last `--window-months` months (default 60). The challenger is retrained
  every `--retrain-every` months (default 3).
- **Metric:** MAPE in percent, from `summarize`. Also median APE, P90 APE and n.
- **Significance:** `paired_bootstrap` resamples whole months (with replacement, 2,000 draws,
  seed 0). It returns MAPE(challenger) minus MAPE(champion) in percentage points.
- **"Beats":** in `_run`, `rf_wins = bs["ci95_hi_pp"] < 0`. The challenger wins only if the
  upper bound of the 95% interval is below zero.
- **Even when it wins,** `_run` still writes `champion.json` as `naive_last`
  (`make_champion("naive_last", ...)`). A winning challenger goes to `candidate/` with the flag
  `HUMAN REVIEW: challenger beat champion`. A person must promote it by hand (see
  `pipeline/README.md`, "Promoting a challenger by hand").

**Every experiment reports:**

- MAPE, median APE, P90 APE and n (from `summarize`).
- The bootstrap point estimate and 95% interval (from `paired_bootstrap`).
- Per-crop MAPE (`per_commodity` in `metrics.json`).
- Per-pricetype MAPE (`per_pricetype`).
- Per-half-year MAPE. The pipeline does not produce this yet. It is a proposal, based on the
  guard rail in `RETRAINING_RESEARCH.md` section 8.

**Baseline numbers to use.** See section 6, item 1. The `backtest` block in
`price_api/artifacts/champion.json` (written by the pipeline) records 2,021 predictions and naive MAPE
15.593%; it records no RandomForest MAPE. `RETRAINING_RESEARCH.md` section 5.1 gives 1,508
Wholesale-only predictions, naive MAPE 17.39% and `rf_logratio` MAPE 18.55%. The two runs cover
different rows, so their numbers can't be compared directly.
Until the owner settles which run is the reference, every experiment must rerun the
baseline itself on the same data.

---

## 2. Leakage rules for external data

The price for month t is forecast at the start of month t (the "forecast origin"). A feature
may be used for month t only if it was known at that origin.

1. **Publication lag, not reference month.** A value may be used for forecast month t only if
   its publication date is on or before the origin of t. A value for reference month t-1 is
   usable only if it was published before the start of month t. Each external series needs a
   documented publication rule or lag. Where the lag is unknown, write "to be confirmed" and
   do not run the experiment until it is filled in.
2. **Same-month values are not used.** Rainfall, FX averages and fuel reviews for month t are
   not used to predict the price for month t. Use lag 1 or older, unless the series is a fixed
   calendar (section 3, item D).
3. **Vintage and revisions.** Store every download with its download date. Where the source
   revises past values, backtests should use the value as it was first published. If only the
   latest vintage is available, label the backtest as optimistic in the report. Whether each
   source revises history is to be confirmed.
4. **Monthly alignment.** Aggregate every series to the same calendar month key as the price
   panel (`per`, a monthly period, from `monthly_panel`). Daily or pentad series are
   aggregated only from days inside the reference month and before the origin.
5. **No future fill.** Missing external values stay missing. Fill forward only from earlier,
   already-published values, and add a flag column that records the fill. Never back-fill or
   interpolate across a future point.
6. **No whole-sample statistics.** Anomalies, z-scores and climatologies must be computed
   from training years only (for example, per-calendar-month averages over years before the
   block). Computing them on the full sample leaks the future.
7. **Leakage canary.** Extend the leakage test in `RETRAINING_RESEARCH.md` section 9 (step 4).
   That test changes the price at month t and checks that features for t do not change. The
   extension (proposal): change the external value for month t and all later months, and check
   that the features for t do not change. Also run a placebo feature that is deliberately
   shifted into the future. It must fail the gate. If it passes, the gate is broken.

---

## 3. Candidate features

Each candidate lists what it is, why it might help, the candidate source (organisation and
dataset only), frequency, publication lag, how to join, and the leakage risk. URLs are landing
pages. **Verify URL and licence before use** for every source. Nothing has been checked.

### A. Rainfall anomalies

- **What:** monthly rainfall total per location, and an anomaly = (rain - climatology) /
  climatology. The climatology uses training years only (rule 6).
- **Hypothesis:** drought or flood affects supply, and prices move with a lag of one or more
  months. This is a proposal, not a finding.
- **Candidate source:** CHIRPS, UC Santa Barbara Climate Hazards Center (chc.ucsb.edu).
  Also the Kenya Meteorological Department (meteo.go.ke) for station data, with licence and
  access to be confirmed.
- **Frequency:** daily, pentad or monthly gridded. Use monthly.
- **Publication lag:** to be confirmed.
- **Join:** `monthly_panel` keeps `latitude` and `longitude` for each market, so a grid-cell
  value can be joined per market. A county or admin1 average is the fallback. Both need the
  boundary or grid mapping to be sourced.
- **Leakage risk:** same-month rain is not known at the origin (rule 2). A full-sample
  climatology leaks (rule 6). Revisions to gridded data may change past values (rule 3).

### B. Pump fuel prices

- **What:** monthly maximum pump price per litre for the EPRA review (KES), and the change
  from the previous review.
- **Hypothesis:** fuel is a transport cost, and transport costs feed into market prices.
  Proposal, not a finding.
- **Candidate source:** Energy and Petroleum Regulatory Authority (EPRA), epra.go.ke.
- **Frequency:** monthly review.
- **Publication lag:** to be confirmed. Record the date each review is announced, separately
  from the month it takes effect.
- **Join:** national. The same value applies to every market in a month, so this feature
  cannot separate markets.
- **Leakage risk:** using the effective month instead of the announcement date can put a
  price into the feature before it was public. Later-compiled series may differ from the
  first announcement.

### C. KES/USD exchange rate

- **What:** month-end or prior-month average KES per USD, and its monthly percentage change.
- **Hypothesis:** imported inputs and some imported produce are priced in dollars.
  Proposal, not a finding.
- **Candidate source:** Central Bank of Kenya published exchange rates (centralbank.go.ke).
- **Frequency:** daily. Aggregate to monthly.
- **Publication lag:** daily rates are published quickly; the exact lag is to be confirmed.
- **Join:** national.
- **Leakage risk:** a monthly average computed over the full reference month uses days after
  the origin. Use the last rate published before the origin, or the average of the prior full
  month, instead.

### D. Harvest and crop calendar

- **What:** a month-of-season category per crop and admin1 region (for example: planting,
  growing, harvest, lean season), built from published calendars.
- **Hypothesis:** prices tend to fall at harvest and rise in the lean season. Proposal, not a
  finding.
- **Candidate source:** FAO GIEWS crop calendars (fao.org/giews) and FEWS NET seasonal
  calendars (fews.net).
- **Frequency:** static, one calendar per crop and region.
- **Publication lag:** not applicable, because the calendar is known in advance. Record the
  calendar's own version date.
- **Join:** per crop (`commodity`) and region (`admin1`, which `monthly_panel` keeps).
- **Leakage risk:** low, because the values are known in advance. The main risk is selection
  on the test window: if the calendar boundaries are chosen by looking at price peaks in the
  backtest, the result is biased. Freeze the calendar from its source document before any run.
- **Note:** `features.py` already computes `sin_month` and `cos_month`, which are in
  `RATIO_COLUMNS`. Test whether the calendar adds anything beyond these two features.

### E. Food inflation (optional)

- **What:** monthly food CPI, and its year-on-year change.
- **Hypothesis:** general price pressure carries into crop prices. Proposal, not a finding.
- **Candidate source:** Kenya National Bureau of Statistics (knbs.or.ke).
- **Frequency:** monthly.
- **Publication lag:** to be confirmed. It is usually later than the reference month, so the
  rule 1 check matters most here.
- **Join:** national.
- **Leakage risk:** index rebasing and revisions. Year-on-year changes must use only values
  published before the origin.

---

## 4. Experiment protocol

1. **One group at a time.** Run the baseline (E0: the current `RATIO_COLUMNS`), then E1 (rain),
   E2 (fuel), E3 (FX), E4 (calendar) and E5 (CPI), each as baseline plus one group.
2. **Ablations.** After the single-group runs, run a combined model with all groups, then the
   combined model with each group removed in turn. A gain that disappears when one group is
   removed shows the gain depends on that group.
3. **Same settings for every run.** Same window (60 months by default), same
   `--retrain-every` (3), same `RF_PARAMS` (100 trees, max depth 15, `random_state` 42), same
   bootstrap settings (2,000 draws, seed 0). Only the feature set changes.
4. **Seed check (proposal).** `RF_PARAMS` fixes `random_state=42`. Run five random seeds for
   each feature set and report the spread. The primary result uses seed 42. A winning result
   should also win on most seeds, so that one lucky seed does not pass the gate.
5. **Primary gate.** The existing rule: the 95% interval upper bound of MAPE(challenger) minus
   MAPE(champion) is below zero.
6. **Minimum improvement (proposal).** Add a second requirement: the point estimate must be
   at least **1.0 percentage point** better than the champion.
   - *Justification:* `RETRAINING_RESEARCH.md` section 5.6 gives the `rf_logratio` interval
     as +0.42 to +1.93 points. Its half-width is (1.93 - 0.42) / 2, about 0.76 points. A gain
     smaller than that is inside the width of one interval, so it cannot be told apart from
     resampling noise. The 1.0-point figure is a proposal. The owner should confirm it before
     any run.
7. **Multiple comparisons (proposal).** Each group tested is another chance for a false pass.
   Use a stricter level for the interval, for example Bonferroni: with five groups, use a
   99% interval (alpha = 0.05 / 5 = 0.01) instead of 95%. Holm's method is an alternative.
   The owner should choose one before the runs.
8. **Noise estimate.** The bootstrap over months (`paired_bootstrap`) is the noise estimate.
   The seed spread from step 4 is a second check. Report both.
9. **Placebo runs.** Run two placebo features through the same gate, and include them in the
   log: (a) a random noise column; (b) a feature shifted into the future (rule 7). Neither
   should pass. If either passes, stop and check the join code before reading any other
   result.
10. **Guard rails.** Check per-crop MAPE and per-half-year MAPE, as `RETRAINING_RESEARCH.md`
    section 8 asks. A feature that wins only in one or two periods is not adopted.
11. **Freeze and log.** Every choice (anomaly definition, calendar boundaries, lag, fill rule)
    is fixed before the run and written into the experiment log. Failed groups stay in the log.
    They are not re-tuned on the same window, to avoid choosing the best of many variants.
12. **Out-of-sample check (proposal).** After a feature set passes, check it on a window that
    was not used for choosing it. Newer months, once they exist, are one option.

---

## 5. Implementation sketch

**Where the join goes.**

- `features.py`: add `load_external(manifest_path)`, which returns a monthly table keyed by
  `per` and location (market, admin1 or national), with the publication date for each value.
  Add `join_external(feats, ext, lag_months=1)`. For each row it takes the latest value whose
  publication date is on or before the origin of `per`.
- `features.py`: keep `build_features` unchanged, so the baseline reproduces exactly.
- `features.py`: `ratio_features` returns `X[RATIO_COLUMNS]`. Make the column list a parameter
  (default unchanged), and add one constant per group, such as `EXTERNAL_COLUMNS_RAIN`.
- `retrain.py`, `_run`: `feats = build_features(panel)` is at the line just before
  `walk_forward(feats, encoders, window, args.retrain_every)`. Insert the external join between
  them. `walk_forward` and `fit_final_rf` both call `ratio_features`, so they pick up the new
  columns once the column list is passed through.
- `retrain.py`, CLI: add `--feature-groups` (proposal). The default `none` keeps production
  behaviour.
- `retrain.py`, candidate output: record the feature groups and the join rules in
  `champion_candidate.json` (proposal). `check_champion_contract` only checks the champion
  fields today, so it needs no change for this, but a random-forest candidate with external
  features would not match the current API's feature builder. That mismatch is already noted
  in `pipeline/README.md`.

**Data folder.** Create `data/external/` next to the existing `data/` folder (which holds
the WFP snapshot per `pipeline/README.md`). None of this exists yet.

- `data/external/raw/`: files as downloaded, never edited.
- `data/external/manifest.csv`: one row per file, with these columns:
  `group`, `file`, `organisation`, `dataset`, `landing_url`, `licence`, `download_date_utc`,
  `sha256`, `coverage_start`, `coverage_end`, `geography`, `reference_frequency`,
  `publication_rule` (a lag or a fixed date), `publication_lag_days` ("to be confirmed" until
  checked), `vintage_note`, `verified_by`, `notes`.
- Files are added only after the licence and URL are checked. The manifest is the record of
  what was used.

**Tests (proposal).**

- Join test: changing any external value published after the origin must not change the
  features for that month.
- Placebo test from section 4, step 9, run through the gate.
- Existing unit tests (`pipeline/test_pipeline_unit.py`, per `pipeline/README.md`) must still
  pass with the default feature set.

---

## 6. Open questions for the owner

1. **Which baseline is the reference?** `champion.json` records 2,021 predictions and naive MAPE
   15.593% (no RF figure). `RETRAINING_RESEARCH.md` section 5.1 gives 1,508 Wholesale-only predictions,
   naive MAPE 17.39% and `rf_logratio` MAPE 18.55%. Which run should experiments report against?
2. **Retrain cadence.** `pipeline/README.md` says the challenger retrains every 3 months
   (`--retrain-every 3`). `RETRAINING_RESEARCH.md` section 8 says every 6 months. Which one
   should the experiments use?
3. **Minimum margin.** Accept the proposed 1.0-point minimum (section 4, step 6), or set another
   value?
4. **Multiple-comparison method.** Bonferroni, Holm, or a fixed stricter interval?
5. **Granularity.** National-level features only at first, or market-level (rain grid and
   market coordinates)?
6. **Licences.** Which sources are acceptable, and does any licence require attribution in the
   app?
7. **Retail prices.** `RETRAINING_RESEARCH.md` section 10 says the findings cover Wholesale
   only, and Wholesale rows dominate the test set. Should Retail be tested separately?
8. **Real-time archive.** Start recording download and publication dates now? Without them,
   the vintage rule (section 2, rule 3) cannot be fully met later.
9. **Promotion owner.** Who reviews a `HUMAN REVIEW` candidate, and what evidence is needed
   beyond the gate?
