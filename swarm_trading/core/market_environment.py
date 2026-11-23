"""Historical market replay with next-bar execution.

Timing within one simulated trading day ``t`` (driven by ``src/main.py``):

1. ``execute_open(t)``   fills orders submitted on an earlier bar at bar ``t``'s open.
2. ``publish_bars(t)``   publishes bar ``t`` (open, high, low, close). Close is the
                         last price known for the day.
3. agents react to bar ``t``. Any order they create is queued and can only fill
   on a later bar, so no order fills on the bar that generated it.

Fill price is the open of the fill bar. If the open is missing (NaN), the close
of that bar is used and recorded as ``price_source``. Orders are day orders: an
order that is not filled on the first bar after submission expires.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Optional
import logging

import pandas as pd

from .message_bus import MessageBus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Bar:
    symbol: str
    day: date
    open: float  # may be NaN
    high: float
    low: float
    close: float
    volume: int


@dataclass
class PendingOrder:
    order_id: str
    trader_id: str
    symbol: str
    side: str  # "BUY" or "SELL"
    quantity: int
    limit_price: Optional[float]
    reason: str
    submitted_on: date


class MarketEnvironment:
    def __init__(
        self,
        message_bus: MessageBus,
        historical_data: dict[str, pd.DataFrame],
        commission_rate: float = 0.001,
    ):
        if commission_rate < 0:
            raise ValueError("commission_rate must be non-negative")
        self.message_bus = message_bus
        self.commission_rate = commission_rate

        self.bars: dict[str, dict[date, Bar]] = defaultdict(dict)
        self.timeline: list[date] = []
        self.current_date: Optional[date] = None
        self.current_prices: dict[str, float] = {}

        self.pending_orders: list[PendingOrder] = []
        self._seen_order_ids: set[str] = set()
        self.submitted_count = 0
        self.duplicate_submissions = 0
        self.filled_count = 0
        self.expired_count = 0

        self._prepare_market_data(historical_data)

    def _prepare_market_data(self, historical_data: dict[str, pd.DataFrame]) -> None:
        all_days: set[date] = set()
        for symbol in sorted(historical_data):
            df = historical_data[symbol]
            days = pd.to_datetime(df["timestamp"]).dt.date  # read-only: caller's frame is not mutated
            for day, open_, high, low, close, volume in zip(
                days, df["Open"], df["High"], df["Low"], df["Close"], df["Volume"]
            ):
                if not isfinite(float(close)) or float(close) <= 0:
                    continue
                self.bars[symbol][day] = Bar(
                    symbol=symbol,
                    day=day,
                    open=float(open_) if open_ is not None else float("nan"),
                    high=float(high),
                    low=float(low),
                    close=float(close),
                    volume=int(volume) if isfinite(float(volume)) else 0,
                )
                all_days.add(day)
        self.timeline = sorted(all_days)
        if not self.timeline:
            raise ValueError("no usable bars in historical data (all Close values missing?)")
        logger.info(
            f"Market prepared: {len(self.timeline)} trading days, {len(self.bars)} symbols"
        )

    async def publish_bars(self, day: date) -> None:
        """Set the day's closing prices and publish one market_update per symbol."""
        self.current_date = day
        for symbol in sorted(self.bars):
            bar = self.bars[symbol].get(day)
            if bar is None:
                continue
            self.current_prices[symbol] = bar.close
            open_value = bar.open if isfinite(bar.open) else None
            await self.message_bus.publish(
                "market_data",
                {
                    "type": "market_update",
                    "timestamp": f"{day.isoformat()}T00:00:00",
                    "symbol": symbol,
                    "open": open_value,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                },
            )

    async def submit_order(
        self,
        order_id: str,
        trader_id: str,
        symbol: str,
        side: str,
        quantity: int,
        limit_price: Optional[float] = None,
        reason: str = "signal",
    ) -> bool:
        """Queue an approved order. Returns False for a duplicate order_id (ignored)."""
        if self.current_date is None:
            raise RuntimeError("submit_order called before the first bar was published")
        if side not in ("BUY", "SELL"):
            raise ValueError(f"side must be BUY or SELL, got {side!r}")
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if order_id in self._seen_order_ids:
            self.duplicate_submissions += 1
            logger.error(f"Duplicate submission ignored for order {order_id}")
            return False
        self._seen_order_ids.add(order_id)
        self.submitted_count += 1
        self.pending_orders.append(
            PendingOrder(
                order_id=order_id,
                trader_id=trader_id,
                symbol=symbol,
                side=side,
                quantity=int(quantity),
                limit_price=limit_price,
                reason=reason,
                submitted_on=self.current_date,
            )
        )
        return True

    async def execute_open(self, day: date) -> None:
        """Fill or expire pending orders against bar ``day``. Runs before bars are published."""
        still_pending: list[PendingOrder] = []
        for order in self.pending_orders:
            if day <= order.submitted_on:
                # Same bar (or earlier) as the signal: not eligible yet.
                still_pending.append(order)
                continue
            bar = self.bars[order.symbol].get(day)
            if bar is None:
                # No bar for this symbol today; keep it until the symbol trades again.
                still_pending.append(order)
                continue

            if isfinite(bar.open):
                fill_price, price_source = bar.open, "open"
            else:
                fill_price, price_source = bar.close, "close_fallback"

            limit_ok = True
            if order.limit_price is not None:
                if order.side == "BUY":
                    limit_ok = fill_price <= order.limit_price
                else:
                    limit_ok = fill_price >= order.limit_price

            if not limit_ok:
                self.expired_count += 1
                await self.message_bus.publish(
                    "order_expired",
                    {
                        "type": "order_expired",
                        "order_id": order.order_id,
                        "trader_id": order.trader_id,
                        "symbol": order.symbol,
                        "side": order.side,
                        "quantity": order.quantity,
                        "reason": "limit_not_reached",
                        "timestamp": f"{day.isoformat()}T00:00:00",
                    },
                )
                continue

            commission = self.commission_rate * order.quantity * fill_price
            self.filled_count += 1
            await self.message_bus.publish(
                "trades",
                {
                    "type": "trade_executed",
                    "trade_id": f"trade_{order.order_id}",
                    "order_id": order.order_id,
                    "trader_id": order.trader_id,
                    "symbol": order.symbol,
                    "side": order.side,
                    "quantity": order.quantity,
                    "execution_price": fill_price,
                    "price_source": price_source,
                    "commission": commission,
                    "submitted_on": order.submitted_on.isoformat(),
                    "timestamp": f"{day.isoformat()}T00:00:00",
                    "reason": order.reason,
                },
            )

        self.pending_orders = still_pending
