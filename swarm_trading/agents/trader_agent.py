from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from math import isfinite
from typing import Optional
import logging

from .base_agent import BaseAgent
from core.message_bus import MessageBus
from core.portfolio import Portfolio

logger = logging.getLogger(__name__)


class OrderState(str, Enum):
    PENDING = "PENDING"      # sent to risk managers, decisions outstanding
    APPROVED = "APPROVED"    # all required approvals received; awaiting fill
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@dataclass
class TrackedOrder:
    order_id: str
    symbol: str
    side: str
    quantity: int
    limit_price: Optional[float]
    reason: str
    reserved_cash: float
    state: OrderState = OrderState.PENDING
    approvals: set = field(default_factory=set)
    rejection_reason: Optional[str] = None


class TraderAgent(BaseAgent):
    """Owns a portfolio for its symbols and turns signals into orders.

    Exactly-once rules:
    * An order is approved and forwarded once, when the required number of
      risk managers have approved it. Later decisions for that order are
      ignored because the state is no longer PENDING.
    * A fill is applied once. A fill for an order that is not APPROVED raises,
      so a double fill cannot be booked silently.

    Cash and shares are reserved while an order is open, so two orders cannot
    spend the same cash or sell the same shares.
    """

    def __init__(
        self,
        agent_id: str,
        message_bus: MessageBus,
        symbols: list[str],
        initial_cash: float = 100000.0,
        max_position_value: float = 20000.0,
        required_approvals: int = 2,
        commission_rate: float = 0.001,
        log_level: str = "INFO",
    ):
        super().__init__(agent_id, message_bus, log_level)
        if required_approvals < 1:
            raise ValueError("required_approvals must be at least 1")
        self.symbols = list(symbols)
        self.portfolio = Portfolio(initial_cash)
        self.max_position_value = max_position_value
        self.required_approvals = required_approvals
        self.commission_rate = commission_rate

        self.current_prices: dict[str, float] = {}
        self.orders: dict[str, TrackedOrder] = {}
        self.reserved_cash = 0.0
        self.reserved_shares: dict[str, int] = defaultdict(int)
        self.liquidating: set[str] = set()
        self.order_count = 0

    async def _setup_subscriptions(self) -> None:
        await self.subscribe("market_data", self._on_market_update)
        await self.subscribe("signals", self._on_signal)
        await self.subscribe("risk_decisions", self._on_risk_decision)
        await self.subscribe("trades", self._on_trade_executed)
        await self.subscribe("order_expired", self._on_order_expired)
        await self.subscribe("stop_loss_alerts", self._on_stop_loss)

    def get_portfolio_value(self) -> float:
        return self.portfolio.total_value(self.current_prices)

    def _available_cash(self) -> float:
        return self.portfolio.cash - self.reserved_cash

    def _available_shares(self, symbol: str) -> int:
        return self.portfolio.quantity(symbol) - self.reserved_shares[symbol]

    async def _on_market_update(self, message: dict) -> None:
        if message.get("type") != "market_update":
            return
        symbol = message.get("symbol")
        close_price = message.get("close")
        if symbol and close_price is not None and isfinite(close_price) and close_price > 0:
            self.current_prices[symbol] = close_price

    async def _on_signal(self, message: dict) -> None:
        if message.get("type") != "signal":
            return
        symbol = message.get("symbol")
        if symbol not in self.symbols:
            return
        confidence = message.get("confidence", 0.0)
        if confidence < 0.6:
            return
        if symbol not in self.current_prices:
            self.logger.warning(f"No price data for {symbol}, cannot trade")
            return

        price = self.current_prices[symbol]
        signal_type = message.get("signal_type")
        if signal_type == "BULLISH" and confidence > 0.65:
            await self._place_buy(symbol, price)
        elif signal_type == "BEARISH" and confidence > 0.65:
            await self._place_sell(symbol, price)

    async def _place_buy(self, symbol: str, reference_price: float) -> None:
        if symbol in self.liquidating:
            return
        if self.portfolio.quantity(symbol) * reference_price >= self.max_position_value:
            return

        budget = min(max(self._available_cash(), 0.0) * 0.1, self.max_position_value * 0.3)
        if budget < reference_price:
            return
        quantity = int(budget / reference_price)
        if quantity < 1:
            return
        limit = reference_price * 1.01
        await self._submit(symbol, "BUY", quantity, limit, "signal")

    async def _place_sell(self, symbol: str, reference_price: float) -> None:
        if symbol in self.liquidating:
            return
        available = self._available_shares(symbol)
        if available <= 0:
            return
        quantity = max(1, int(available * 0.5))
        limit = reference_price * 0.99
        await self._submit(symbol, "SELL", quantity, limit, "signal")

    async def _submit(
        self,
        symbol: str,
        side: str,
        quantity: int,
        limit_price: Optional[float],
        reason: str,
    ) -> str:
        self.order_count += 1
        order_id = f"{self.agent_id}_order_{self.order_count}"

        if side == "BUY":
            reserved = quantity * limit_price * (1.0 + self.commission_rate)
            self.reserved_cash += reserved
        else:
            reserved = 0.0
            self.reserved_shares[symbol] += quantity
        if reason == "stop_loss":
            self.liquidating.add(symbol)

        self.orders[order_id] = TrackedOrder(
            order_id=order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            limit_price=limit_price,
            reason=reason,
            reserved_cash=reserved,
        )

        await self.publish(
            "orders",
            {
                "type": "order_request",
                "order_id": order_id,
                "trader_id": self.agent_id,
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "price": limit_price,
                "reason": reason,
            },
        )
        self.logger.info(
            f"Placed {side} order",
            order_id=order_id,
            symbol=symbol,
            quantity=quantity,
            limit=f"{limit_price:.4f}" if limit_price is not None else "market",
            reason=reason,
        )
        return order_id

    def _release(self, tracked: TrackedOrder) -> None:
        if tracked.side == "BUY":
            self.reserved_cash -= tracked.reserved_cash
            if abs(self.reserved_cash) < 1e-9:
                self.reserved_cash = 0.0
        else:
            self.reserved_shares[tracked.symbol] -= tracked.quantity
        if tracked.reason == "stop_loss":
            self.liquidating.discard(tracked.symbol)

    async def _on_risk_decision(self, message: dict) -> None:
        if message.get("type") != "risk_decision":
            return
        tracked = self.orders.get(message.get("order_id"))
        if tracked is None or tracked.state != OrderState.PENDING:
            return  # already resolved: a late decision must not re-approve or re-reject

        if message.get("approved"):
            tracked.approvals.add(message.get("risk_manager_id"))
            if len(tracked.approvals) < self.required_approvals:
                return
            tracked.state = OrderState.APPROVED
            await self.publish(
                "approved_orders",
                {
                    "type": "order_approved",
                    "order_id": tracked.order_id,
                    "trader_id": self.agent_id,
                    "symbol": tracked.symbol,
                    "side": tracked.side,
                    "quantity": tracked.quantity,
                    "price": tracked.limit_price,
                    "reason": tracked.reason,
                },
            )
            await self.publish(
                "order_decisions",
                {"type": "order_decision", "order_id": tracked.order_id,
                 "trader_id": self.agent_id, "decision": "APPROVED", "reason": ""},
            )
            self.logger.info("Order approved", order_id=tracked.order_id)
        else:
            tracked.state = OrderState.REJECTED
            tracked.rejection_reason = message.get("reason", "Unknown")
            self._release(tracked)
            await self.publish(
                "order_decisions",
                {"type": "order_decision", "order_id": tracked.order_id,
                 "trader_id": self.agent_id, "decision": "REJECTED",
                 "reason": tracked.rejection_reason},
            )
            self.logger.warning(
                "Order rejected", order_id=tracked.order_id, reason=tracked.rejection_reason
            )

    async def _on_trade_executed(self, message: dict) -> None:
        if message.get("type") != "trade_executed" or message.get("trader_id") != self.agent_id:
            return
        order_id = message.get("order_id")
        tracked = self.orders.get(order_id)
        if tracked is None:
            raise RuntimeError(f"{self.agent_id}: fill for unknown order {order_id}")
        if tracked.state != OrderState.APPROVED:
            raise RuntimeError(
                f"{self.agent_id}: fill for order {order_id} in state {tracked.state.value} "
                "(double fill or fill without approval)"
            )

        self.portfolio.apply_fill(
            symbol=tracked.symbol,
            side=tracked.side,
            quantity=message["quantity"],
            price=message["execution_price"],
            commission=message.get("commission", 0.0),
        )
        tracked.state = OrderState.FILLED
        self._release(tracked)
        self.logger.info(
            "Trade executed",
            order_id=order_id,
            symbol=tracked.symbol,
            side=tracked.side,
            quantity=tracked.quantity,
            price=f"{message['execution_price']:.4f}",
            cash=f"{self.portfolio.cash:.2f}",
            position=self.portfolio.quantity(tracked.symbol),
        )

    async def _on_order_expired(self, message: dict) -> None:
        if message.get("type") != "order_expired" or message.get("trader_id") != self.agent_id:
            return
        tracked = self.orders.get(message.get("order_id"))
        if tracked is None or tracked.state != OrderState.APPROVED:
            raise RuntimeError(f"{self.agent_id}: expiry for order not awaiting fill")
        tracked.state = OrderState.EXPIRED
        self._release(tracked)
        self.logger.info("Order expired unfilled", order_id=tracked.order_id)

    async def _on_stop_loss(self, message: dict) -> None:
        """Liquidate the full available position when a risk manager raises a stop-loss."""
        if message.get("type") != "stop_loss" or message.get("trader_id") != self.agent_id:
            return
        symbol = message.get("symbol")
        if symbol not in self.symbols or symbol in self.liquidating:
            return
        available = self._available_shares(symbol)
        if available <= 0:
            return

        await self.publish(
            "stop_loss_liquidations",
            {
                "type": "stop_loss_liquidation",
                "trader_id": self.agent_id,
                "symbol": symbol,
                "quantity": available,
                "avg_cost": message.get("avg_cost"),
                "current_price": message.get("current_price"),
                "loss_percent": message.get("loss_percent"),
                "timestamp": message.get("timestamp"),
            },
        )
        await self._submit(symbol, "SELL", available, None, "stop_loss")
