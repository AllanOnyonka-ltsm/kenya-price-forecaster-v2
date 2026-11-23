from datetime import datetime
from math import isfinite
from typing import Optional
import logging
import random

from .base_agent import BaseAgent
from core.message_bus import MessageBus

logger = logging.getLogger(__name__)


class AnalystAgent(BaseAgent):
    """Emits a signal from a bar's own open and close.

    The signal is made at the close of bar t. The order it leads to can only
    fill at a later bar's open (see core/market_environment.py), so the signal
    never uses information that was not available when it was made.

    The 30% emission gate uses an injected random.Random so seeded runs match.
    """

    EMIT_PROBABILITY = 0.3

    def __init__(
        self,
        agent_id: str,
        message_bus: MessageBus,
        symbols: list[str],
        rng: Optional[random.Random] = None,
        log_level: str = "INFO",
    ):
        super().__init__(agent_id, message_bus, log_level)
        self.symbols = list(symbols)
        self.rng = rng if rng is not None else random.Random(0)
        self.signal_count = 0

    async def _setup_subscriptions(self) -> None:
        await self.subscribe("market_data", self._on_market_update)

    async def _on_market_update(self, message: dict) -> None:
        if message.get("type") != "market_update":
            return
        symbol = message.get("symbol")
        if symbol not in self.symbols:
            return

        close_price = message.get("close")
        open_price = message.get("open")
        if close_price is None or open_price is None:
            return
        if not (isfinite(close_price) and isfinite(open_price)) or open_price <= 0:
            return

        price_change = (close_price - open_price) / open_price

        if self.rng.random() < self.EMIT_PROBABILITY:
            await self._generate_signal(symbol, price_change, message.get("timestamp"))

    async def _generate_signal(
        self, symbol: str, price_change: float, timestamp: Optional[str]
    ) -> None:
        if abs(price_change) < 0.01:
            return

        if price_change > 0.02:
            signal_type = "BULLISH"
            confidence = min(0.9, 0.5 + abs(price_change) * 10)
        elif price_change < -0.02:
            signal_type = "BEARISH"
            confidence = min(0.9, 0.5 + abs(price_change) * 10)
        else:
            signal_type = "NEUTRAL"
            confidence = 0.3

        self.signal_count += 1
        signal_id = f"{self.agent_id}_signal_{self.signal_count}"
        await self.publish(
            "signals",
            {
                "type": "signal",
                "signal_id": signal_id,
                "timestamp": timestamp or datetime.min.isoformat(),
                "agent_id": self.agent_id,
                "symbol": symbol,
                "signal_type": signal_type,
                "confidence": confidence,
                "reasoning": f"Bar return {price_change * 100:.2f}% (open to close)",
            },
        )
        self.logger.info(
            "Generated signal",
            signal_id=signal_id,
            symbol=symbol,
            type=signal_type,
            confidence=f"{confidence:.2f}",
        )
