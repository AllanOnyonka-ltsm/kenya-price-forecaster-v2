from math import isfinite
from typing import Optional
import logging

from .base_agent import BaseAgent
from core.message_bus import MessageBus
from core.portfolio import Portfolio

logger = logging.getLogger(__name__)


class RiskManagerAgent(BaseAgent):
    """Pre-trade checks on order requests and stop-loss checks on each bar close.

    Keeps its own ledger per trader, built from the same fills the traders
    apply. Stop-loss liquidation orders (reason "stop_loss") skip the size,
    cash and exposure limits, because they reduce risk. They still need the
    shares to exist.
    """

    def __init__(
        self,
        agent_id: str,
        message_bus: MessageBus,
        initial_portfolio_value: float = 1000000.0,
        initial_cash_per_trader: Optional[float] = None,
        max_position_size: float = 50000.0,
        stop_loss_percent: float = 0.05,
        max_portfolio_risk: float = 0.20,
        commission_rate: float = 0.001,
        log_level: str = "INFO",
    ):
        super().__init__(agent_id, message_bus, log_level)
        self.initial_portfolio_value = initial_portfolio_value
        self.initial_cash_per_trader = (
            initial_cash_per_trader
            if initial_cash_per_trader is not None
            else initial_portfolio_value / 4
        )
        self.max_position_size = max_position_size
        self.stop_loss_percent = stop_loss_percent
        self.max_portfolio_risk = max_portfolio_risk
        self.commission_rate = commission_rate

        self.books: dict[str, Portfolio] = {}
        self.current_prices: dict[str, float] = {}
        self.approved_count = 0
        self.rejected_count = 0

    async def _setup_subscriptions(self) -> None:
        await self.subscribe("orders", self._on_order_request)
        await self.subscribe("market_data", self._on_market_update)
        await self.subscribe("trades", self._on_trade_executed)
        await self.subscribe("clock", self._on_clock)

    def _book(self, trader_id: str) -> Portfolio:
        if trader_id not in self.books:
            self.books[trader_id] = Portfolio(self.initial_cash_per_trader)
        return self.books[trader_id]

    async def _on_market_update(self, message: dict) -> None:
        if message.get("type") != "market_update":
            return
        symbol = message.get("symbol")
        close_price = message.get("close")
        if symbol and close_price is not None and isfinite(close_price) and close_price > 0:
            self.current_prices[symbol] = close_price

    async def _on_order_request(self, message: dict) -> None:
        if message.get("type") != "order_request":
            return
        order_id = message.get("order_id")
        trader_id = message.get("trader_id")
        symbol = message.get("symbol")
        side = message.get("side")
        quantity = message.get("quantity")
        reason = message.get("reason", "signal")

        approved, why = self._validate_order(trader_id, symbol, side, quantity, reason)
        if approved:
            self.approved_count += 1
        else:
            self.rejected_count += 1
            self.logger.warning("Order REJECTED", order_id=order_id, symbol=symbol, reason=why)

        await self.publish(
            "risk_decisions",
            {
                "type": "risk_decision",
                "order_id": order_id,
                "risk_manager_id": self.agent_id,
                "approved": approved,
                "reason": why,
            },
        )

    def _validate_order(
        self, trader_id: str, symbol: str, side: str, quantity: int, reason: str
    ) -> tuple[bool, str]:
        if symbol not in self.current_prices:
            return False, "No market data available for symbol"
        book = self._book(trader_id)

        if side == "SELL":
            held = book.quantity(symbol)
            if quantity > held:
                return False, f"Insufficient shares: trying to sell {quantity}, have {held}"
            return True, "Order approved"

        if reason == "stop_loss":
            return True, "Order approved"

        reference = self.current_prices[symbol]
        order_value = quantity * reference
        if order_value > self.max_position_size:
            return False, (
                f"Order value ${order_value:.2f} exceeds max position size "
                f"${self.max_position_size:.2f}"
            )
        cash_needed = order_value * (1.0 + self.commission_rate)
        if cash_needed > book.cash:
            return False, f"Insufficient cash: need ${cash_needed:.2f}, have ${book.cash:.2f}"

        total_exposure = sum(
            b.market_value(self.current_prices) for b in self.books.values()
        )
        max_allowed = self.initial_portfolio_value * self.max_portfolio_risk
        if total_exposure + order_value > max_allowed:
            return False, (
                f"Portfolio risk limit exceeded: ${total_exposure + order_value:.2f} "
                f"> ${max_allowed:.2f}"
            )
        return True, "Order approved"

    async def _on_trade_executed(self, message: dict) -> None:
        if message.get("type") != "trade_executed":
            return
        self._book(message.get("trader_id")).apply_fill(
            symbol=message["symbol"],
            side=message["side"],
            quantity=message["quantity"],
            price=message["execution_price"],
            commission=message.get("commission", 0.0),
        )

    async def _on_clock(self, message: dict) -> None:
        if message.get("type") != "bar_close":
            return
        await self._check_stop_losses(message.get("date"))

    async def _check_stop_losses(self, bar_date: Optional[str]) -> None:
        for trader_id in sorted(self.books):
            book = self.books[trader_id]
            for symbol in sorted(book.holdings):
                quantity = book.quantity(symbol)
                if quantity <= 0 or symbol not in self.current_prices:
                    continue
                avg_cost = book.average_cost(symbol)
                if avg_cost <= 0:
                    continue
                current_price = self.current_prices[symbol]
                loss_percent = (avg_cost - current_price) / avg_cost
                if loss_percent > self.stop_loss_percent:
                    self.logger.warning(
                        "STOP LOSS triggered",
                        symbol=symbol,
                        trader_id=trader_id,
                        loss_percent=f"{loss_percent * 100:.2f}%",
                        avg_cost=f"{avg_cost:.4f}",
                        current_price=f"{current_price:.4f}",
                    )
                    await self.publish(
                        "stop_loss_alerts",
                        {
                            "type": "stop_loss",
                            "trader_id": trader_id,
                            "symbol": symbol,
                            "quantity": quantity,
                            "avg_cost": avg_cost,
                            "current_price": current_price,
                            "loss_percent": loss_percent,
                            "timestamp": bar_date,
                            "risk_manager_id": self.agent_id,
                        },
                    )
