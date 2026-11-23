# crop_swarm: a crop-price forecasting challenger

A challenger, not a champion. The package forecasts monthly WFP Kenya staple prices with a small
swarm of deterministic agents, and a walk-forward backtest compares it with the pipeline's
`naive_last` champion. It never writes `price_api/artifacts/champion.json` and does not change
`pipeline/retrain.py`. Status: **the backtest in this run did not justify promotion** (see Results).

## Layout

```
swarm/
  crop_swarm/
    series.py          load the pipeline panel (read only) and build dense monthly arrays
    agents.py          TrendSeasonalAgent, SupplySeasonAgent, RiskBandAgent, NarrativeAgent,
                       weighted_median, bounded_nudge
    swarm.py           Swarm: fit on a training prefix, forecast one target
    llm_provider.py    complete_json(prompt, schema) with offline / ollama / claude_cli / anthropic_api
  backtest.py          walk-forward backtest, markdown + JSON output, PROMOTE / KEEP-CHAMPION verdict
  tests/test_crop_swarm_unit.py   25 offline unit tests (includes a run on a real data slice)
  results/             backtest_<date>.json and backtest_<date>.md from the run
```

It reads, and never edits, `monorepo/pipeline/` (`features.py`, `validate.py`, `retrain.py`) and
`monorepo/data/wfp_snapshot.csv.gz`.

## Design

**Information rule.** Every agent is a pure function of a price prefix. `Swarm.forecast(key, series, target)`
takes the full array, derives the origin `o = target - horizon` itself and passes only `series[:o+1]` to
the agents. Months `>= target` cannot reach any agent, the aggregator, or the bands. `Swarm.fit(train, h, cut)`
truncates its input at `cut` (the last month whose price is known). There is no wall clock, no random
draw and no async bus. The test suite checks both properties (see Tests).

**Walk-forward protocol (same as `pipeline/retrain.py`).** Window = last 60 months of the panel.
Blocks of 3 target months. For block `b` the training cut is `b[0] - h`, which is `b[0] - 1` for the
pipeline's horizon 1 (identical to `retrain.py`'s `per_i < b[0]`). Series key `(commodity, market, unit, pricetype)`,
monthly median prices, observed months only, no imputation. `retrain.walk_forward` is called in the backtest
and its h=1 rows are compared row by row with the backtest's h=1 rows (see Results).

**Agents** (log-price deltas from the origin price):

| agent | what it uses (prefix up to origin) | fitted on training only |
|---|---|---|
| `TrendSeasonalAgent` | seasonal naive `log y[t-12]` plus `g * (log y[o] - log y[o-12])` | `g` chosen from {0, .25, .5, .75, 1} by training MAE |
| `SupplySeasonAgent` | calendar move `s[t mod 12] - s[o mod 12]`, `s` = month-of-year index of the centred 13-month log deviation, pooled over markets per (commodity, pricetype) | `s` from training months only; months with fewer than 24 pooled observations use their harvest-phase mean |
| `RiskBandAgent` | 10th and 90th percentiles of `actual / point` ratios (same convention as `retrain.error_bands`) | pooled per (commodity, pricetype) if n >= 30, else per commodity, else all, from training-period residuals |
| `Aggregator` (in `Swarm`) | weighted median of agent log deltas, weights = 1 / training MAE of each agent, normalised | training rows only |
| `NarrativeAgent` (optional, OFF) | LLM JSON `{direction, confidence, reasons}`; multiplies the point by `1 + 0.05 * sign * confidence` | none |

If no agent has a view, the swarm returns the naive last value and flags `fallback`. The band uses the
point forecast, so the band moves with the nudge when the narrative agent is on.

**Harvest calendar.** `SupplySeasonAgent` uses the approximate ranges from `research/IMPROVEMENT_RESEARCH.md`
item 6: long-rains harvest Apr to Aug, short-rains harvest Sep to Jan, lean Feb to Mar. These are "roughly"
ranges that the research doc does not verify, and the calendar enters only as the fallback for months with
thin data. It is not a rainfall model.

**Narrative nudge bound.** `bounded_nudge(direction, confidence)` returns `0.05 * sign * clip(confidence, 0, 1)`,
so the aggregate can move at most 5 percent in either direction. Invalid directions raise. The provider's
reply is validated against `NARRATIVE_SCHEMA` before it gets here.

## Metrics and verdict

Identical to the pipeline: `retrain.summarize` (MAPE, median APE, P90 APE), `retrain.paired_bootstrap`
(month-clustered paired bootstrap of MAPE difference vs `naive_last`, 2,000 resamples, seed 0).

Added: MASE with two scales, the in-sample one-step naive MAE (`lag 1`) and the in-sample seasonal naive MAE
(`lag 12`), each computed from the known prices before the block cut. Both are reported for every horizon.
Also: fallback counts, and the swarm's 80 percent band coverage and width.

Horizons 1, 3 and 6 are reported. For h = 3 and 6 the forecast for month t is made from the price of month t - h.
The cut is moved back by h, so no information after the origin is used. The rows are the observed target
months whose origin month is observed. For each horizon, every method is scored on the same rows.

**Seasonal naive and drift fallbacks.** When the required lag is missing, seasonal naive uses the naive value
and drift uses the naive value. These rows stay in the sample so that n is the same for every method, and the
count is reported as `rows with fallback`.

**Verdict.** The pipeline's promotion rule: a challenger wins only if the 95 percent CI upper bound of the
paired MAPE difference (challenger minus `naive_last`) is below 0. The verdict is printed for horizon 1, the
pipeline's horizon. Even on PROMOTE the swarm is only a candidate, and a human must review it (the pipeline
rule). The script does not write any champion or candidate file.

## How to run

From `monorepo/swarm`, with a Python that has numpy, pandas, scikit-learn and joblib (the pipeline's
requirements; the scratchpad venv has all four):

```
python -W ignore -m unittest discover -s tests -p "test_*_unit.py"   # 25 tests, about 5 s
python -W ignore backtest.py                                         # about 30 s, writes results/
python -W ignore backtest.py --horizons 1 --bootstrap-n 500          # quicker
```

Options: `--snapshot`, `--window-months` (default 60), `--retrain-every` (default 3), `--horizons`
(default `1,3,6`), `--bootstrap-n` (default 2000), `--out-dir`.

## LLM backends (narrative agent, OFF by default)

Selected by `CROP_SWARM_LLM` (default `offline`). Every backend validates the reply against the schema and
retries once on invalid output. Any other failure (missing binary, timeout, limit error, HTTP error, no key)
falls back to the offline stub, and the reason is kept in `provider.last_fallback_reason`.

| value | what it does | required env | terms and caveats |
|---|---|---|---|
| `offline` (default) | deterministic neutral answer (`flat`, confidence 0). No network. | none | Used by tests and by the backtest. |
| `ollama` | `POST {CROP_SWARM_OLLAMA_URL or http://localhost:11434/v1}/chat/completions` with JSON mode, temperature 0 | `CROP_SWARM_OLLAMA_MODEL` | Local model, no account terms. Small models can be unreliable at structured output, which is why the retry and fallback exist. **Not run in this session** (no local server). |
| `claude_cli` | `claude -p <prompt + schema> --output-format json` in an empty temp directory, with `ANTHROPIC_API_KEY` removed from the child environment, never `--bare` | `CROP_SWARM_ALLOW_CLAUDE_CLI=1`; optional `CROP_SWARM_CLAUDE_BIN`, `CROP_SWARM_CLAUDE_MODEL`, `CROP_SWARM_TIMEOUT_S` (default 120) | **Personal local development only, not for serving other users** (the WhatsApp bot, a public demo, a shared server). Anthropic's terms do not allow subscription logins to be routed through third-party products, and the research note `research/IMPROVEMENT_RESEARCH.md` calls the scheduled-use case ambiguous. Usage draws on shared subscription limits. **Not called in this session**: the tests mock `subprocess.run`. The envelope format (`result`, `is_error`, `subtype`) was assumed and not checked against the installed CLI version. Re-check the terms before any use. |
| `anthropic_api` | Messages API, `model` from `CROP_SWARM_ANTHROPIC_MODEL` (default `claude-haiku-5-5`) | `ANTHROPIC_API_KEY` | Per-token billing, so this is the route for any service other people use. Falls back to offline when the key is not set. **Not called in this session** (no key used). |

The agent calls the LLM only when `Swarm(narrative=NarrativeAgent(provider))` is used. `backtest.py` never
does this, so its results are "swarm, LLM off".

## Results (this run)

Run: `python -W ignore backtest.py` on `monorepo/data/wfp_snapshot.csv.gz`
(sha256 `4ce53496ab33e4d88a41e96b4d26671a2f560fa2e3d013e46fbd04b71fcdeb7a`, 351 series, 4,830 panel rows,
2006-01 to 2026-10). Window 60 months, 20 blocks, retrain every 3 months, 2,000 bootstrap resamples, seed 0.
Full output: `results/backtest_2026-11-22.json` and `results/backtest_2026-11-22.md`.

- **Parity with `retrain.walk_forward` (h=1):** identical = True, 2,021 matched rows (retrain 2,021, backtest 2,021), max absolute difference in naive predictions 0.0.
- **Champion reproduction:** `champion.json` says n = 2,021, MAPE = 15.593. Reproduced here: n = 2,021, MAPE = 15.593, reproduced = True.

### Horizon 1 month (the pipeline's horizon), n = 2,021

| method | n | MAPE % | median APE % | P90 APE % | MASE (naive scale) | MASE (seasonal-naive scale) | rows with fallback |
|---|---|---|---|---|---|---|---|
| naive_last | 2021 | 15.593 | 9.744 | 35.531 | 1.3496 | 1.0974 | - |
| seasonal_naive | 2021 | 25.116 | 16.667 | 53.846 | 1.9343 | 1.4776 | 785 |
| drift | 2021 | 16.15 | 9.984 | 37.064 | 1.3927 | 1.1442 | 886 |
| swarm | 2021 | 16.324 | 10.578 | 35.855 | 1.4631 | 1.1365 | 0 |

Paired bootstrap, MAPE(method) minus MAPE(naive_last), percentage points (negative = method better):

| method | point | 95% CI low | 95% CI high | months |
|---|---|---|---|---|
| seasonal_naive | 9.523 | 6.956 | 12.038 | 60 |
| drift | 0.557 | 0.334 | 0.805 | 60 |
| swarm | 0.731 | 0.319 | 1.223 | 60 |

Swarm 80% bands: coverage 78.57% (target about 80%), median upper/lower ratio 1.6611.

### Horizon 3 months, n = 2,057

| method | n | MAPE % | median APE % | P90 APE % | MASE (naive scale) | MASE (seasonal-naive scale) | rows with fallback |
|---|---|---|---|---|---|---|---|
| naive_last | 2057 | 25.363 | 16.581 | 54.422 | 2.3262 | 1.8057 | - |
| seasonal_naive | 2057 | 28.65 | 20.0 | 58.659 | 2.5785 | 1.8386 | 811 |
| drift | 2057 | 28.11 | 17.806 | 59.823 | 2.5187 | 1.9557 | 1000 |
| swarm | 2057 | 25.712 | 17.704 | 55.647 | 2.4603 | 1.7545 | 0 |

| method | point | 95% CI low | 95% CI high | months |
|---|---|---|---|---|
| seasonal_naive | 3.287 | 1.138 | 5.484 | 60 |
| drift | 2.748 | 1.696 | 3.885 | 60 |
| swarm | 0.349 | -0.98 | 1.919 | 60 |

Swarm 80% bands: coverage 74.67%, median upper/lower ratio 2.1766.

### Horizon 6 months, n = 1,927

| method | n | MAPE % | median APE % | P90 APE % | MASE (naive scale) | MASE (seasonal-naive scale) | rows with fallback |
|---|---|---|---|---|---|---|---|
| naive_last | 1927 | 28.525 | 19.234 | 60.0 | 2.4044 | 1.7869 | - |
| seasonal_naive | 1927 | 30.915 | 21.429 | 60.579 | 2.6047 | 1.8805 | 693 |
| drift | 1927 | 35.891 | 21.93 | 73.363 | 2.8969 | 2.1798 | 1054 |
| swarm | 1927 | 29.27 | 19.804 | 60.458 | 2.5708 | 1.7667 | 0 |

| method | point | 95% CI low | 95% CI high | months |
|---|---|---|---|---|
| seasonal_naive | 2.389 | 0.845 | 4.065 | 60 |
| drift | 7.365 | 4.725 | 10.241 | 60 |
| swarm | 0.744 | -0.362 | 1.904 | 60 |

Swarm 80% bands: coverage 74.05%, median upper/lower ratio 2.2514.

Swarm 80% coverage at h=1 by commodity: Bananas 87.86, Cabbage 79.09, Kale 76.92, Onions (dry) 78.68,
Potatoes (Irish) 76.06, Tomatoes 76.18 (percent). The champion's own bands, which are in-sample, give 79.76
percent on the same rows.

### Verdict

**KEEP-CHAMPION.** At horizon 1 the paired MAPE difference (swarm minus naive_last) is +0.731 pp, 95% CI
[0.319, 1.223] over 60 months. The upper bound is above 0, so the pipeline rule keeps `naive_last`. At
horizons 3 and 6 the swarm's CI includes 0, so the data do not separate it from `naive_last` there either.
The swarm does not beat the naive champion. This matches `research/IMPROVEMENT_RESEARCH.md`, which expects
no gain over simple baselines for monthly staples.

Read the results this way:

- The swarm is not a better one-step forecaster here. Its horizon-1 MAPE is 0.731 points worse, and the
  95% bootstrap CI for that difference excludes 0.
- Seasonal naive is much worse than naive at h=1, which tells you the 12-month lag alone is noisy in this panel.
- The swarm's 80% bands cover 74 to 79 percent of observations, close to the target, but its bands are fitted
  on past residuals while its point parameters are fitted in-sample on those residuals, so they are somewhat
  optimistic.
- Fallback rows are real limits: 785 (seasonal naive) and 886 (drift) of 2,021 h=1 rows lack the lag they need,
  and they carry the naive value.

## Tests

`python -W ignore -m unittest discover -s tests -p "test_*_unit.py"` (25 tests, offline, about 5 s):

- No look-ahead: changing the target month and every later month leaves the forecast for the target unchanged.
  Changing the origin month does change it (sanity check). `fit()` ignores months after the cut even when it is
  handed them. Horizon mismatch and an unobserved origin are refused.
- Determinism: same inputs give identical forecasts, weights and provider output. Agents are pure.
- Provider: falls back to offline when the CLI binary is missing, when it is not on PATH, when the
  `CROP_SWARM_ALLOW_CLAUDE_CLI` opt-in is absent, on an unknown backend, and when the key is missing.
  `claude_cli` removes `ANTHROPIC_API_KEY` from the child environment and never passes `--bare`. Usage-limit
  and timeout errors fall back. An invalid reply is retried once.
- Bounded nudge: never more than 5 percent, for every direction and confidence (including out-of-range
  values). The swarm's point moves by exactly `1 + nudge` with the narrative agent on.
- Aggregator: weighted median with ties and NaN.
- Real data: the backtest runs on a slice of the real snapshot (Kale and Tomatoes, 12-month window), reproduces
  the retrain.py h=1 rows exactly, and is deterministic.

## Caveats

- Horizons 3 and 6 use the h=1 protocol with the cut moved back by h. They are an extension, not part of the
  pipeline's promotion rule.
- MASE is computed with the in-sample lag-1 and lag-12 naive MAE of each series, using only known prices before the
  block cut. The same scale is used for every horizon.
- `RiskBandAgent` residuals come from training-period targets (already observed at block start). Their
  parameters are fitted in-sample, so the bands are not conformal and carry no coverage guarantee.
- The harvest calendar is approximate and unverified (see above).
- The seasonal index is pooled over markets within a commodity and pricetype, so it describes the average market.
- The NarrativeAgent is untested against a real model. Its behaviour is only exercised through the fake
  provider and the mocked CLI.
- No Kenyan-specific external data (rainfall, fuel, RTFP) is used. Those are items 5 to 7 of the research doc.
