"""Thread-safe pub/sub bridge between the Emmy loop (thread) and the
FastAPI WebSocket handlers (asyncio).

The loop calls `publish()` from its worker thread; each connected client has
its own asyncio.Queue that mirrors broadcasts. Drops events for slow clients
rather than blocking the loop.
"""

from __future__ import annotations

import asyncio
from typing import Any


class StateBus:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._last_state: dict[str, Any] = {"type": "state", "value": "starting"}

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._subscribers.add(queue)
        queue.put_nowait(self._last_state)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: dict[str, Any]) -> None:
        """Thread-safe broadcast. Safe to call from any thread."""
        if event.get("type") == "state":
            self._last_state = event
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(self._fanout, event)

    def _fanout(self, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Slow client — drop the event rather than back-pressure the loop.
                pass
