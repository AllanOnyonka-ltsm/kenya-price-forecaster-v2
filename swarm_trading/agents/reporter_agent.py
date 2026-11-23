import csv
import json
from pathlib import Path
from typing import Optional
import logging

from .base_agent import BaseAgent
from core.message_bus import MessageBus

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORTS_DIR = PROJECT_ROOT / "reports"


class ReporterAgent(BaseAgent):
    """Records events and writes the report. Counts are per order, not per message."""

    def __init__(
        self,
        agent_id: str,
        message_bus: MessageBus,
        reports_dir: Optional[Path] = DEFAULT_REPORTS_DIR,
        log_level: str = "INFO",
    ):
        super().__init__(agent_id, message_bus, log_level)
        self.reports_dir = Path(reports_dir) if reports_dir is not None else None

        self.trades: list[dict] = []
        self.trade_ids: set[str] = set()
        self.duplicate_trades = 0
        self.stop_losses: list[dict] = []
        self.rejected_orders: list[dict] = []
        self.approved_orders = 0
        self.rejected_orders_count = 0
        self.expired_orders = 0
        self.portfolio_snapshots: list[dict] = []

    async def _setup_subscriptions(self) -> None:
        await self.subscribe("trades", self._on_trade)
        await self.subscribe("order_decisions", self._on_order_decision)
        await self.subscribe("order_expired", self._on_order_expired)
        await self.subscribe("stop_loss_liquidations", self._on_stop_loss)
        await self.subscribe("portfolio_update", self._on_portfolio_update)

    async def _on_trade(self, message: dict) -> None:
        if message.get("type") != "trade_executed":
            return
        trade_id = message.get("trade_id")
        if trade_id in self.trade_ids:
            self.duplicate_trades += 1
            self.logger.error(f"Duplicate trade detected: {trade_id}")
            return
        self.trade_ids.add(trade_id)
        self.trades.append({
            "trade_id": trade_id,
            "order_id": message.get("order_id"),
            "trader_id": message.get("trader_id"),
            "symbol": message.get("symbol"),
            "side": message.get("side"),
            "quantity": message.get("quantity"),
            "execution_price": message.get("execution_price"),
            "price_source": message.get("price_source"),
            "commission": message.get("commission"),
            "submitted_on": message.get("submitted_on"),
            "timestamp": message.get("timestamp"),
            "reason": message.get("reason"),
        })

    async def _on_order_decision(self, message: dict) -> None:
        if message.get("type") != "order_decision":
            return
        if message.get("decision") == "APPROVED":
            self.approved_orders += 1
        else:
            self.rejected_orders_count += 1
            self.rejected_orders.append({
                "order_id": message.get("order_id"),
                "trader_id": message.get("trader_id"),
                "reason": message.get("reason"),
            })

    async def _on_order_expired(self, message: dict) -> None:
        if message.get("type") == "order_expired":
            self.expired_orders += 1

    async def _on_stop_loss(self, message: dict) -> None:
        if message.get("type") != "stop_loss_liquidation":
            return
        self.stop_losses.append({
            "trader_id": message.get("trader_id"),
            "symbol": message.get("symbol"),
            "quantity": message.get("quantity"),
            "avg_cost": message.get("avg_cost"),
            "current_price": message.get("current_price"),
            "loss_percent": message.get("loss_percent"),
            "timestamp": message.get("timestamp"),
        })

    async def _on_portfolio_update(self, message: dict) -> None:
        if message.get("type") != "portfolio_snapshot":
            return
        self.portfolio_snapshots.append(message.get("data", {}))

    def _max_drawdown(self) -> float:
        values = [s.get("total_value", 0.0) for s in self.portfolio_snapshots]
        if len(values) < 2:
            return 0.0
        peak = values[0]
        worst = 0.0
        for value in values:
            peak = max(peak, value)
            if peak > 0:
                worst = max(worst, (peak - value) / peak)
        return worst

    async def generate_daily_report(self, simulation_date: str) -> dict:
        final = self.portfolio_snapshots[-1] if self.portfolio_snapshots else {}
        total_value = final.get("total_value", 0.0)
        realized = final.get("realized_pnl", 0.0)
        unrealized = final.get("unrealized_pnl", 0.0)

        total_decisions = self.approved_orders + self.rejected_orders_count
        rejected_pct = (self.rejected_orders_count / total_decisions * 100) if total_decisions else 0.0

        buys = sum(1 for t in self.trades if t["side"] == "BUY")
        sells = sum(1 for t in self.trades if t["side"] == "SELL")

        report = {
            "date": simulation_date,
            "summary": {
                "total_portfolio_value": total_value,
                "realized_pnl": realized,
                "unrealized_pnl": unrealized,
                "total_pnl": realized + unrealized,
                "total_trades": len(self.trades),
                "buy_orders": buys,
                "sell_orders": sells,
            },
            "risk_metrics": {
                "approved_orders": self.approved_orders,
                "rejected_orders": self.rejected_orders_count,
                "rejected_orders_pct": rejected_pct,
                "expired_orders": self.expired_orders,
                "stop_losses_triggered": len(self.stop_losses),
                "max_drawdown": self._max_drawdown(),
                "duplicate_trades_detected": self.duplicate_trades,
            },
            "trades": self.trades,
            "stop_losses": self.stop_losses,
            "rejected_orders": self.rejected_orders,
        }

        if self.reports_dir is not None:
            self.reports_dir.mkdir(parents=True, exist_ok=True)
            report_path = self.reports_dir / "daily_pnl.json"
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)
            self.logger.info(f"Daily report written: {report_path}")
            self._write_trades_csv(self.reports_dir / "trades_history.csv")
        return report

    def _write_trades_csv(self, path: Path) -> None:
        if not self.trades:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(self.trades[0].keys()))
            writer.writeheader()
            writer.writerows(self.trades)
        self.logger.info(f"Trades CSV written: {path}")
