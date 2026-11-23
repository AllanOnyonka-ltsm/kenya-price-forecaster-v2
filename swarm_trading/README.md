# Stock Trading Agent Swarm

A multi-agent stock trading simulation that replays historical daily bars. Ten
agents (3 analysts, 4 traders, 2 risk managers, 1 reporter) communicate over a
message bus. The simulation is a research tool. It is not financial advice, and
simulated results do not predict real trading results.

## Quick start

Requires Python 3.12. Run all commands from this directory.

```bash
python -m venv venv
venv\Scripts\activate            # Windows (Linux/macOS: source venv/bin/activate)
pip install -r requirements.txt

# 1. Offline demo on the bundled real sample (3 tickers, H1 2023). No network needed.
python src/main.py --offline --data-dir data/sample --symbols AAPL,MSFT,JNJ \
    --start 2023-01-01 --end 2023-07-01 --seed 42

# 2. Offline test suite (no network needed)
python -m unittest discover -s tests
```

To run on the default 10 tickers, the first run downloads them from Yahoo
Finance into `data/historical/` (needs network). Later runs read the cache.
Add `--offline` to refuse downloads. In that case a missing cache is an error
with a message, not a crash.

```bash
python src/main.py                       # uses cache, downloads missing symbols
python src/main.py --offline             # cache only
```

## Command-line options

| Option | Default | Meaning |
|---|---|---|
| `--symbols` | 10 mega-caps | comma-separated tickers |
| `--start` / `--end` | 2023-01-01 / 2024-01-01 | date range; start inclusive, end exclusive |
| `--seed` | 42 | seeds every RNG (`random`, `numpy`, and each analyst's generator) |
| `--initial-cash` | 1000000 | total capital, split equally across 4 traders |
| `--data-dir` | `data/historical` | folder of `<SYMBOL>.csv` files |
| `--offline` | off | never touch the network |
| `--reports-dir` | `reports/` | where `daily_pnl.json` and `trades_history.csv` are written |
| `--no-reports` | off | skip writing report files |
| `--bus` | `memory` | `memory` (deterministic) or `redis` (optional, see below) |

Exit status: 0 on success, 2 when no usable market data is available.

## How a trading day runs

Each simulated day runs five steps with a full message drain after each one.
There are no sleeps and no wall-clock timers.

1. Fill or expire orders queued on earlier bars, at this bar's open.
2. Publish the bar (open, high, low, close). Agents react.
3. Publish `bar_close`. Risk managers check stop-losses.
4. Record a portfolio snapshot at the close.

A signal made at the close of day t can only fill at the open of a later day.
Fill price is the open. If the open is missing, the close of that bar is used,
and the trade record says so (`price_source`).

## Agents

- **AnalystAgent**: turns a bar's open-to-close return into a BULLISH, BEARISH or NEUTRAL signal. Emits on a seeded 30% gate.
- **TraderAgent**: owns a portfolio for its symbols. It sizes orders, reserves cash and shares while orders are open, and liquidates on stop-loss alerts.
- **RiskManagerAgent**: checks each order (size, cash, shares, portfolio exposure). It keeps its own ledger per trader and raises stop-loss alerts when a position is more than 5% below average cost.
- **ReporterAgent**: records trades, decisions, stop-losses and snapshots, and writes the report.

An order is approved only when both risk managers approve it. A rejection from
either one rejects it.

## P&L

`core/portfolio.py` uses average-cost accounting, with the buy commission
included in cost basis. Realised P&L comes from sells. Unrealised P&L is marked
at each bar's close. Total P&L equals total value minus initial cash, and a
unit test checks that identity.

## Data

- `data/sample/` holds three real Yahoo Finance daily series (AAPL, MSFT, JNJ, 2023-01-01 to 2023-06-30). Refresh with `python scripts/fetch_sample_data.py`.
- Timestamps are normalised with `utc=True`, then converted to naive midnight UTC. This handles the -05:00 and -04:00 offsets across the DST change.
- Prices are Yahoo's auto-adjusted values (split and dividend adjusted).

## Tests

```bash
python -m unittest discover -s tests
```

The suite is offline. It covers one execution per order, P&L accounting,
stop-loss liquidation, no same-bar fills, seeded determinism, and the cache
round-trip with the offline error.

## Redis (optional)

`--bus redis --redis-url redis://localhost:6379` uses `redis.asyncio` pub/sub
and needs a running Redis server. The bus waits until every published message
has been delivered to the local listener. Delivery order across channels can
differ from the in-memory bus, so runs over Redis are not guaranteed to be
identical. This path is not covered by the offline tests.

## Layout

```
agents/       analyst, trader, risk manager, reporter, base class
core/         message bus, market replay, portfolio ledger, logging, schemas
data/         data loader and sample data
src/main.py   deterministic runner and CLI
scripts/      fetch_sample_data.py, generate_narrative_report.py
tests/        unittest suite (offline)
```

## Fixed bugs

See `SWARM_TRADING_CHANGES.md` for each bug, its fix, and the file changed.

- Every approved order executed twice. Orders are now approved once, forwarded once, filled once, and the market ignores repeated submissions.
- Same-bar fills. Orders fill at the next bar's open. A market-level check blocks a same-bar fill.
- Limit orders always filled. Limits are now checked against the fill price.
- Stop-loss alerts did nothing. Alerts now liquidate the position through an order that skips size limits.
- P&L was hard-coded to zero. Realised and unrealised P&L are now computed from fills and marks.
- Snapshots were stale (every 10th day). A snapshot is now taken every bar.
- The cache could not load (mixed timezones). Timestamps are normalised with `utc=True`.
- Missing data crashed with `IndexError`. It now fails with a clear message.
- Results depended on the clock and unseeded randomness. Timers are removed and all RNGs are seeded.
- `aioredis` does not import on Python 3.12. It is removed. Redis is optional and uses `redis.asyncio`.
- The narrative report failed on Windows (`UnicodeEncodeError`). It now writes UTF-8 and uses only ASCII text.
- Emoji appeared in code and log strings. Removed.

## Known limitations

- The universe is 10 hand-picked mega-caps, which is survivorship-biased. Adjusted prices are used.
- `.env` and `.env.example` are not read by the code.
- `docker-compose.yml` runs the same simulation in every service. This is not fixed.
- `DELIVERABLES_VERIFICATION.md` and `DETAILED_SIMULATION_REPORT.md` describe the original, unreproducible run. They are not regenerated and should not be cited.
- Unused dependencies remain in `requirements.txt`.

This is a simulation, not a trading system. It is not financial advice.
