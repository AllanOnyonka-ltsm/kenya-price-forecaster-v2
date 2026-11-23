"""Deterministic swarm simulation runner.

Run from anywhere, for example:

    python src/main.py --offline --data-dir data/sample --symbols AAPL,MSFT,JNJ \
        --start 2023-01-01 --end 2023-07-01 --seed 42

Each simulated trading day runs five steps in a fixed order, with bus.drain()
after each one. No timers or sleeps are involved:

    1. market.execute_open(t)   fill or expire orders queued on earlier bars
    2. market.publish_bars(t)   publish bar t; agents react; orders are queued
    3. bus.publish clock bar_close, so risk managers check stop-losses
    4. record a portfolio snapshot at the close of bar t
"""

import argparse
import asyncio
import logging
import random
import sys
from datetime import date
from pathlib import Path
from typing import Optional

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agents import AnalystAgent, ReporterAgent, RiskManagerAgent, TraderAgent  # noqa: E402
from core.logger import setup_logging  # noqa: E402
from core.market_environment import MarketEnvironment  # noqa: E402
from core.message_bus import LocalMessageBus, MessageBus, RedisMessageBus  # noqa: E402
from data.data_loader import (  # noqa: E402
    DEFAULT_HISTORICAL_DIR,
    SAMPLE_DATA_DIR,
    DataLoader,
    DataUnavailableError,
)

logger = logging.getLogger(__name__)

DEFAULT_SYMBOLS = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "META", "NVDA", "JPM", "V", "JNJ"]
DEFAULT_START = "2023-01-01"
DEFAULT_END = "2024-01-01"


def seed_everything(seed: int) -> None:
    """Seed every RNG the process uses. The analysts also get their own seeded Random instances."""
    random.seed(seed)
    np.random.seed(seed)


def load_market_data(
    symbols: list[str],
    start: Optional[str],
    end: Optional[str],
    data_dir: Path,
    offline: bool,
) -> dict:
    """Load from cache, download only the missing symbols, and fail clearly if nothing is usable."""
    loader = DataLoader(historical_dir=data_dir)
    data = loader.load_all(symbols, start, end)
    missing = [s for s in symbols if s not in data]

    if missing and offline:
        logger.warning(f"Offline mode: no cached data for {missing} in {data_dir}")
    elif missing:
        fetched = loader.download_historical_data(missing, start or DEFAULT_START, end or DEFAULT_END)
        for symbol, reason in loader.failed_downloads.items():
            logger.warning(f"Download failed for {symbol}: {reason}")
        for symbol, df in fetched.items():
            data[symbol] = DataLoader._filter_dates(df, start, end)

    if not data:
        hint = (
            "Offline mode: no cached data in "
            f"{data_dir}. Use --data-dir {SAMPLE_DATA_DIR} for the bundled sample, or run once "
            "without --offline when the network is available."
            if offline
            else
            f"No cached data in {data_dir} and the download returned nothing (is the network "
            f"available?). Use --data-dir {SAMPLE_DATA_DIR} for the bundled sample."
        )
        raise DataUnavailableError(f"No market data for {symbols}. {hint}")
    return data


class TradingSimulation:
    def __init__(
        self,
        historical_data: dict,
        symbols: list[str],
        initial_cash: float = 1_000_000.0,
        seed: int = 0,
        commission_rate: float = 0.001,
        reports_dir: Optional[Path] = None,
        bus: Optional[MessageBus] = None,
        num_analysts: int = 3,
        num_traders: int = 4,
        num_risk_managers: int = 2,
    ):
        self.historical_data = historical_data
        self.symbols = list(symbols)
        self.initial_cash = initial_cash
        self.seed = seed
        self.commission_rate = commission_rate
        self.reports_dir = reports_dir
        self.num_risk_managers = num_risk_managers
        self._bus = bus
        self._counts = (num_analysts, num_traders, num_risk_managers)

        self.bus: Optional[MessageBus] = None
        self.market: Optional[MarketEnvironment] = None
        self.analysts: list[AnalystAgent] = []
        self.traders: list[TraderAgent] = []
        self.risk_managers: list[RiskManagerAgent] = []
        self.reporter: Optional[ReporterAgent] = None

    @property
    def agents(self) -> list:
        return [*self.analysts, *self.traders, *self.risk_managers, self.reporter]

    async def initialize(self) -> None:
        seed_everything(self.seed)
        self.bus = self._bus if self._bus is not None else LocalMessageBus()
        if isinstance(self.bus, RedisMessageBus):
            await self.bus.connect()
        self.market = MarketEnvironment(self.bus, self.historical_data, self.commission_rate)
        await self.bus.subscribe("approved_orders", self._on_approved_order)

        num_analysts, num_traders, num_rms = self._counts
        # Round-robin split: every symbol is covered by exactly one analyst and one trader.
        for i in range(num_analysts):
            self.analysts.append(AnalystAgent(
                agent_id=f"analyst_{i + 1}",
                message_bus=self.bus,
                symbols=self.symbols[i::num_analysts],
                rng=random.Random(self.seed * 1000 + i),
            ))
        trader_cash = self.initial_cash / num_traders
        for i in range(num_traders):
            self.traders.append(TraderAgent(
                agent_id=f"trader_{i + 1}",
                message_bus=self.bus,
                symbols=self.symbols[i::num_traders],
                initial_cash=trader_cash,
                required_approvals=num_rms,
                commission_rate=self.commission_rate,
            ))
        for i in range(num_rms):
            self.risk_managers.append(RiskManagerAgent(
                agent_id=f"risk_manager_{i + 1}",
                message_bus=self.bus,
                initial_portfolio_value=self.initial_cash,
                initial_cash_per_trader=trader_cash,
                commission_rate=self.commission_rate,
            ))
        self.reporter = ReporterAgent(
            agent_id="reporter_1", message_bus=self.bus, reports_dir=self.reports_dir
        )
        for agent in self.agents:
            await agent.start()
        logger.info(
            f"Created {len(self.agents)} agents; seed={self.seed}; "
            f"symbols={self.symbols}; trading days={len(self.market.timeline)}"
        )

    async def _on_approved_order(self, message: dict) -> None:
        if message.get("type") != "order_approved":
            return
        await self.market.submit_order(
            order_id=message["order_id"],
            trader_id=message["trader_id"],
            symbol=message["symbol"],
            side=message["side"],
            quantity=message["quantity"],
            limit_price=message.get("price"),
            reason=message.get("reason", "signal"),
        )

    def portfolio_snapshot(self, day: date) -> dict:
        total = realized = unrealized = cash = 0.0
        for trader in self.traders:
            prices = trader.current_prices
            snap = trader.portfolio.snapshot(prices)
            total += snap["total_value"]
            realized += snap["realized_pnl"]
            unrealized += snap["unrealized_pnl"]
            cash += snap["cash"]
        return {
            "date": day.isoformat(),
            "total_value": total,
            "cash": cash,
            "realized_pnl": realized,
            "unrealized_pnl": unrealized,
        }

    async def step(self, day: date) -> None:
        await self.market.execute_open(day)
        await self.bus.drain()
        await self.market.publish_bars(day)
        await self.bus.drain()
        await self.bus.publish("clock", {"type": "bar_close", "date": day.isoformat()})
        await self.bus.drain()
        await self.bus.publish(
            "portfolio_update",
            {"type": "portfolio_snapshot", "data": self.portfolio_snapshot(day)},
        )
        await self.bus.drain()

    async def run(self) -> dict:
        timeline = self.market.timeline
        logger.info(f"Running {len(timeline)} trading days from {timeline[0]} to {timeline[-1]}")
        for index, day in enumerate(timeline):
            await self.step(day)
            if index % 50 == 0 or index == len(timeline) - 1:
                logger.info(f"Progress: day {index + 1}/{len(timeline)} ({day})")

        report = await self.reporter.generate_daily_report(timeline[-1].isoformat())
        summary = report["summary"]
        risk = report["risk_metrics"]
        logger.info("=" * 60)
        logger.info("FINAL REPORT SUMMARY")
        logger.info(f"Total portfolio value: ${summary['total_portfolio_value']:,.2f}")
        logger.info(f"Total P&L:             ${summary['total_pnl']:,.2f}")
        logger.info(f"  Realised P&L:        ${summary['realized_pnl']:,.2f}")
        logger.info(f"  Unrealised P&L:      ${summary['unrealized_pnl']:,.2f}")
        logger.info(f"Trades:                {summary['total_trades']} "
                    f"(buys {summary['buy_orders']}, sells {summary['sell_orders']})")
        logger.info(f"Approved orders:       {risk['approved_orders']}")
        logger.info(f"Rejected orders:       {risk['rejected_orders']} ({risk['rejected_orders_pct']:.1f}%)")
        logger.info(f"Expired (unfilled):    {risk['expired_orders']}")
        logger.info(f"Stop-loss liquidations:{risk['stop_losses_triggered']:>3}")
        logger.info(f"Max drawdown:          {risk['max_drawdown'] * 100:.2f}%")
        logger.info(f"Duplicate trades:      {risk['duplicate_trades_detected']}")
        logger.info("=" * 60)
        return report

    async def shutdown(self) -> None:
        for agent in self.agents:
            await agent.stop()
        if self.bus is not None:
            await self.bus.close()


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the stock trading agent swarm.")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS),
                        help="comma-separated tickers")
    parser.add_argument("--start", default=DEFAULT_START, help="first date, inclusive (YYYY-MM-DD)")
    parser.add_argument("--end", default=DEFAULT_END, help="end date, exclusive (YYYY-MM-DD)")
    parser.add_argument("--seed", type=int, default=42, help="seed for every RNG (default 42)")
    parser.add_argument("--initial-cash", type=float, default=1_000_000.0)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_HISTORICAL_DIR,
                        help="directory of <SYMBOL>.csv files (default: data/historical)")
    parser.add_argument("--offline", action="store_true",
                        help="never touch the network; use cached data only")
    parser.add_argument("--reports-dir", type=Path, default=PROJECT_ROOT / "reports")
    parser.add_argument("--no-reports", action="store_true", help="do not write report files")
    parser.add_argument("--bus", choices=["memory", "redis"], default="memory",
                        help="message bus (default: memory; redis is optional)")
    parser.add_argument("--redis-url", default="redis://localhost:6379")
    return parser.parse_args(argv)


async def amain(args: argparse.Namespace) -> int:
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    data = load_market_data(symbols, args.start, args.end, args.data_dir, args.offline)

    bus: MessageBus = RedisMessageBus(args.redis_url) if args.bus == "redis" else LocalMessageBus()
    sim = TradingSimulation(
        historical_data=data,
        symbols=[s for s in symbols if s in data],
        initial_cash=args.initial_cash,
        seed=args.seed,
        reports_dir=None if args.no_reports else args.reports_dir,
        bus=bus,
    )
    try:
        await sim.initialize()
        await sim.run()
    finally:
        await sim.shutdown()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    setup_logging(log_level="INFO")
    args = parse_args(argv)
    try:
        return asyncio.run(amain(args))
    except DataUnavailableError as exc:
        logging.getLogger(__name__).error(str(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
