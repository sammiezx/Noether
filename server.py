"""FastAPI app: serves the static UI and a bidirectional WebSocket.

Outbound (server → client): state events from the StateBus.
Inbound (client → server): runtime commands, currently just pause/resume.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from controls import Controls
from state_bus import StateBus

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"


def create_app(bus: StateBus, controls: Controls) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def _no_cache(request, call_next):
        # The UI assets evolve constantly during development; tell the browser
        # never to cache them so edits show up on a normal reload (no more
        # stale app.js).
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.on_event("startup")
    async def _bind_loop() -> None:
        bus.bind_loop(asyncio.get_running_loop())

    @app.websocket("/ws")
    async def ws_events(websocket: WebSocket) -> None:
        await websocket.accept()
        queue = bus.subscribe()

        # Push the current paused state so the UI button starts in sync.
        await websocket.send_json({"type": "paused", "value": controls.paused})

        # Push the current persona so a freshly-loaded page shows the right
        # brand + face immediately (the live "persona" event only fires on a
        # switch, which a new page would otherwise miss).
        try:
            import persona
            await websocket.send_json(
                {"type": "persona", "key": persona.current(), "label": persona.label()}
            )
        except Exception:
            pass

        async def sender() -> None:
            while True:
                event = await queue.get()
                await websocket.send_json(event)

        async def receiver() -> None:
            while True:
                msg = await websocket.receive_json()
                command = msg.get("command")
                if command == "toggle_pause":
                    paused = controls.toggle()
                    bus.publish({"type": "paused", "value": paused})
                elif command == "pause":
                    controls.pause()
                    bus.publish({"type": "paused", "value": True})
                elif command == "resume":
                    controls.resume()
                    bus.publish({"type": "paused", "value": False})
                elif command == "shutdown":
                    bus.publish({"type": "state", "value": "terminating"})
                    bus.publish({"type": "assistant", "text": "Powering down. Catch you later."})

                    # Let the broadcast reach the UI, then tear everything down:
                    # kill the neural-voice helper subprocess (torch + a ~1.5GB
                    # model — must not be orphaned), then hard-exit the whole
                    # process. os._exit is immediate and can't hang on the open
                    # WebSocket the way a graceful SIGINT shutdown can.
                    async def _quit() -> None:
                        await asyncio.sleep(0.4)
                        try:
                            import tts
                            tts.shutdown_all()
                        except Exception:
                            pass
                        os._exit(0)
                    asyncio.create_task(_quit())

        send_task = asyncio.create_task(sender())
        recv_task = asyncio.create_task(receiver())
        try:
            done, _ = await asyncio.wait(
                {send_task, recv_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                # Surface unexpected exceptions; ignore clean disconnects.
                exc = task.exception()
                if exc and not isinstance(exc, WebSocketDisconnect):
                    raise exc
        finally:
            send_task.cancel()
            recv_task.cancel()
            bus.unsubscribe(queue)

    # Mount the equation visualizer as a sub-app at /equations. Must be
    # registered before the catch-all "/" static mount below, or that mount
    # would shadow it. Imported lazily so a missing ANTHROPIC_API_KEY only
    # bites when the server actually boots (not at module import time).
    from equation_viz.app import app as equation_app

    # Bare /equations (no trailing slash) doesn't reach the sub-app's "/"
    # route, so send it to /equations/ where relative asset/API paths resolve.
    @app.get("/equations")
    async def _equations_redirect() -> RedirectResponse:
        return RedirectResponse(url="/equations/")

    app.mount("/equations", equation_app, name="equations")

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app
