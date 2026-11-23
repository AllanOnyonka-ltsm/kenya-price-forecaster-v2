"""Cash and position ledger with realised and unrealised P&L.

Accounting rules (average-cost method):

* A buy adds its commission to the cost basis, so avg_cost = (old_cost + qty*price + commission) / new_qty.
* A sell realises (fill_price - avg_cost) * qty - commission.
* Unrealised P&L for an open lot is (mark_price - avg_cost) * qty.
* Identity: total_value - initial_cash == realised_pnl + unrealised_pnl, for any marks.
"""

from dataclasses import dataclass
from typing import Mapping, Optional


class PortfolioError(Exception):
    """Raised when a fill would make cash or shares negative."""


@dataclass
class Holding:
    quantity: int = 0
    avg_cost: float = 0.0


class Portfolio:
    def __init__(self, initial_cash: float):
        if initial_cash < 0:
            raise ValueError("initial_cash must be non-negative")
        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.holdings: dict[str, Holding] = {}
        self.realized_pnl = 0.0
        self.commissions_paid = 0.0

    def quantity(self, symbol: str) -> int:
        holding = self.holdings.get(symbol)
        return holding.quantity if holding else 0

    def average_cost(self, symbol: str) -> float:
        holding = self.holdings.get(symbol)
        return holding.avg_cost if holding and holding.quantity > 0 else 0.0

    def apply_fill(
        self,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        commission: float = 0.0,
    ) -> float:
        """Apply one fill. Returns the realised P&L from this fill (0 for buys)."""
        if quantity <= 0:
            raise PortfolioError(f"fill quantity must be positive, got {quantity}")
        if price <= 0:
            raise PortfolioError(f"fill price must be positive, got {price}")
        if commission < 0:
            raise PortfolioError("commission must be non-negative")

        self.commissions_paid += commission

        if side == "BUY":
            cost = quantity * price + commission
            if cost > self.cash + 1e-9:
                raise PortfolioError(
                    f"insufficient cash for {symbol}: need {cost:.2f}, have {self.cash:.2f}"
                )
            holding = self.holdings.setdefault(symbol, Holding())
            new_quantity = holding.quantity + quantity
            holding.avg_cost = (holding.avg_cost * holding.quantity + cost) / new_quantity
            holding.quantity = new_quantity
            self.cash -= cost
            return 0.0

        if side == "SELL":
            holding = self.holdings.get(symbol)
            held = holding.quantity if holding else 0
            if quantity > held:
                raise PortfolioError(
                    f"insufficient shares of {symbol}: selling {quantity}, hold {held}"
                )
            realised = (price - holding.avg_cost) * quantity - commission
            self.realized_pnl += realised
            self.cash += quantity * price - commission
            holding.quantity -= quantity
            if holding.quantity == 0:
                holding.avg_cost = 0.0
            return realised

        raise PortfolioError(f"unknown side {side!r}")

    def market_value(self, prices: Mapping[str, float]) -> float:
        """Value of open positions. Falls back to avg cost if a symbol has no price."""
        total = 0.0
        for symbol, holding in self.holdings.items():
            if holding.quantity <= 0:
                continue
            mark = prices.get(symbol, holding.avg_cost)
            total += holding.quantity * mark
        return total

    def unrealized_pnl(self, prices: Mapping[str, float]) -> float:
        total = 0.0
        for symbol, holding in self.holdings.items():
            if holding.quantity <= 0:
                continue
            mark = prices.get(symbol, holding.avg_cost)
            total += (mark - holding.avg_cost) * holding.quantity
        return total

    def total_value(self, prices: Mapping[str, float]) -> float:
        return self.cash + self.market_value(prices)

    def total_pnl(self, prices: Mapping[str, float]) -> float:
        return self.realized_pnl + self.unrealized_pnl(prices)

    def snapshot(self, prices: Mapping[str, float]) -> dict:
        return {
            "cash": self.cash,
            "market_value": self.market_value(prices),
            "total_value": self.total_value(prices),
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self.unrealized_pnl(prices),
            "total_pnl": self.total_pnl(prices),
            "commissions_paid": self.commissions_paid,
        }
