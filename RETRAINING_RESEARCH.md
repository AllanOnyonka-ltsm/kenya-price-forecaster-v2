# Should the Crop-Price Model Be Retrained?

**Date of report:** 2026-11-22
**Scope:** Kenya wholesale food prices from the WFP dataset, six crops, a 60-month honest test (2021-10 to 2026-09).
**Source of every number:** the backtest and leak-test files listed in section 11. Anything those files do not show is marked **unknown**.

---

## 1. Summary and decision

- **The deployed model is fed the answer.** Its most important input, `price_ma_3` (the average of the last three months), is computed *including* the month being predicted. Its importance score is 0.9074. Experiments confirm the leak. The training code itself is not in the files, so the cause is inferred from how the model behaves.
- **The leak made the model look much better than it is.** On the same data, the deployed model scores 12.54% error with the leaked inputs and 26.31% with honest inputs. Naive "last month's price" scores 22.29%.
- **No model beats "last month's price" overall.** In an honest walk-forward test (each prediction uses only earlier data) over 1,508 predictions, the naive forecast has the lowest error on every headline measure: MAPE 17.39%, MAE KES 407.49. The best machine-learning model reaches MAPE 18.55% and MAE KES 439.32.
- **The gap is statistically clear.** All six machine-learning variants are worse than naive overall. Their 95% bootstrap intervals for the MAPE difference all sit above zero. The smallest lower bound is +0.42 points, for `rf_logratio`.
- **Caveat: naive is not best in every period.** A non-naive method had the lowest MAPE in 5 of 11 half-year periods. Those five periods had only 12 to 79 predictions each. In the six larger periods (61 to 397 predictions), naive was best every time. The overall conclusion holds, but "naive always wins" is too strong.
- **Proposed decision:** Retrain, but treat the retrained model as a **challenger**, not the production forecast.
  1. Make the naive forecast (`naive_last`) the **champion**, the forecast farmers see.
  2. Show an empirical error band for each crop (section 7).
  3. Retrain the machine-learning challenger monthly on leak-free inputs.
  4. Promote the challenger only if it significantly beats the champion on the same walk-forward test.
- **Known unknowns:** the deployed model's training cutoff is not recorded anywhere in the repo. One leak-test error figure (68.0% MAPE) is not reconciled with the walk-forward results (section 10).

---

## 2. Data

**Source.** The WFP "Kenya - Food Prices" dataset on the Humanitarian Data Exchange (data.humdata.org), file `wfp_food_prices_ken.csv`. The local copy is 3,793,902 bytes (about 3.8 MB), which matches the brief. The "last updated 2026-11-04" date comes from the brief and **could not be verified** from the files.

**Filtering (from `common.py`, `load_frame`):**
- Drop metadata rows whose date starts with `#` (the HDX hashtag row).
- Keep only `currency == KES`.
- Keep only rows whose `priceflag` value is `"actual"` (the dataset's flag for recorded prices).
- Keep only rows whose commodity, market, unit, pricetype, admin1 and category values exist in the deployed model's label encoders.
- Keep only prices greater than 0.

After filtering: **3,228 observed rows**, **121 commodity-market series** (123 when unit and pricetype are included).

**Mixed-unit series.** Two commodity-market pairs have more than one unit or pricetype:
- Kale at Takaba (Mandera): 50 KG Wholesale (9 rows), KG Retail (1 row).
- Potatoes (Irish) at Nairobi: 50 KG Wholesale (170 rows), KG Retail (1 row).

The backtest keys each series on unit and pricetype, so these stay separate.

**Evaluation universe.** A row is only evaluated if the previous calendar month is also observed (monthly grid per series; a missing month is a gap, not skipped). That leaves **2,616 rows**. This matters because the naive forecast needs last month's price to exist.

**Test window.** Target months **2021-10 to 2026-09** (60 months). The test set has **1,508 rows across 91 series**, all **Wholesale**.

| Crop | Test rows |
|---|---|
| Tomatoes | 331 |
| Onions (dry) | 283 |
| Kale | 282 |
| Cabbage | 274 |
| Potatoes (Irish, white) | 253 |
| Potatoes (Irish, red) | 85 |

Test rows per year: 2021: 182, 2022: 719, 2023: 347, 2024: 154, 2025: 73, 2026: 33.

**What the files do not show:**
- The full date range of the dataset (the test window is known; the earliest and latest data are not).
- The number of distinct markets.
- How many Retail rows exist after filtering. The test set has none, so **all findings here apply to Wholesale prices only**.
- The unit of each test row (the mixed-unit check only covers two series).
- Why "Potatoes (Irish)" at Nairobi (170 wholesale rows after filtering) has no test rows. The same applies to Spinach and Bananas, which appear in the model's perishable-crop list but not in the test set.

**Perishable list.** The deployed model's metadata (`preprocessing_info.pkl`) labels these crops as perishable: Tomatoes, Onions (dry), Potatoes (Irish), Potatoes (Irish, white), Potatoes (Irish, red), Kale, Spinach, Cabbage, Bananas.

---

## 3. The leakage finding

**In plain words.** A forecast is only honest if the model sees nothing from the month it is predicting. Here the model was given a three-month average that already contains the price it is supposed to forecast. That is like grading a test with the answer key in the question. The model learns to copy the answer, which looks excellent on past data and fails on future months.

**What leaks.** Three inputs are computed *including* the current month: `price_ma_3` (3-month average), `price_ma_6` (6-month average) and `price_vol_6` (6-month volatility). The honest version shifts them by one month, so they use only earlier months. The difference is in `common.py`, `add_feats`, where `leaky=True` omits the shift.

**Evidence.**

1. **Importance.** In the deployed model, `price_ma_3` scores **0.9074**. The next inputs are `price_lag_1` (0.0273), `price_lag_3` (0.0124), `price_vol_6` (0.0122), `price_lag_6` (0.0071) and `price_ma_6` (0.0067). (`leak_test_output.txt`)

2. **Correlation with the actual price.** Correlation of `price_ma_3` with the target is **0.9481** with leaked inputs and **0.8715** with honest inputs. The leaked version is more correlated because it contains the target itself. Correlation of `price_lag_1` is 0.8943 either way. (`leak_test_output.txt`)

3. **Fit on the same rows.** On 2,544 reconstructed rows (`leak_test_output.txt`):

| Predictor | R² | MAPE |
|---|---|---|
| Deployed model, leaked inputs | 0.8980 | 12.54% |
| Deployed model, honest inputs | 0.7245 | 26.31% |
| Naive: last month's price (`price_lag_1`) | 0.7886 | 22.29% |
| `price_ma_3` as-is, leaked (uses the target) | 0.8986 | 16.00% |
| `price_ma_3` honest | 0.7513 | 25.76% |

   With leaked inputs, the model appears to beat naive (12.54% vs 22.29%). With honest inputs, it does not (26.31% vs 22.29%). This is the core of the problem.

4. **Sensitivity test.** On rows with honest inputs, raising `price_ma_3` by 20% raises the deployed model's mean prediction by **+28.6%**. Raising `price_lag_1` by 20% lowers it by **−6.1%**. A model that depended on last month's price would not respond in that direction. (`leak_test_output.txt`)

5. **Random-input test.** When `price_ma_3` and `price_lag_1` are set apart at random, the deployed model's predictions track `price_ma_3` (mean |pred/ma_3 − 1| = 0.118) more closely than `price_lag_1` (mean |pred/lag_1 − 1| = 0.356). Correlation with `price_ma_3` is 0.938 versus 0.795 with `price_lag_1`. (`leak_test_output.txt`)

6. **Retraining comparison.** Models retrained on pre-2023 rows and tested on 2023 onward (820 rows) (`leak_evidence_output.txt`):

| Retrained with | Top feature | Holdout R² | Holdout MAPE |
|---|---|---|---|
| Leaked inputs | `price_ma_3` (0.913) | 0.874 | 35.4% |
| Honest inputs | `price_lag_1` (0.838) | 0.742 | 68.0% (see section 10) |

   Once the leak is removed, the model's top feature becomes last month's price, which is what a persistence model would rely on.

**Limit of the evidence.** These tests show the deployed model *behaves as if* it was trained on leaked inputs. The training code and training data for `random_forest_price_model.pkl` are not among the files, so this is inferred from behaviour, not read from the code.

**Why it misled.** The leaked model scores well on the data it was built from, because it can read the answer. Its honest version, which is what it will do in real use, scores 21.32% on the walk-forward test versus 17.39% for naive. That is the gap that matters for farmers.

---

## 4. Backtest method

**Why walk-forward.** A random train/test split lets a model learn from the future. Walk-forward mimics real use: at each point, the model sees only the past.

**Procedure (`backtest.py`).** The 60 test months are split into 10 blocks of 6 months. For each block:
- The machine-learning models are retrained on all rows before the block (an expanding window: each retrain uses all earlier history).
- They predict the block's months.
- The deployed model is **not** retrained. It is fed honest inputs (`deployed_B`).

The run arguments (60 months, retrain every 6) are inferred from the output header; the command line itself is not recorded.

**Methods compared.**

| Method | What it does |
|---|---|
| `naive_last` | Next month's price = last month's price (the same as the API's `previous_month_price`, per the script docstring) |
| `mean_3m` | Average of the previous three months |
| `seasonal_naive` | Same month last year. Only possible where the value 12 months earlier exists (938 of 1,508 rows) |
| `deployed_B` | The deployed model, fed honest inputs, not retrained |
| `rf_raw` | Random forest predicting the price in KES directly |
| `rf_logratio` | Random forest predicting log(price / last month's price), then converted back |
| `hgb_raw`, `hgb_logratio` | The same two targets using gradient boosting (HistGradientBoosting) |

Random forest settings: 100 trees, max depth 15, minimum samples to split 5, random seed 42. Gradient boosting settings: 300 iterations, learning rate 0.05, max depth 6, minimum samples per leaf 15, L2 regularisation 1.0, seed 42.

**Metrics.**
- **MAE (mean absolute error):** the average size of the miss, in KES. Lower is better.
- **MAPE (mean absolute percentage error):** the average miss as a share of the actual price, in percent. Lower is better. A MAPE of 17% means the typical forecast is 17% away from the actual price.
- **Median APE:** the middle value of the per-row percentage misses. Less affected by a few very large misses than MAPE.
- **Bias (`bias_med_pct`):** the median of (prediction − actual) / actual, in percent. Zero means no systematic over- or under-forecasting. Positive means over-forecasting.

**Bootstrap confidence intervals.** To judge whether one method really beats another, the script resamples whole months with replacement 2,000 times (seed 0). For each resample it computes the average MAPE difference between the method and naive, using the same rows for both (a "paired" comparison). The 2.5th, 50th and 97.5th percentiles of those averages form the 95% interval. Resampling whole months keeps the rows from the same month together, which respects that prices in the same month are related. If the interval is entirely above zero, the method is significantly worse than naive. The method only counts as significantly better if the interval is entirely below zero.

---

## 5. Results

### 5.1 Overall (all 1,508 Wholesale predictions)

| Method | n | MAE (KES) | MAPE (%) | Median APE (%) | Bias, median (%) |
|---|---|---|---|---|---|
| **naive_last** | 1508 | **407.49** | **17.39** | **11.83** | 0.00 |
| mean_3m | 1508 | 475.97 | 20.92 | 14.29 | -0.44 |
| seasonal_naive | 938 | 826.49 | 36.70 | 27.69 | -11.07 |
| deployed_B | 1508 | 490.28 | 21.32 | 13.73 | -0.17 |
| rf_raw | 1508 | 446.68 | 20.37 | 13.89 | 2.14 |
| rf_logratio | 1508 | 439.32 | 18.55 | 12.99 | -1.50 |
| hgb_logratio | 1508 | 464.82 | 19.75 | 14.15 | -1.04 |
| hgb_raw | 1508 | 462.74 | 21.06 | 14.12 | 1.36 |

Naive is best on MAE, MAPE and median APE. Seasonal naive is by far the worst.

### 5.2 Common sample (938 rows where seasonal naive exists)

This makes the comparison fair for seasonal naive, which needs a value from a year earlier.

| Method | n | MAE (KES) | MAPE (%) | Median APE (%) | Bias, median (%) |
|---|---|---|---|---|---|
| **naive_last** | 938 | **411.10** | **17.98** | **11.77** | 0.00 |
| mean_3m | 938 | 487.95 | 21.64 | 14.31 | -1.31 |
| seasonal_naive | 938 | 826.49 | 36.70 | 27.69 | -11.07 |
| deployed_B | 938 | 491.57 | 21.94 | 13.54 | -0.32 |
| rf_raw | 938 | 450.34 | 21.08 | 14.29 | 1.00 |
| rf_logratio | 938 | 445.71 | 19.00 | 13.49 | -2.28 |
| hgb_logratio | 938 | 468.35 | 20.05 | 14.17 | -2.34 |
| hgb_raw | 938 | 464.57 | 21.34 | 14.08 | 0.98 |

On this sample too, naive is best on all three measures. Seasonal naive is still worst, so a "same month last year" rule does not help.

### 5.3 MAPE by commodity (%)

| Crop | n | naive | mean_3m | seasonal | deployed_B | rf_raw | rf_logratio | hgb_logratio | hgb_raw |
|---|---|---|---|---|---|---|---|---|---|
| Cabbage | 274 | **19.6** | 22.7 | 44.1 | 22.3 | 20.3 | 20.9 | 22.0 | 22.5 |
| Kale | 282 | **20.7** | 25.0 | 38.8 | 25.9 | 26.0 | 22.7 | 23.2 | 26.0 |
| Onions (dry) | 283 | **13.7** | 17.6 | 42.1 | 17.9 | 21.6 | 16.0 | 16.7 | 22.3 |
| Potatoes (Irish, red) | 85 | 13.0 | 17.0 | 32.5 | 18.2 | 13.9 | 12.6 | 13.7 | **12.4** |
| Potatoes (Irish, white) | 253 | 16.7 | 19.9 | 30.4 | 21.2 | 16.3 | **16.0** | 18.2 | 16.8 |
| Tomatoes | 331 | **17.5** | 20.6 | 29.6 | 20.5 | 19.4 | 18.7 | 20.4 | 20.0 |

Bold marks the lowest MAPE in each row. Naive is lowest on four crops. On both potato series, a machine-learning variant has a lower point MAPE than naive. Per-crop intervals were not computed, so these differences are not tested for significance.

### 5.4 MAE by commodity (KES)

| Crop | n | naive | mean_3m | seasonal | deployed_B | rf_raw | rf_logratio | hgb_logratio | hgb_raw |
|---|---|---|---|---|---|---|---|---|---|
| Cabbage | 274 | **574.0** | 645.2 | 1243.1 | 629.6 | 592.5 | 632.6 | 649.0 | 648.1 |
| Kale | 282 | **295.4** | 336.3 | 547.7 | 347.3 | 353.2 | 319.6 | 329.3 | 350.6 |
| Onions (dry) | 283 | **135.3** | 171.1 | 407.6 | 170.7 | 183.9 | 148.5 | 153.4 | 184.5 |
| Potatoes (Irish, red) | 85 | 311.2 | 400.0 | 847.6 | 433.3 | 342.4 | **307.5** | 333.1 | 310.1 |
| Potatoes (Irish, white) | 253 | 434.5 | 517.1 | 926.2 | 547.3 | 439.6 | **432.4** | 484.7 | 451.4 |
| Tomatoes | 331 | **601.9** | 703.6 | 1010.1 | 741.0 | 662.5 | 669.1 | 712.7 | 690.6 |

On MAE, naive is lowest on four crops. The two potato series are the exceptions, and by very small margins on white potatoes (432.4 vs 434.5 KES).

### 5.5 MAPE by half-year (%)

| Half-year | n | naive | mean_3m | rf_logratio | hgb_logratio | rf_raw | deployed_B |
|---|---|---|---|---|---|---|---|
| 2021H2 | 182 | **16.1** | 18.3 | 17.1 | 19.6 | 17.8 | 17.4 |
| 2022H1 | 322 | **13.6** | 16.3 | 16.1 | 19.0 | 16.8 | 16.7 |
| 2022H2 | 397 | **15.7** | 20.5 | 16.3 | 16.7 | 19.0 | 21.0 |
| 2023H1 | 268 | **19.6** | 23.6 | 20.5 | 21.1 | 22.3 | 24.2 |
| 2023H2 | 79 | 21.7 | 24.6 | **20.2** | 23.6 | 26.6 | 24.4 |
| 2024H1 | 93 | **25.2** | 28.2 | 28.0 | 25.8 | 27.8 | 28.4 |
| 2024H2 | 61 | **20.6** | 30.5 | 22.2 | 24.7 | 25.2 | 32.6 |
| 2025H1 | 56 | 19.1 | **17.7** | 19.2 | 18.8 | 21.8 | 19.2 |
| 2025H2 | 17 | 19.3 | 20.8 | **18.8** | 20.6 | 22.3 | 19.2 |
| 2026H1 | 21 | 22.8 | 24.8 | 22.6 | **20.7** | 23.4 | 27.2 |
| 2026H2 | 12 | 19.7 | **17.1** | 23.3 | 21.0 | 17.8 | 20.0 |

Bold marks the lowest MAPE in the row, among the columns shown. Naive is best in the six larger periods (61 to 397 predictions). A non-naive method is best in five periods (2023H2, 2025H1, 2025H2, 2026H1, 2026H2), each with 12 to 79 predictions. These periods are too small to show a trend, but they are the most recent ones, so this should be watched as more data arrives.

### 5.6 Bootstrap: is each method significantly worse than naive?

Values are the MAPE difference (method minus naive) in percentage points. Positive means worse than naive.

| Method | 2.5th percentile | Median | 97.5th percentile | Verdict |
|---|---|---|---|---|
| mean_3m | +2.16 | +3.52 | +4.83 | Significantly worse |
| rf_raw | +2.13 | +2.98 | +3.77 | Significantly worse |
| rf_logratio | +0.42 | +1.15 | +1.93 | Significantly worse (narrowest margin) |
| hgb_logratio | +1.19 | +2.37 | +3.54 | Significantly worse |
| hgb_raw | +2.75 | +3.66 | +4.47 | Significantly worse |
| deployed_B | +2.44 | +3.91 | +5.37 | Significantly worse |

Every interval is entirely above zero. None of the tested methods is significantly better than naive.

---

## 6. Why the naive forecast wins

These points are reasoned from the files, not from outside studies.

1. **The model only sees its own price history.** Its inputs are past prices (lags, averages, volatility), a calendar, and crop, market and location codes. It has no weather, fuel, exchange-rate or harvest information. The honest model's top feature is last month's price (0.838, section 3), so most of its signal is the same as naive. The rest of its inputs add variation, which adds error.
2. **Perishable crops change month to month, and last month's price is a strong guide.** The model's own metadata labels these crops perishable (section 2). For these crops, the best single guess is often the most recent observed price.
3. **Flexible models need more signal than this data gives them.** The test has 1,508 predictions, spread over 91 series and 60 months. The models can fit patterns in earlier data that do not carry over. Retrained models do better than the deployed model fed honest inputs (best 18.55% for `rf_logratio`, against 21.32%), but still do not reach naive (17.39%).
4. **The leak hid this.** Once the leak is removed, the apparent advantage of the model over naive disappears (section 3, table item 3).

---

## 7. Error bands for the champion (naive forecast)

The naive forecast is a single number, last month's price. It is more honest to show a range around it, based on how wrong it has been.

**Error measures used.**
- **APE (absolute percentage error):** the size of the miss as a share of the actual price, ignoring direction.
- **Signed error:** (prediction − actual) / actual. Negative means the prediction was too low (prices rose); positive means too high (prices fell).

**Table 7.1: naive forecast error by crop (from `error_quantiles_pct.csv`, %).** The file gives APE at the 50th, 80th, 90th and 95th percentiles. It does **not** give the 10th percentile of APE, so none is shown here.

| Crop | n | APE p50 | APE p80 | APE p90 | APE p95 | Signed p05 | Signed p10 | Signed p25 | Signed p75 | Signed p90 | Signed p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| All crops | 1508 | 11.8 | 26.3 | 38.1 | 50.0 | -31.0 | -23.5 | -12.2 | 11.3 | 31.0 | 45.5 |
| Cabbage | 274 | 13.9 | 30.5 | 43.5 | 52.0 | -34.1 | -25.7 | -13.3 | 14.8 | 39.0 | 50.7 |
| Kale | 282 | 13.9 | 32.3 | 45.6 | 55.4 | -39.9 | -29.3 | -13.4 | 13.9 | 34.5 | 48.7 |
| Onions (dry) | 283 | 9.8 | 21.9 | 30.1 | 38.3 | -24.9 | -20.0 | -10.2 | 8.0 | 24.5 | 36.0 |
| Potatoes (Irish, red) | 85 | 9.1 | 21.0 | 26.0 | 34.6 | -26.6 | -22.1 | -14.3 | 6.2 | 19.3 | 24.6 |
| Potatoes (Irish, white) | 253 | 12.5 | 25.5 | 35.4 | 47.6 | -29.8 | -25.6 | -14.4 | 9.2 | 23.6 | 41.8 |
| Tomatoes | 331 | 11.4 | 24.6 | 35.2 | 44.7 | -28.7 | -19.8 | -10.9 | 12.5 | 31.6 | 44.1 |

**Turning this into a price range.** Define:
- P = last month's observed price (the champion's forecast).
- e = signed error, with the 10th and 90th percentiles e10 and e90 from Table 7.1.

Since e = (P − actual) / actual, the actual price is P / (1 + e). So an 80% band (the middle 80% of historical outcomes) runs from **P / (1 + e90)** to **P / (1 + e10)**. The band is derived from the table, not stated in it.

**Table 7.2: derived 80% band multipliers (applied to P).**

| Crop | Lower multiplier | Upper multiplier | Example: if P = KES 1,000, the band is |
|---|---|---|---|
| All crops | 0.763 | 1.307 | KES 763 to KES 1,307 |
| Cabbage | 0.719 | 1.346 | KES 719 to KES 1,346 |
| Kale | 0.743 | 1.414 | KES 743 to KES 1,414 |
| Onions (dry) | 0.803 | 1.250 | KES 803 to KES 1,250 |
| Potatoes (Irish, red) | 0.838 | 1.284 | KES 838 to KES 1,284 |
| Potatoes (Irish, white) | 0.809 | 1.344 | KES 809 to KES 1,344 |
| Tomatoes | 0.760 | 1.247 | KES 760 to KES 1,247 |

**How to explain this to farmers.** "The best estimate for next month is about what this month's price was. In past months, the actual price landed inside this range about 8 times in 10 for this crop. For cabbage, that means between 72% and 135% of this month's price." The range is wider for kale and cabbage, and narrower for red potatoes and onions.

**Limits.** The bands come from 85 to 331 past predictions per crop (1,508 overall), from Wholesale prices only. They assume next month looks like the past 5 years. They need to be recalculated monthly (section 9). The band can only be shown when last month's price is observed.

---

## 8. What could beat the naive forecast

Each candidate must pass the same gate: add it to a challenger, run the same walk-forward test (60 months, retrain every 6), and promote only if the 95% bootstrap interval for the MAPE difference against naive lies **entirely below zero**. The candidates are listed in priority order, but none has been tested here.

| Candidate input | Why it might help | How to test | Data status |
|---|---|---|---|
| Rainfall and vegetation index (satellite) | Supply shocks from drought or flood may move prices before they reach the market | Add monthly rainfall and vegetation features (past months only) to the challenger; run the gate | Not in the files. Source and availability unknown |
| Fuel prices | Transport costs feed into market prices | Same as above | Not in the files |
| KES/USD exchange rate | Imported produce and inputs are priced in dollars | Same as above | Not in the files |
| Harvest calendar | Prices fall at harvest and rise in the lean season | Add a month-of-season indicator based on known harvest windows | Not in the files. The `sin_month`/`cos_month` features already encode season, so the test should check whether a harvest-specific signal adds anything |
| Cross-market prices | A price move in one market may lead another | Add last month's price in neighbouring markets (past months only) | Available in principle from the same WFP file; not tested |
| More frequent data | Monthly data may hide weekly movements | Requires a different data source | Not in the files |

**Two guard rails for any candidate:**
1. Features must be built from the past only. A leakage test (section 9) must pass before a challenger is trained.
2. A challenger that wins only in a few months should not be promoted. Check the half-year table as well as the overall result.

---

## 9. Self-healing retraining plan (overview)

"Self-healing" here means the system checks itself each month and keeps the safe forecast if the new model is not clearly better. It does not mean the model fixes itself unattended.

**Monthly job:**
1. **Download** the latest WFP file from HDX.
2. **Validate** it: expected columns, KES currency, no duplicate series-months, no negative or zero prices, and the latest month present. Stop and report if any check fails.
3. **Recompute** the champion's error bands (Table 7.1 and 7.2) on the latest walk-forward window.
4. **Run a leakage test** on the feature code: changing the price in month *t* must not change any feature used to predict month *t*. Fail the job if it does.
5. **Retrain** the challengers (honest features only) on all data before the test window.
6. **Run the gate** (section 8): compare each challenger with naive on the same walk-forward test, with bootstrap intervals.
7. **Decide.** Promote a challenger only if its interval is entirely below zero. Otherwise keep naive as champion.
8. **Write a run report** (markdown) with the numbers, the decision, and any failed checks.
9. **Human review.** Each run opens a pull request with the report. A person approves any change to the champion, the bands, or the features.

**Failure behaviour.** If data validation fails or the latest month is missing, the champion and its bands stay as they were, and the run report says why. A stale forecast should be flagged to users as stale, rather than silently continued.

---

## 10. Caveats and limitations

1. **Sample size.** 1,508 predictions over 60 months is modest. Per-crop samples are as small as 85 (Potatoes, red). Half-year samples go down to 12.
2. **Overlapping data.** Predictions in the same month and crop are related. The monthly bootstrap handles this at the month level, but the effective sample is smaller than 1,508 rows. This report does not compute an effective sample size.
3. **Only Wholesale, only six crops.** Retail prices and other crops in the dataset were not tested. Findings should not be extended to them.
4. **No per-crop significance tests.** The point differences on Potatoes (red and white) are not tested.
5. **2020 is an outlier year.** It is outside the test window. In the reconstructed frame (`leak_test_output.txt`), 2020 has only 13 rows, a deployed-model error of 92.5% and a naive error of 345.1%. Exclude 2020 from any error bands unless a reason is found.
6. **Deployed model training cutoff: unknown.** The by-year error table (`leak_test_output.txt`, deployed model with leaked inputs) does not reveal the cutoff clearly. Every year from 2007 to 2019 is between 5.9% and 15.0% (2006: 21.9%). The errors are 92.5% in 2020 (n = 13), 16.2% in 2021, 9.5% in 2022, 12.1% in 2023, 15.6% in 2024, 22.7% in 2025 and 18.1% in 2026. This is not conclusive.
7. **Deployed model settings and scikit-learn version mismatch.** Loading the `.pkl` with scikit-learn 1.9.1 confirmed 100 trees, max depth 15, minimum samples to split 5, `random_state` 42, and 19 features; scikit-learn warned that the file was saved with version 1.4.2. It still loads, but a newer scikit-learn may eventually refuse old pickles, which is one more reason not to depend on this file. The backtest's retrained models ran on scikit-learn 1.9.1.
8. **Unreconciled figure: 68.0% holdout MAPE.** In `leak_evidence_output.txt`, the model retrained on honest inputs (train before 2023, test 2023 onward) shows 68.0% MAPE. The walk-forward random forest for the same period (`rf_raw`, by half-year, 2023H1 to 2026H2) ranges from 17.8% to 27.8%, and the deployed model with honest inputs scores 26.31% on the reconstructed frame (section 3). The listed files do not explain the gap. It is not used as a headline number in this report. The leak-test script should be checked before it is cited.
9. **Test-set gaps.** "Potatoes (Irish)" at Nairobi and the Spinach and Bananas series do not appear in the test window. The reason is not in the files.
10. **Missing previous month.** The naive forecast needs last month's price. Where it is missing, the champion has no forecast. The system needs a stated fallback for that case; none was tested.
11. **Data date.** The "last updated 2026-11-04" date is from the brief and was not verified.
12. **Unused run.** `backtest_output_96m.txt` exists but is empty (0 bytes), so no 96-month run is reported. The backtest script writes its results files under fixed names with no run suffix, so the results files reflect whichever run finished last. The 60-month output (`backtest_output_60m.txt`) matches the results files (n = 1,508, test window 2021-10 to 2026-09).

---

## 11. Reproducibility

All scripts are in `C:\Users\bison\AppData\Local\Temp\claude\f96cae34649d9124\ba99f955-248b-406c-9ac9-32827b07f351\scratchpad\backtest\`. Paths are hard-coded in `common.py`, so the scripts only run on the original machine unless those paths are changed.

| Output file | Produced by | Contents used in this report |
|---|---|---|
| `common.py` | Shared module (not an output) | Data filtering, feature construction (leaked and honest), artifact loading |
| `leak_test_output.txt` | `leak_test.py` | Deployed-model importances, correlations, leaked-vs-honest fit, by-year table, sensitivity and random-input tests (section 3, 10) |
| `leak_evidence_output.txt` | `leak_evidence.py` | Retrained leaked-vs-honest comparison, 2023+ holdout (section 3, 10) |
| `backtest_output_60m.txt` | `backtest.py` (60 months, retrain every 6, inferred) | Sample description, all tables for sections 5.3 to 5.5, bootstrap printout |
| `results.md`, `results.csv` | `backtest.py` | Overall table (5.1) and the per-crop and per-type tables |
| `results_common_sample_with_seasonal.csv` | `backtest.py` | Common-sample table (5.2) |
| `bootstrap_vs_naive.csv` | `backtest.py` | Bootstrap table (5.6) |
| `error_quantiles_pct.csv` | `backtest.py` | Error quantiles and derived bands (section 7) |
| `test_predictions.csv` | `backtest.py` | Row-level predictions (not analysed in this report; available for audit) |
| `backtest_output.txt` | Earlier `backtest.py` run | Not used in this report |
| `backtest_output_96m.txt` | Unfinished run | Empty; not used |
| `explore.py`, `explore2.py`, `leak1.py`, `inspect_art.py` | Exploration scripts | Not used in this report |

**To reproduce the 60-month results:** run `backtest.py` with arguments `60 6` under `python -I`, with scikit-learn installed, from the backtest folder. Then run `leak_test.py` and `leak_evidence.py` for the leak tests.

**Input files:** `wfp_data\wfp_food_prices_ken.csv` (3,793,902 bytes) and the four artifacts in the repo's `price_api\artifacts\` folder (`random_forest_price_model.pkl`, `feature_columns.pkl`, `label_encoders.pkl`, `preprocessing_info.pkl`). The repo was read only, not changed.
