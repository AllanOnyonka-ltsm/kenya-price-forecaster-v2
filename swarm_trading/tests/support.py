"""Shared helpers for the offline test suite. The tests never use the network."""

import asyncio
import logging
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from core.message_bus import LocalMessageBus  # noqa: E402

logging.getLogger().setLevel(logging.ERROR)  # keep test output readable; errors still show
from src.main import TradingSimulation  # noqa: E402


def make_frame(symbol, rows):
    """rows: list of (YYYY-MM-DD, open, high, low, close, volume). Open may be None (NaN)."""
    df = pd.DataFrame({
        "timestamp": pd.to_datetime([r[0] for r in rows]),
        "Open": [float("nan") if r[1] is None else r[1] for r in rows],
        "High": [r[2] for r in rows],
        "Low": [r[3] for r in rows],
        "Close": [r[4] for r in rows],
        "Volume": [r[5] for r in rows],
    })
    df["symbol"] = symbol
    df["date"] = df["timestamp"].dt.date
    return df


def random_walk_data(symbols, days=60, seed=0, start="2023-01-02"):
    rng = random.Random(seed)
    dates = pd.bdate_range(start, periods=days).strftime("%Y-%m-%d").tolist()
    data = {}
    for symbol in symbols:
        price = 100.0 + rng.uniform(-20, 20)
        rows = []
        for day in dates:
            open_ = price * (1 + rng.gauss(0, 0.008))
            close = open_ * (1 + rng.gauss(0.001, 0.025))
            high = max(open_, close) * (1 + abs(rng.gauss(0, 0.005)))
            low = min(open_, close) * (1 - abs(rng.gauss(0, 0.005)))
            rows.append((day, round(open_, 4), round(high, 4), round(low, 4), round(close, 4),
                         int(rng.uniform(1e5, 1e6))))
            price = close
        data[symbol] = make_frame(symbol, rows)
    return data


def run_async(coro):
    return asyncio.run(coro)


async def build_simulation(data, symbols, seed=1, reports_dir=None):
    sim = TradingSimulation(
        historical_data=data,
        symbols=symbols,
        initial_cash=1_000_000.0,
        seed=seed,
        reports_dir=reports_dir,
        bus=LocalMessageBus(),
    )
    await sim.initialize()
    return sim
