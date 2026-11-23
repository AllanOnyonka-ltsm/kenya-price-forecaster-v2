"""Message buses for the swarm.

LocalMessageBus (default) is an in-memory FIFO. ``drain()`` delivers queued
messages one at a time, in publish order, and waits for each callback before
starting the next one. Because delivery is sequential and single-threaded, a
seeded run always produces the same sequence of events.

RedisMessageBus is optional. It needs a running Redis server and uses
``redis.asyncio``, which is imported only when the bus is connected. Delivery
over Redis pub/sub is asynchronous, so runs over this bus are not guaranteed to
be identical from run to run. It is not covered by the offline test suite.
"""

from abc import ABC, abstractmethod
from collections import deque
from typing import Any, Awaitable, Callable, Optional
import asyncio
import json
import logging

logger = logging.getLogger(__name__)

Callback = Callable[[dict[str, Any]], Awaitable[None]]


class MessageBus(ABC):
    @abstractmethod
    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        ...

    @abstractmethod
    async def subscribe(self, channel: str, callback: Callback) -> None:
        ...

    @abstractmethod
    async def drain(self) -> None:
        """Block until every message published so far has been delivered."""

    @abstractmethod
    async def close(self) -> None:
        ...


class LocalMessageBus(MessageBus):
    """Deterministic in-memory bus. Exceptions raised by callbacks propagate."""

    def __init__(self, max_messages_per_drain: int = 1_000_000):
        self.channels: dict[str, list[Callback]] = {}
        self._queue: deque[tuple[str, dict[str, Any]]] = deque()
        self._max_messages = max_messages_per_drain
        self.delivered_count = 0

    async def start(self) -> None:
        """Kept for API compatibility. The in-memory bus needs no start-up."""

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        if not isinstance(message, dict):
            raise TypeError("message must be a dict")
        self._queue.append((channel, dict(message)))

    async def subscribe(self, channel: str, callback: Callback) -> None:
        self.channels.setdefault(channel, []).append(callback)

    async def drain(self) -> None:
        delivered = 0
        while self._queue:
            channel, data = self._queue.popleft()
            for callback in list(self.channels.get(channel, [])):
                # Each subscriber gets its own copy, so one cannot mutate another's view.
                await callback(dict(data))
            delivered += 1
            if delivered > self._max_messages:
                raise RuntimeError(
                    f"drain() delivered more than {self._max_messages} messages; "
                    "possible message loop"
                )
        self.delivered_count += delivered

    async def close(self) -> None:
        self._queue.clear()


class RedisMessageBus(MessageBus):
    """Optional Redis pub/sub transport. Requires a running Redis server."""

    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.redis_url = redis_url
        self.redis_client: Optional[Any] = None
        self.pubsub: Optional[Any] = None
        self.subscribers: dict[str, list[Callback]] = {}
        self._listener: Optional[asyncio.Task] = None
        self._sent = 0
        self._received = 0
        self._error: Optional[BaseException] = None
        self._cond: Optional[asyncio.Condition] = None

    async def connect(self) -> None:
        import redis.asyncio as redis_asyncio  # lazy: only needed for this bus

        self.redis_client = redis_asyncio.from_url(self.redis_url, decode_responses=True)
        await self.redis_client.ping()
        self.pubsub = self.redis_client.pubsub()
        self._cond = asyncio.Condition()
        logger.info(f"Connected to Redis at {self.redis_url}")

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        if self.redis_client is None:
            raise RuntimeError("Redis bus is not connected; call connect() first")
        if channel in self.subscribers:
            self._sent += 1  # counted only when this process will receive it back
        await self.redis_client.publish(channel, json.dumps(message, default=str))

    async def subscribe(self, channel: str, callback: Callback) -> None:
        if self.pubsub is None:
            raise RuntimeError("Redis bus is not connected; call connect() first")
        if channel not in self.subscribers:
            self.subscribers[channel] = []
            await self.pubsub.subscribe(channel)
        self.subscribers[channel].append(callback)
        if self._listener is None:
            self._listener = asyncio.create_task(self._listen())

    async def _listen(self) -> None:
        try:
            async for raw in self.pubsub.listen():
                if raw.get("type") != "message":
                    continue
                data = json.loads(raw["data"])
                for callback in list(self.subscribers.get(raw["channel"], [])):
                    await callback(data)
                async with self._cond:
                    self._received += 1
                    self._cond.notify_all()
        except BaseException as exc:  # surface listener failures to drain()
            self._error = exc
            if self._cond is not None:
                async with self._cond:
                    self._cond.notify_all()
            raise

    async def drain(self) -> None:
        if self._cond is None:
            raise RuntimeError("Redis bus is not connected; call connect() first")
        async with self._cond:
            await self._cond.wait_for(
                lambda: self._error is not None or self._received >= self._sent
            )
        if self._error is not None:
            raise RuntimeError("Redis listener failed") from self._error

    async def close(self) -> None:
        if self._listener is not None:
            self._listener.cancel()
            try:
                await self._listener
            except (asyncio.CancelledError, Exception):
                pass
            self._listener = None
        if self.pubsub is not None:
            await self.pubsub.close()
        if self.redis_client is not None:
            await self.redis_client.close()
        logger.info("Redis bus closed")
