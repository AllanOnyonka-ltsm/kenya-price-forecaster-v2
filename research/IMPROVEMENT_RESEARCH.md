# Make the swarm earn its place against naive_last

The most useful improvements to kenya-price-forecaster-v2 are statistical, not agentic. The project should tighten its evaluation (seasonal-naive baselines, MASE against `naive_last`, scoring only on observed months), add a global LightGBM, wrap every model in adaptive conformal intervals, and bring in World Bank RTFP prices, CHIRPS rainfall and EPRA fuel prices as features. The multi-agent swarm and any MiroFish-style simulation should stay a gated, experimental challenger and a scenario/narrative layer. They should not become the point forecaster. The evidence points one way. **MiroFish has no published accuracy evaluation**, and the only head-to-head test found showed it was wrong and added nothing beyond a single LLM call ([WPP Research](https://cms.research.wpp.com/?p=1700)). When independent researchers re-tested the stock-trading agent swarms that the project's design is adapted from, the reported advantages shrank or disappeared once the tests ran past the model's knowledge cutoff or charged costs ([Profit Mirage](https://arxiv.org/abs/2510.07920); [The Alpha Illusion](https://www.alphaxiv.org/abs/2605.16895.md)). The research turned up no study that tests an LLM debate system on monthly food prices against seasonal naive. Monthly crop series also make contamination worse: a backtest long enough to mean anything will almost entirely fall before the LLM's training cutoff. So the only clean test of the swarm is a prospective, timestamped forecast log. On running agents without API keys: `claude -p` on a personal Pro/Max login is a documented route for the developer's own local experiments. Anything that answers other people, including the WhatsApp router, must use an API key under the Commercial Terms, or a local model.

## Prioritised repo improvements

Effort labels are my own estimates for one developer who already knows the codebase. **S** means up to about a day, **M** a few days, and **L** one to two weeks or more. None of these items has been tested on this project's data. Each one should be promoted only if the project's own walk-forward backtest supports it.

1. **Fix evaluation hygiene before adding any model.** Add a seasonal-naive baseline (12-month lag) and a drift baseline next to `naive_last`. Report MASE/RMSSE relative to `naive_last` and seasonal naive per horizon (1, 3 and 6 months), commodity and price type. Score only months that were actually observed, never forward-filled or imputed values.
   - *Why:* a challenger can only beat the champion on a scale where the baselines are explicit. Published Kenyan studies mostly leave naive baselines out, which makes their claims hard to interpret.
   - *Effort:* S.
   - *Evidence:* GIFT-Eval normalises every score to seasonal naive ([arXiv 2610.17794](https://arxiv.org/pdf/2610.17794)). Kenyan ML studies compare against linear models, not naive ones ([UoN maize ANN study](https://uonjournals.uonbi.ac.ke/ojs/index.php/ajps/article/view/1016); [Frontiers in AI 2025](https://www.frontiersin.org/articles/10.3389/frai.2025.1661989)).

2. **Put the swarm behind a contamination-safe challenger protocol.** Register the swarm as a challenger only. Run it alongside three ablation arms on identical inputs: a single LLM call, self-consistency (the median of independent samples), and homogeneous versus heterogeneous model mixes. Score only months after each model's knowledge cutoff, or use a prospective log where each forecast is written with a timestamp before the outcome is known. Record token cost per forecast. Use Diebold–Mariano-style tests across series.
   - *Why:* the trading swarm results the design borrows from rest on short backtests that fall before the models' cutoffs. Telling a model to "ignore" later knowledge does not work.
   - *Effort:* M.
   - *Evidence:* [Tan et al., NeurIPS 2024](https://arxiv.org/abs/2406.16964); [Profit Mirage](https://arxiv.org/abs/2510.07920); ["simulated ignorance fails"](https://arxiv.org/pdf/2601.13717); [Wisdom of Deliberating AI Crowds](https://arxiv.org/html/2512.22625v1).

3. **Add adaptive conformal prediction intervals to the walk-forward loop.** Work with log-price residuals. Use pooled split conformal per commodity × price type, then Adaptive Conformal Inference (ACI) per series or group. Track empirical 80% and 95% coverage, interval width and pinball loss per horizon, and show the bands in the React UI.
   - *Why:* ACI guarantees long-run coverage under distribution shift for any point or quantile model, `naive_last` included. That means the current champion can get honest intervals immediately.
   - *Effort:* S–M.
   - *Evidence:* [Gibbs & Candès 2021](https://arxiv.org/abs/2106.00170v3); [Gibbs & Candès 2024](https://arxiv.org/pdf/2208.08401v2).

4. **Train one global LightGBM across all market × commodity × price-type series.** Features:
   - lags 1, 2, 3 and 12, plus rolling means;
   - month-of-year;
   - the national median price of the same commodity;
   - a months-since-last-observation feature;
   - lags left as NaN when the month is missing, which LightGBM handles natively.

   Quantile objectives at 0.1, 0.5 and 0.9 can then be conformalised (step 3).
   - *Why:* short, gappy panels defeat per-series ARIMA/ETS. Cross-learning across series was the winning pattern in M5.
   - *Effort:* M.
   - *Evidence:* [M5 paper](https://statmodeling.stat.columbia.edu/wp-content/uploads/2021/10/M5_accuracy_competition.pdf) (the claim that 45 of the top 50 used LightGBM comes from secondary sources); the caveat that cross-learning helps mainly on denser series ([arXiv 2009.07701](https://arxiv.org/pdf/2009.07701.pdf)).

5. **Ingest World Bank RTFP Kenya (233 markets, 2007 to Mar 2026) as a feature or auxiliary target.**
   - *Why:* it is gap-filled with ML imputation, updated weekly, and comes with open/high/low/close uncertainty. Never score forecasts against RTFP's imputed values, or the test will reward the imputation model.
   - *Effort:* S–M.
   - *Evidence:* [Microdata catalog 6167](https://microdata.worldbank.org/index.php/catalog/6167/data-dictionary); [World Bank Data Blog](https://blogs.worldbank.org/en/opendata/new-machine-learning-method-provides-real-time-estimates-local-food-prices-crisis-affected).

6. **Add CHIRPS rainfall anomalies and a season-phase calendar.** Season-phase flags: lean seasons, long-rains harvest (roughly Apr–Aug), short-rains harvest (roughly Sep–Jan) and festive Nov–Dec. For Nairobi and Mombasa, use rainfall in the surplus regions that supply them rather than local rainfall.
   - *Why:* Kenyan maize prices follow two-season harvest timing, and the final CHIRPS product arrives about three weeks after month-end, which suits a monthly model.
   - *Effort:* M.
   - *Evidence:* [IFPRI FSP Kenya Oct 2025](https://www.foodsecurityportal.org/sites/default/files/2026-03/Monthly%20Report_Kenya_October_2025_final.pdf); [UCAR CHIRPS guide](https://climatedataguide.ucar.edu/climate-data/chirps-climate-hazards-infrared-precipitation-station-data-version-2).
   - *Caveat:* no Kenyan study quantifies how much rainfall features improve 1–3-month price forecasts.

7. **Add EPRA pump prices and KES/USD as exogenous drivers.** EPRA's cycle runs from the 15th to the 14th, so map each cycle to calendar months carefully to avoid look-ahead.
   - *Why:* Kenya imports all of its refined fuel, so pump prices pass international prices and exchange-rate moves through to transport costs.
   - *Effort:* S–M.
   - *Evidence:* [The Star on EPRA](https://www.the-star.co.ke/news/2026-06-14-epra-review-how-fuel-will-retail-in-major-towns); [AllAfrica](https://allafrica.com/stories/202601150007.html). The Central Bank of Kenya exchange-rate source was not verified.

8. **Add a zero-shot Chronos-Bolt challenger and a median ensemble** of the top two or three models.
   - *Why:* it is cheap to run, CPU-friendly, and produces native quantiles. Expect modest or no gain over seasonal naive at monthly frequency.
   - *Effort:* S–M.
   - *Evidence:* [arXiv 2506.08113](https://arxiv.org/pdf/2506.08113v2); frequency splits in the [TinyCast](https://arxiv.org/pdf/2608.15767) and [t0](https://arxiv.org/pdf/2609.24559) preprints; on monthly macro data only two of five such models matched a simple AR model ([arXiv 2407.00890](https://arxiv.org/pdf/2407.00890v4)).

9. **Build a pluggable `LLMProvider` with four backends:** a deterministic stub, Ollama/llama.cpp, a `claude -p` subprocess, and the Anthropic API. Route the WhatsApp intent router only to the API or a local model, and add an AI-disclosure message at the start of each session.
   - *Why:* the swarm and the router can then run in CI, offline or in production without subscription credentials ever leaking into served code.
   - *Effort:* M.
   - *Evidence:* [Claude Code legal and compliance](https://code.claude.com/docs/en/legal-and-compliance); [Usage Policy](https://www.anthropic.com/legal/aup).

10. **Redesign the swarm's output as structured market intents and scenario bands.** Agents should not emit price numbers.
    - Personas: farmer, cross-border trader, Wakulima broker, transporter, miller, consumer, NCPB official.
    - Each persona emits a JSON intent such as sell-now %, hold %, expected direction, and substitution.
    - These intents aggregate into supply and demand pressure indices.
    - The indices either tilt a statistical baseline or set its exogenous inputs, per scenario from a shock catalogue (drought, diesel rise, import ban, border closure).
    - Any narrative may only contain numbers that appear in tool output.
    - *Why:* LLM agents converge poorly to market equilibrium, so they should not set prices. MiroFish's report agent does not check its numbers against tool output.
    - *Effort:* L.
    - *Evidence:* [arXiv 2609.02580](https://arxiv.org/abs/2609.02580); [MiroFish report_agent.py](https://raw.githubusercontent.com/666ghj/MiroFish/main/backend/app/services/report_agent.py).

11. **Do not fork MiroFish. Borrow the ideas, or build on OASIS (Apache-2.0).**
    - *Why:* MiroFish is AGPL-3.0, has a hard Zep Cloud dependency, and simulates social media rather than markets.
    - *Effort:* this is a decision, not a build task.
    - *Evidence:* [MiroFish README](https://github.com/666ghj/MiroFish); [WPP Research](https://cms.research.wpp.com/?p=1700).

12. **Add an ALPS-style price-spike alert** as a separate output from the point forecast.
    - *Why:* WFP pairs its forecasts with spike alerts and calls price forecasting "by definition a task prone to error".
    - *Effort:* S–M.
    - *Evidence:* [WFP VAM manual](https://vamresources.manuals.wfp.org/docs/food-price-forecasting-and-alert-for-price-spikes).

13. **Phase 2: a KAMIS scraper with unit, grade and outlier validation**, cross-checked against FEWS NET and FAO FPMA.
    - *Why:* KAMIS is designed to cover about five markets in each of 47 counties. It has no documented API and its data quality has not been audited.
    - *Effort:* L.
    - *Evidence:* [KAMIS About](https://kamis.kilimo.go.ke/index.php/site/about); [FEWS NET API help](https://help.fews.net/fdw/fews-net-api).

## MiroFish is a viral discourse simulator, not a price model

MiroFish (github.com/666ghj/MiroFish) calls itself "a simple and universal swarm intelligence engine, predicting anything." It is **AGPL-3.0**, built on the CAMEL-AI team's OASIS engine, and incubated by Shanda Group ([MiroFish README](https://github.com/666ghj/MiroFish)). Its author is Guo Hangjiang ("BaiFu"), reportedly a fourth-year university student who previously built BettaFish. The repo was created on 26 Nov 2025, shipped release 0.1.2 on 7 Mar 2026, and reached #1 on GitHub's global trending list that month ([Pillitteri](https://pasqualepillitteri.it/en/news/21443/does-mirofish-really-work-agent-swarm-alternatives)). A snapshot on 22 Nov 2026 showed **86.3k stars, 13.1k forks and 322 commits** ([GitHub](https://github.com/666ghj/MiroFish)). It reportedly received 30M RMB of investment, but the sources disagree on the investor: Phemex names Chen Tianqiao personally and Preqin names Shanda Capital, and no primary press release was found ([Phemex](https://phemex.com/news/article/students-ai-engine-mirofish-secures-30-million-rmb-investment-65330); [Preqin](https://www.preqin.com/data/profile/asset/mirofish/793003)). Phemex also reports that the engine was built in about ten days using "vibe coding." With 0.1.x versioning, a single lead developer and roughly a year of history, this is an early-stage project. Its star count measures attention, not validation.

The architecture is a five-stage pipeline:

1. An LLM builds an ontology and a GraphRAG graph in Zep Cloud from seed documents.
2. Entities are extracted, and personas and a simulation configuration are generated.
3. OASIS runs simulated **Twitter and Reddit** platforms in parallel subprocesses. The default is 72 simulated hours at 30 minutes per round, or 144 rounds. Each agent action, such as CREATE_POST or LIKE_POST, is logged and pushed back into Zep as temporal memory.
4. A ReACT-style ReportAgent plans a 2–5-section outline and writes each section with four tools, including one that interviews running agents.
5. The user can then chat with any agent or with the ReportAgent.

Sources: [simulation_runner.py](https://raw.githubusercontent.com/666ghj/MiroFish/main/backend/app/services/simulation_runner.py); [report_agent.py](https://raw.githubusercontent.com/666ghj/MiroFish/main/backend/app/services/report_agent.py).

The stack is a Python 3.11–3.12 backend managed by `uv`, a Node 18+ frontend, and Docker Compose. Configuration needs `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL_NAME` and `ZEP_API_KEY`, and any OpenAI-compatible model works; the README recommends qwen-plus ([README](https://github.com/666ghj/MiroFish)). The key architectural fact is that **the simulated world is a social-media feed**. It has no order book, inventory, transport, production or price-formation mechanism. Any "prediction" is the ReportAgent's LLM reading of simulated discourse.

Running it is fragile and costly. WPP Research found that **Zep is a hard dependency with no switch-off**. Its free tier ran out after three runs and killed a simulation mid-run. A hardcoded max-tokens limit truncated the ontology JSON, the Docker image was stale, and the UI was Chinese-only. WPP also could not set the agent count directly: it is derived from the ontology, and when they asked for 100 agents they got about 25 ([WPP Research](https://cms.research.wpp.com/?p=1700)). Their whole experiment cost about $1 on gemini-2.5-flash-lite. One secondary report describes a 40-round, 52-agent run that took about 40 minutes with "very aggressive" token use ([Pillitteri](https://pasqualepillitteri.it/en/news/21443/does-mirofish-really-work-agent-swarm-alternatives)). For local operation there are community forks. MiroFish-Offline replaces Zep with Neo4j and the hosted LLM with Ollama; its default qwen2.5:32b needs substantial VRAM, and 14b is the lighter option ([MiroFish-Offline](https://github.com/nikmcfly/MiroFish-Offline)). Another fork, amadad/mirofish, uses KuzuDB ([amadad/mirofish](https://github.com/amadad/mirofish)). Cost grows roughly with agents × rounds × (actions + memory writes). Running one simulation per market × commodity × month is therefore not practical. A few aggregate scenario runs per month is.

The evidence on accuracy is close to nil. The README makes no benchmark claims beyond adjectives, and a backtesting proposal (#813) had no comments as of October 2026. Issue #17 reports that conclusions change when the model changes. Issue #529 reports the ReportAgent writing its own fake tool results, which is consistent with the `_strip_fake_tool_results` mitigation now in the code. I read these issue details only through a secondary source ([Pillitteri](https://pasqualepillitteri.it/en/news/21443/does-mirofish-really-work-agent-swarm-alternatives)). In WPP's single-day test on 14 May 2026, the S&P 500 rose 0.77%. **Both MiroFish and a single gemini-2.5-flash call predicted it would fall.** None of MiroFish's five ticker picks was among the actual top five gainers, and WPP judged the picks "consistent with random selection." WPP also noted that one day is far too small a sample ([WPP Research](https://cms.research.wpp.com/?p=1700)). The structural critique is straightforward: when one model plays every agent, its errors repeat across agents instead of cancelling, so the number of agents is not a statistical sample. OASIS's own paper shows that it reproduces qualitative social phenomena such as information spread, polarisation and herd effects. It does not show numeric forecasting skill ([arXiv 2411.11581](https://arxiv.org/abs/2411.11581)).

### What transfers to Kenyan crop prices

Four ideas transfer, if they are reworked.

- **A seed knowledge graph of the market system.** Sources: KAMIS bulletins, FEWS NET outlooks, KMD seasonal forecasts, EPRA reviews, import and duty notices, NCPB purchase prices. Entities: markets such as Wakulima, Kongowea, Kisumu and Eldoret, plus border routes.
- **Personas tied to price behaviour, not generic social-media users.** Examples: an Uasin Gishu smallholder deciding whether to store or sell, a Busia or Namanga cross-border trader, a lorry transporter passing on diesel costs, an urban low-income consumer. Each needs quantitative attributes such as stock held, costs and margins.
- **A shock catalogue.** Failed long rains, a diesel price rise, a maize import ban or a duty-free import window, festive or school-term demand, border closure, fall armyworm, and currency depreciation for imported rice and wheat.
- **A guarded narrative generator.**

All of these are untested proposals. The essential change is to replace "post/like" actions with structured intents that aggregate into pressure indices, which means building on OASIS or a small custom loop rather than running MiroFish as-is. The closest supporting research is thin and comes from finance:

- TwinMarket (NeurIPS 2025) shows emergent bubbles from LLM investor agents ([arXiv 2502.01506](https://arxiv.org/html/2502.01506v5)).
- Wang et al. report lower MSE on commodity futures from a hierarchical-knowledge agent-based model. The abstract names neither the commodities nor the baseline values ([arXiv 2503.20787](https://arxiv.org/abs/2503.20787)).
- Double-auction experiments show that **LLM-agent markets converge slowly or not at all toward equilibrium** ([arXiv 2609.02580](https://arxiv.org/abs/2609.02580)). This is why agents should feed a baseline rather than set prices.

I found no application of MiroFish or OASIS to food prices, and no LLM-agent study on East African commodities.

### How to evaluate a simulation layer honestly

Unconditional forecasts and what-if bands need different tests. For forecasts, use a rolling-origin backtest in which each origin sees only a frozen set of seed documents dated before it. Compare against `naive_last`, seasonal naive, a historical seasonal profile, the statistical model without simulation features, and a single direct LLM prompt with the same seed, which mirrors WPP's control arm. Report results separately for **shock months** and normal months, because a simulation layer can only plausibly help in shock months.

A what-if band is conditional, so judge it after the fact. Pick the scenario that matches what actually happened, for example the 2022 drought combined with the removal of the fuel subsidy, and check whether its band contained the realised prices. Compare its width and coverage with the unconditional band. Promote the layer only if it shows a significant gain on shock months at an acceptable cost per run. Otherwise, ship it labelled as a non-predictive "analyst narrative." All of this protocol is my inference from the notes. No published study validates it for this domain.

## Debate swarms have not beaten simple baselines on numeric forecasts

The project's swarm design comes from stock-trading agents, and that literature is the weakest part of the evidence. TradingAgents reported AAPL returns of 26.62% with a Sharpe ratio of 8.21 against −5.23% for buy-and-hold. That result came from a roughly three-month backtest in early 2024, with no transaction costs, no repeated runs, and no discussion of whether the models had seen the test period in training. The authors themselves flagged the Sharpe as above the expected range ([arXiv 2412.20138](https://arxiv.org/html/2412.20138v4)). A GitHub issue on the repo raises the memorisation problem directly ([issue #805](https://github.com/TauricResearch/TradingAgents/issues/805)). Independent re-evaluations are consistently negative:

- *Profit Mirage* finds that returns "evaporate" after the GPT-4o cutoff. Search snippets put total-return decay between 50% and 72%, but I did not verify those figures in the full text ([arXiv 2510.07920](https://arxiv.org/abs/2510.07920)).
- *FINSABER* ran two decades of backtests on 100+ symbols and found the reported LLM advantages "deteriorate significantly." The strategies were too timid in bull markets and too aggressive in bear markets ([arXiv 2505.07078](https://arxiv.org/abs/2505.07078)).
- *The Alpha Illusion* ran a one-year reproduction with realistic costs. TradingAgents fell from $106.4K gross to **$102.3K net, below buy-and-hold at about $104.8K**. Net buy-and-hold beat both agents tested on 4 of 5 tickers ([alphaXiv 2605.16895](https://www.alphaxiv.org/abs/2605.16895.md)).
- *FinCAD* names the core problem "parametric look-ahead bias": knowledge stored in the model's weights that no audit of the data pipeline can catch ([arXiv 2605.24564](https://arxiv.org/pdf/2605.24564.pdf)).

Evidence that LLMs can forecast numeric time series at all leans negative. LLMTime (NeurIPS 2023) was competitive zero-shot. Its authors found that alignment hurts, with chat models calibrated worse than base models ([arXiv 2310.07820](https://arxiv.org/html/2310.07820v3)). An independent re-test with GPT-3.5 found **ARIMA better on 13 of 18 datasets** ([arXiv 2408.04867](https://arxiv.org/html/2408.04867v1)). Tan et al. showed that removing the LLM from three popular LLM-based forecasters, or replacing it with a single attention layer, made no difference to accuracy or improved it, at lower compute cost ([NeurIPS 2024](https://proceedings.neurips.cc/paper_files/paper/2024/hash/6ed5bf446f59e2c6646d23058c86424b-Abstract.html)). Purpose-built time-series foundation models are a different class from chat LLMs. Chronos-2 beats seasonal naive on almost all fev-bench tasks, though I inferred those column meanings from the CSV layout and did not verify them, and its paper acknowledges partial overlap between its training data and the benchmark ([fev-bench](https://huggingface.co/spaces/autogluon/fev-bench/blob/12a0f014dbead073595f031dae4c7b55b701084b/tables/pairwise_WQL.csv); [arXiv 2510.15821](https://arxiv.org/pdf/2510.15821)).

Deliberation itself helps only narrowly. On 202 Metaculus questions, **only groups of different models sharing information improved, by about 4% in log loss (p=0.017)**. Groups made of three copies of the same model got slightly worse, though not significantly, and the p-values were not corrected for multiple comparisons ([arXiv 2512.22625](https://arxiv.org/html/2512.22625v1)). General debate benchmarks find that debate does not reliably beat self-consistency, and that it often falls below the best single model ([arXiv 2311.17371](https://arxiv.org/pdf/2311.17371); [arXiv 2510.20963](https://arxiv.org/pdf/2510.20963)). LLMs are gaining on human forecasters in tournaments: FRI projects parity around November 2026, an extrapolation with a 95% interval from Dec 2025 to Jan 2028 ([FRI](https://forecastingresearch.substack.com/p/ai-llm-forecasting-model-forecastbench-benchmark)). But those are binary event questions settled prospectively, a task very unlike monthly price levels.

Agricultural LLM work is sparse and weakly benchmarked. One news-plus-LSTM shock model reports an AUC of 0.94 against baselines below 0.5, which is a red flag, and has no statistical time-series baseline ([arXiv 2508.06497](https://arxiv.org/abs/2508.06497)). I found no study of an LLM on WFP, FEWS NET or GIEWS prices.

Three implications follow for this project:

- Because the data is monthly, a meaningful backtest will be almost entirely pre-cutoff, so contamination is worse than in daily trading.
- Any swarm gain must survive ablation against a single LLM and against self-consistency.
- If debate is used, the models in it should be heterogeneous.

The realistic place for LLM value is in **extracting signals from text** (policy bans, drought news) to feed a numeric model, or in judgmental adjustment around shocks. Producing the numbers directly is not that place.

## Data, models and intervals that fit sparse Kenyan panels

The `naive_last` champion is consistent with how monthly staple prices behave, which is close to a random walk with seasonal drift. Published Kenyan work offers no evidence that ML beats a naive model, because none of it reports one. A 2025 Frontiers study on the same 2006–2024 WFP span reports a MAPE of 125.251 according to the search snippet. A MAPE above 100% suggests either a reporting quirk or very poor accuracy, so treat the study with caution ([Frontiers in AI](https://www.frontiersin.org/articles/10.3389/frai.2025.1661989)). WFP's own operational tool pairs forecasts with ALPS spike alerts ([WFP VAM](https://vamresources.manuals.wfp.org/docs/food-price-forecasting-and-alert-for-price-spikes)).

### Data sources

| Source | Kenya coverage and access | Caveats |
|---|---|---|
| WFP via HDX | Search metadata gives 2006 to Aug 2024 with monthly updates ([HDX](https://data.humdata.org/dataset/wfp-food-prices-for-kenya)) | The page returned 403 to my fetch. A Feb 2026 listing suggests the HDX metadata is stale ([ReliefWeb](https://response.reliefweb.int/kenya/food-security/data)). Licence unconfirmed. |
| World Bank RTFP | 233 markets, 2007 to Mar 2026, updated weekly, ML gap-fill with OHLC estimates ([Microdata](https://microdata.worldbank.org/index.php/catalog/6167/data-dictionary)) | Estimates are revised as new data arrives. Do not score forecasts against imputed values. |
| KAMIS | Designed for about 5 markets × 47 counties, 150+ products, wholesale, retail and farm-gate prices ([KAMIS](https://kamis.kilimo.go.ke/index.php/site/about)) | No API, no licence statement, no quality audit. Grade mismatches have been reported (low-authority source). |
| FEWS NET | REST API `marketpricefacts`, which needs a JWT for some data; public CSVs ([FEWS NET API](https://help.fews.net/fdw/fews-net-api)) | Access depends on permissions. Much of the data comes from third parties. |
| FAO GIEWS FPMA | 2,700+ series across 130 countries; listed as CC-BY-4.0 ([FPMA](https://www.fao.org/giews/food-prices/price-tool/en/); [DPG](https://digitalpublicgoods.net/r/food-price-monitoring-and-analysis-fpma-tool)) | Series counts conflict between pages. No public API documentation found. |
| CHIRPS | 0.05°, monthly back to 1981, final product about 3 weeks after month-end ([UCAR](https://climatedataguide.ucar.edu/climate-data/chirps-climate-hazards-infrared-precipitation-station-data-version-2)) | Licence not confirmed. Aggregate to the regions that supply each market. |
| EPRA fuel | Monthly maximum pump prices, cycle runs 15th to 14th ([The Star](https://www.the-star.co.ke/news/2026-06-14-epra-review-how-fuel-will-retail-in-major-towns)) | Map the cycle to calendar months carefully. |

### Models

The model ladder follows the benchmark evidence. GIFT-Eval normalises every score to seasonal naive ([arXiv 2410.10393](https://arxiv.org/html/2410.10393v2)), and foundation-model gains narrow at monthly frequency ([TinyCast](https://arxiv.org/pdf/2608.15767); [t0](https://arxiv.org/pdf/2609.24559)). On monthly FRED data, only Moirai and TimesFM out of five time-series language models matched a simple AR model ([arXiv 2407.00890](https://arxiv.org/pdf/2407.00890v4)).

The recommended sequence is:

1. Seasonal naive.
2. Damped ETS or Theta per series.
3. A global LightGBM with lags and exogenous features, which is the M5 lesson, with the caveat that cross-learning helps most on denser series ([arXiv 2009.07701](https://arxiv.org/pdf/2009.07701.pdf)).
4. Zero-shot Chronos-Bolt.
5. A median ensemble.

N-BEATS and N-HiTS need many long series, so they come after LightGBM.

### Missing months

- Work in log prices.
- Add a months-since-last-observation feature.
- Leave lags as NaN when the month is missing.
- Never forward-fill the target, for training or for scoring.
- When the 12-month-ago value is missing, fall back from seasonal naive to the national median, or to `naive_last`.
- A state-space smoother or RTFP can build features, but scoring stays on observed points.

### Intervals

Intervals are the cheapest large win. ACI updates its miscoverage rate online, α_{t+1} = α_t + γ(α − err_t), and reaches its long-run coverage target with no distributional assumptions ([arXiv 2106.00170](https://arxiv.org/abs/2106.00170v3)). The 2024 follow-up tunes γ automatically ([arXiv 2208.08401](https://arxiv.org/pdf/2208.08401v2)). Per-series calibration sets will be tiny, so pool residuals by commodity × price type. Then conformalise LightGBM or Chronos quantiles in the style of conformalised quantile regression (CQR). The suggested γ range of 0.005–0.05 is a tuning choice with no source behind it. RTFP's use of a Generalized Error Distribution is a reminder that **food prices have fat tails**, so watch tail coverage specifically ([Microdata metadata](https://microdata.worldbank.org/metadata/export/4483/json)). I found no study applying conformal methods to African food prices.

### Scoring

Report MASE against seasonal naive (m=12) and against naive. Report sMAPE as a secondary metric only. Add WQL/CRPS and empirical 80%/95% coverage. Hyndman & Koehler's case for MASE ([DOI](https://doi.org/10.1016/j.ijforecast.2006.03.001)) and Hewamalage et al.'s guide to forecast-evaluation pitfalls ([DOI](https://doi.org/10.1007/s10618-022-00894-5)) are cited from prior knowledge and were not re-fetched for this report.

## A personal Claude login runs experiments, not the farmer bot

`claude -p` uses the Pro/Max subscription login by default. `claude setup-token` creates a one-year token for "CI pipelines, scripts, or other environments," and that token can only make model requests ([Authentication](https://code.claude.com/docs/en/authentication)). There are three traps:

- **`ANTHROPIC_API_KEY` overrides the subscription.** In `-p` mode "the key is always used when present," so calls get billed per token without warning.
- **`--bare` ignores subscription credentials.** It never reads OAuth credentials or `CLAUDE_CODE_OAUTH_TOKEN`, and the docs say it "will become the default for `-p` in a future release" ([Headless docs](https://code.claude.com/docs/en/headless)). Pin the CLI version or retest after each upgrade.
- **Limits are shared and unpublished.** Usage comes out of a five-hour limit and a weekly limit shared with claude.ai, and no numeric caps are published ([support article](https://support.claude.com/en/articles/11145838-using-claude-code-with-your-pro-or-max-plan)).

A swarm making many calls per round can use up a window quickly, so design it to degrade gracefully when it hits rate limits.

The terms draw a clear line around serving other people. The Agent SDK docs say: "Unless previously approved, Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products" ([Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview)). The legal page bars developers from routing "requests through Free, Pro, Max, or Ultra plan credentials on behalf of their users" or from intermediating session tokens. It carves out end users signing in to the *unmodified* Claude Code binary with their own subscription ([Legal and compliance](https://code.claude.com/docs/en/legal-and-compliance)). The Consumer Terms (§3(7)) prohibit scripted access "except when you are accessing our Services via an Anthropic API Key" ([Consumer Terms](https://www.anthropic.com/legal/consumer-terms)). That sits uneasily with Anthropic's own documentation of subscription tokens for scripts, and no official FAQ reconciles the two. **This is an unresolved ambiguity.**

Taken together:

| Use | Status |
|---|---|
| The developer personally running `claude -p` swarm experiments on their own machine | Documented and appears tolerated ("ordinary, individual usage") |
| A cron job or long unattended simulation running all day on a Pro login | Ambiguous |
| The WhatsApp router, a public demo, a shared server, a classmate using the token, extracting OAuth tokens to call the API | Clearly not allowed |

This is not legal advice. Re-check the pages, because they change often.

The compliant paid route is cheap. Claude Haiku 5.5 costs **$0.10 per million input tokens and $0.50 per million output tokens** for prompts up to 100k tokens, with a 50% batch discount and $0.01 cache hits ([Pricing](https://platform.claude.com/docs/en/about-claude/pricing)). On my own estimate, a turn of about 3k input and 500 output tokens costs roughly $0.00055, so a thousand agent turns cost about $0.55 before batching. Newer models produce about 30% more tokens for the same text, so budget for that. New accounts get free credits of unstated size. Claude Campus is closed for Fall 2026 ([Campus](https://claude.com/programs/campus)). A farmer-facing bot also needs an AI disclosure at the start of each session, and should not frame price outlooks as investment advice. The Usage Policy names "investment products," and whether crop-sale timing advice falls under that is unclear ([Usage Policy](https://www.anthropic.com/legal/aup)).

Local models are the free default. Ollama serves an OpenAI-compatible API at `localhost:11434/v1`, with JSON mode, and an Anthropic-compatible `/v1/messages` endpoint that Claude Code can point at; neither supports `tool_choice` ([Ollama OpenAI](https://docs.ollama.com/api/openai-compatibility); [Ollama Anthropic](https://docs.ollama.com/api/anthropic-compatibility)). llama.cpp's `llama-server` adds JSON-schema and GBNF-constrained output. Its README gives the default port as 9931, while many guides say 8080, so check locally ([llama.cpp](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)). The guidance on laptop models comes from blogs and is unverified. Tool-calling reliability reportedly "falls off a cliff below about four billion parameters," and Qwen3-4B-2507, Phi-4-mini and Gemma 4 E4B are the usual small picks ([D-Central](https://d-central.tech/local-llm-agent-capability/)). A 4B model is estimated at about 15–25 tok/s on an 8 GB CPU-only laptop, and an 8B model at about 8–15 tok/s on 16 GB ([PromptQuorum](https://www.promptquorum.com/local-llms/fastest-local-llms-low-end-pcs)). Limit small local models to schema-constrained jobs such as event extraction, scenario selection and summaries, and validate and retry their outputs.

One `LLMProvider` interface ties this together, with four backends:

- **Stub:** returns outputs keyed by a prompt hash, for tests and CI.
- **OpenAI-compatible local:** covers Ollama, llama.cpp and LM Studio. The LM Studio endpoint is unverified.
- **`claude -p` subprocess:**
  - strips `ANTHROPIC_API_KEY` from the child environment and never passes `--bare`;
  - only runs when `ALLOW_SUBSCRIPTION_CLI=1` is set, and refuses when a production flag is set;
  - falls back to the local backend when it hits rate limits.
- **Anthropic API:** Haiku 5.5, using Batch for simulation sweeps. This is the only cloud backend allowed behind WhatsApp or the web.

Log the provider, model and cost on every call so experiments stay comparable across backends. This architecture is my recommendation, not documented Anthropic guidance.

## Conclusion

The project's own history makes the key point: a hard-to-beat `naive_last` is the expected result for monthly staple prices, not a failure to fix with more intelligence. The upgrades most likely to pay off add information the champion lacks: gap-filled RTFP prices, rainfall in the regions that supply each market, fuel and exchange-rate pass-through, and calibrated uncertainty. The value of the agent swarm is different in kind. It can produce structured, inspectable *scenarios*, such as what a failed long rains plus a diesel rise would do to Eldoret maize. Those are conditional statements that a point forecaster cannot make, and they need their own ex-post evaluation instead of a leaderboard rank.

The most important thing to build now is cheap: a timestamped prospective forecast log for every arm, champion and swarm alike. Monthly data gives so few clean post-cutoff test points that each month of delay pushes back the earliest date at which anyone can honestly say whether the swarm works. Start logging before the swarm is finished, so that by the time it ships there is already a baseline record to judge it against.

## Sources

**MiroFish and agent simulation**
- [MiroFish GitHub README](https://github.com/666ghj/MiroFish)
- [MiroFish services directory](https://github.com/666ghj/MiroFish/tree/main/backend/app/services)
- [MiroFish simulation_runner.py](https://raw.githubusercontent.com/666ghj/MiroFish/main/backend/app/services/simulation_runner.py)
- [MiroFish report_agent.py](https://raw.githubusercontent.com/666ghj/MiroFish/main/backend/app/services/report_agent.py)
- [Pillitteri, "Does MiroFish really work?"](https://pasqualepillitteri.it/en/news/21443/does-mirofish-really-work-agent-swarm-alternatives)
- [Phemex News on MiroFish funding](https://phemex.com/news/article/students-ai-engine-mirofish-secures-30-million-rmb-investment-65330)
- [Preqin MiroFish profile](https://www.preqin.com/data/profile/asset/mirofish/793003)
- [YourStory, MiroFish hype vs reality](https://yourstory.com/ai-story/mirofish-ai-explained-hype-vs-reality) (snippet only)
- [WPP Research MiroFish test](https://cms.research.wpp.com/?p=1700)
- [dev.to MiroFish article and comments](https://dev.to/therealmrmumba/everything-you-need-to-know-about-mirofish-the-ai-swarm-engine-predicting-everything-5fp3) (unverified comments)
- [MiroFish-Offline](https://github.com/nikmcfly/MiroFish-Offline)
- [BrightCoding on MiroFish-Offline](https://blog.brightcoding.dev/2026/06/13/stop-paying-cloud-apis-run-insane-multi-agent-simulations-locally-with-mirofish-offline)
- [amadad/mirofish](https://github.com/amadad/mirofish)
- [juejin analysis](https://juejin.cn/post/7649661913547391022)
- [OASIS, arXiv 2411.11581](https://arxiv.org/abs/2411.11581)
- [TwinMarket, arXiv 2502.01506](https://arxiv.org/html/2502.01506v5)
- [Wang et al., arXiv 2503.20787](https://arxiv.org/abs/2503.20787)
- [Competitive market behaviour of LLMs, arXiv 2609.02580](https://arxiv.org/abs/2609.02580)
- [Artificial Leviathan, arXiv 2406.14373](https://arxiv.org/pdf/2406.14373)
- [SimCity, arXiv 2510.01297](https://arxiv.org/pdf/2510.01297v1)
- [Agricultural pricing ABM (DOAJ)](https://doaj.org/article/7be6d44fcdc14a758bd1cf67bb37a70b)

**LLM and multi-agent forecasting evidence**
- [LLMTime, arXiv 2310.07820](https://arxiv.org/html/2310.07820v3)
- [Cao & Wang, arXiv 2408.04867](https://arxiv.org/html/2408.04867v1)
- [Tan et al., NeurIPS 2024](https://proceedings.neurips.cc/paper_files/paper/2024/hash/6ed5bf446f59e2c6646d23058c86424b-Abstract.html)
- [Tan et al., arXiv 2406.16964](https://arxiv.org/abs/2406.16964)
- [fev-bench WQL table](https://huggingface.co/spaces/autogluon/fev-bench/blob/12a0f014dbead073595f031dae4c7b55b701084b/tables/pairwise_WQL.csv)
- [Chronos-2, arXiv 2510.15821](https://arxiv.org/pdf/2510.15821)
- [Cisco TSFM report, arXiv 2511.19841](https://arxiv.org/pdf/2511.19841)
- [TradingAgents, arXiv 2412.20138](https://arxiv.org/html/2412.20138v4)
- [TradingAgents issue #805](https://github.com/TauricResearch/TradingAgents/issues/805)
- [Profit Mirage, arXiv 2510.07920](https://arxiv.org/abs/2510.07920)
- [FINSABER, arXiv 2505.07078](https://arxiv.org/abs/2505.07078)
- [The Alpha Illusion, alphaXiv 2605.16895](https://www.alphaxiv.org/abs/2605.16895.md)
- [Look-Ahead-Bench, arXiv 2601.13770](https://arxiv.org/pdf/2601.13770)
- [FinCAD, arXiv 2605.24564](https://arxiv.org/pdf/2605.24564.pdf)
- [DMO-FinTech 2026 workshop paper](https://academicworkshops.github.io/DMO-FinTech/docs/2026_Paper_LLM-Based_Financial_Multi-Agent_Systems.pdf)
- [Wisdom of Deliberating AI Crowds, arXiv 2512.22625](https://arxiv.org/html/2512.22625v1)
- [Multi-agent debate benchmarks: arXiv 2311.17371](https://arxiv.org/pdf/2311.17371), [arXiv 2509.05396](https://arxiv.org/html/2509.05396v2), [arXiv 2510.20963](https://arxiv.org/pdf/2510.20963)
- [TS-Debate, arXiv 2601.19151](https://arxiv.org/abs/2601.19151v1)
- [MDPI Mathematics dual-agent debate](https://www.mdpi.com/2227-7390/14/8/1393)
- [ForecastBench, arXiv 2409.19839](https://arxiv.org/pdf/2409.19839)
- [FRI ForecastBench update](https://forecastingresearch.substack.com/p/ai-llm-forecasting-model-forecastbench-benchmark)
- [AIA Forecaster, arXiv 2511.07678](https://arxiv.org/pdf/2511.07678v1.pdf)
- [Metaculus Q2 2025](https://www.lesswrong.com/posts/Surnjh8A4WjgtQTkZ/q2-ai-benchmark-results-pros-maintain-clear-lead)
- [Metaculus Q3 2025](https://www.lesswrong.com/posts/LHdNtJCm93pxNHJKb/can-ai-outpredict-humans-results-from-metaculus-s-q3-ai)
- [Ghali et al., arXiv 2508.06497](https://arxiv.org/abs/2508.06497)
- [VPF-MoE, MDPI Agriculture](https://mdpi.com/2077-0472/15/2/162)
- [FedChronos, arXiv 2608.01290](https://arxiv.org/pdf/2608.01290)
- [Simulated ignorance fails, arXiv 2601.13717](https://arxiv.org/pdf/2601.13717)
- [Memorization survey, arXiv 2608.23058](https://arxiv.org/pdf/2608.23058)
- [Hyndman & Koehler 2006](https://doi.org/10.1016/j.ijforecast.2006.03.001) (not re-fetched)
- [Hewamalage et al. 2023](https://doi.org/10.1007/s10618-022-00894-5) (not re-fetched)

**East African data, models and intervals**
- [HDX WFP food prices for Kenya](https://data.humdata.org/dataset/wfp-food-prices-for-kenya)
- [ReliefWeb Response Kenya data](https://response.reliefweb.int/kenya/food-security/data)
- [World Bank RTFP Microdata 6167](https://microdata.worldbank.org/index.php/catalog/6167/data-dictionary)
- [RTFP metadata JSON](https://microdata.worldbank.org/metadata/export/4483/json)
- [World Bank Data Blog on RTFP](https://blogs.worldbank.org/en/opendata/new-machine-learning-method-provides-real-time-estimates-local-food-prices-crisis-affected)
- [Andrée WPS 9886](https://econpapers.repec.org/RePEc:wbk:wbrwps:9886)
- [KAMIS About](https://kamis.kilimo.go.ke/index.php/site/about)
- [FEWS NET API help](https://help.fews.net/fdw/fews-net-api)
- [FEWS NET Kenya monthly prices](https://fews.net/kenya-monthly-fews-net-staple-food-price-data)
- [FAO FPMA tool](https://www.fao.org/giews/food-prices/price-tool/en/)
- [DPG registry FPMA](https://digitalpublicgoods.net/r/food-price-monitoring-and-analysis-fpma-tool)
- [CHIRPS, UCAR guide](https://climatedataguide.ucar.edu/climate-data/chirps-climate-hazards-infrared-precipitation-station-data-version-2)
- [Funk et al. 2015](https://pmc.ncbi.nlm.nih.gov/articles/PMC4672685)
- [The Star, EPRA review](https://www.the-star.co.ke/news/2026-06-14-epra-review-how-fuel-will-retail-in-major-towns)
- [AllAfrica, fuel imports](https://allafrica.com/stories/202601150007.html)
- [GIFT-Eval, arXiv 2410.10393](https://arxiv.org/html/2410.10393v2)
- [GIFT-Eval-CTX, arXiv 2610.17794](https://arxiv.org/pdf/2610.17794)
- [TinyCast, arXiv 2608.15767](https://arxiv.org/pdf/2608.15767)
- [t0, arXiv 2609.24559](https://arxiv.org/pdf/2609.24559)
- [TSLMs on FRED, arXiv 2407.00890](https://arxiv.org/pdf/2407.00890v4)
- [Foundation model benchmark, arXiv 2506.08113](https://arxiv.org/pdf/2506.08113v2)
- [M5 accuracy paper](https://statmodeling.stat.columbia.edu/wp-content/uploads/2021/10/M5_accuracy_competition.pdf)
- [Cross-learning analysis, arXiv 2009.07701](https://arxiv.org/pdf/2009.07701.pdf)
- [Wamalwa & Muchemi, UoN](https://uonjournals.uonbi.ac.ke/ojs/index.php/ajps/article/view/1016)
- [Frontiers in AI 2025 Kenya study](https://www.frontiersin.org/articles/10.3389/frai.2025.1661989)
- [Zindi agriBORA](https://zindi.africa/competitions/agribora-commodity-price-forecasting-challenge)
- [WFP VAM price forecasting and ALPS](https://vamresources.manuals.wfp.org/docs/food-price-forecasting-and-alert-for-price-spikes)
- [IFPRI FSP Kenya Oct 2025](https://www.foodsecurityportal.org/sites/default/files/2026-03/Monthly%20Report_Kenya_October_2025_final.pdf)
- [IFPRI FSP East Africa May 2025](https://www.foodsecurityportal.org/sites/default/files/2026-04/Monthly%20Report_East%20Africa_May%202025_final.pdf)
- [ACI, arXiv 2106.00170](https://arxiv.org/abs/2106.00170v3)
- [Conformal under arbitrary shift, arXiv 2208.08401](https://arxiv.org/pdf/2208.08401v2)

**Running LLMs without API keys**
- [Claude Code headless docs](https://code.claude.com/docs/en/headless)
- [Claude Code authentication](https://code.claude.com/docs/en/authentication)
- [Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview)
- [Claude Code legal and compliance](https://code.claude.com/docs/en/legal-and-compliance)
- [Using Claude Code with Pro or Max](https://support.claude.com/en/articles/11145838-using-claude-code-with-your-pro-or-max-plan)
- [Anthropic Consumer Terms](https://www.anthropic.com/legal/consumer-terms)
- [Anthropic Usage Policy](https://www.anthropic.com/legal/aup)
- [Claude API pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- [Claude Campus](https://claude.com/programs/campus)
- [AI for Science program](https://support.claude.com/en/articles/11199177-anthropic-s-ai-for-science-program) (snippet only)
- [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)
- [Ollama Anthropic compatibility](https://docs.ollama.com/api/anthropic-compatibility)
- [llama.cpp server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
- [D-Central local agent capability](https://d-central.tech/local-llm-agent-capability/) (secondary)
- [PromptQuorum low-end PC LLMs](https://www.promptquorum.com/local-llms/fastest-local-llms-low-end-pcs) (unverified estimates)
- [HackerNoon CPU benchmark](https://hackernoon.com/running-nvidia-nemotron-35-bolt-e4b-without-a-gpu-benchmark-results)
