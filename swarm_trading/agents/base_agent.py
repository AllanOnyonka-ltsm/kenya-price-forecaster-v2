from abc import ABC, abstractmethod
import logging

from core.message_bus import MessageBus
from core.logger import StructuredLogger

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Agents are event-driven: they only react to bus messages.

    start() subscribes to channels. There is no background loop and no
    wall-clock timer, so the simulation's order of events depends only on
    the data and the seed. Exceptions raised while publishing or subscribing
    are not caught here; they propagate to the caller.
    """

    def __init__(self, agent_id: str, message_bus: MessageBus, log_level: str = "INFO"):
        self.agent_id = agent_id
        self.message_bus = message_bus
        self.logger = StructuredLogger(agent_id, log_level)
        self._started = False

    async def start(self) -> None:
        if self._started:
            self.logger.warning("Agent already started")
            return
        self._started = True
        await self._setup_subscriptions()
        self.logger.info(f"Agent {self.agent_id} started")

    async def stop(self) -> None:
        self._started = False
        self.logger.info(f"Agent {self.agent_id} stopped")

    @abstractmethod
    async def _setup_subscriptions(self) -> None:
        ...

    async def publish(self, channel: str, message: dict) -> None:
        await self.message_bus.publish(channel, message)

    async def subscribe(self, channel: str, callback) -> None:
        await self.message_bus.subscribe(channel, callback)
