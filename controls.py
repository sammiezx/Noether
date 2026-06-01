"""Thread-safe runtime controls shared between the WebSocket handler
(asyncio loop) and the Jarvis worker thread."""

from __future__ import annotations

import threading


class Controls:
    def __init__(self) -> None:
        self._paused = threading.Event()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def toggle(self) -> bool:
        if self._paused.is_set():
            self._paused.clear()
        else:
            self._paused.set()
        return self._paused.is_set()
