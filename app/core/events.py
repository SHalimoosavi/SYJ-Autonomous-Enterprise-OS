"""
Event bus abstraction. Backend is chosen by config (EVENT_BUS_BACKEND):
  - "memory": in-process pub/sub, works everywhere including Termux. Default for dev.
  - "redis":  Redis pub/sub for multi-process production deployments.

Departments publish/subscribe only through this interface, never touching
the backend directly — swapping memory -> redis is a config change only.
"""
import asyncio
import structlog
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Awaitable, Callable

logger = structlog.get_logger()
Handler = Callable[[dict], Awaitable[None]]


class EventBus(ABC):
    @abstractmethod
    async def publish(self, topic: str, payload: dict) -> None: ...

    @abstractmethod
    def subscribe(self, topic: str, handler: Handler) -> None: ...


class InMemoryEventBus(EventBus):
    """Default bus for Termux/local dev. No external services required."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, topic: str, handler: Handler) -> None:
        self._subscribers[topic].append(handler)

    async def publish(self, topic: str, payload: dict) -> None:
        logger.info("event.publish", topic=topic, payload=payload)
        for handler in self._subscribers.get(topic, []):
            asyncio.create_task(handler(payload))


class RedisEventBus(EventBus):
    """
    Production Deployment: requires a running redis-server, not available
    natively in Termux. Implement with redis.asyncio pub/sub when deploying
    to Linux/cloud. Left as an interface stub here intentionally.
    """

    def __init__(self, redis_url: str) -> None:
        self.redis_url = redis_url
        raise NotImplementedError(
            "RedisEventBus is a Production Deployment task — see docs/TERMUX.md"
        )

    async def publish(self, topic: str, payload: dict) -> None: ...
    def subscribe(self, topic: str, handler: Handler) -> None: ...


def get_event_bus() -> EventBus:
    from app.core.config import get_settings
    settings = get_settings()
    if settings.EVENT_BUS_BACKEND == "redis":
        return RedisEventBus(settings.REDIS_URL)
    return InMemoryEventBus()


event_bus = get_event_bus()
