"""Noether — entry point. Boots:
  1. The Emmy pipeline (audio → STT → Claude → TTS) in a worker thread.
  2. A FastAPI server with a WebSocket the browser UI subscribes to.
  3. The UI in Safari (override with NOETHER_BROWSER=safari|default|none).
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
import webbrowser

import uvicorn
from dotenv import load_dotenv

load_dotenv()

from controls import Controls
from loop import run_loop
from server import create_app
from state_bus import StateBus

HOST = "127.0.0.1"
PORT = 8765

# Which browser to open the UI in: "safari" (default), "default" (system
# default), or "none" (don't auto-open — e.g. you use the VS Code Simple Browser).
BROWSER = os.environ.get("NOETHER_BROWSER", "safari").lower()


def main() -> None:
    bus = StateBus()
    controls = Controls()
    app = create_app(bus, controls)

    loop_thread = threading.Thread(target=run_loop, args=(bus, controls), daemon=True)
    loop_thread.start()

    def open_browser() -> None:
        time.sleep(0.8)
        url = f"http://{HOST}:{PORT}/"
        if BROWSER == "none":
            return
        if BROWSER == "safari":
            try:
                subprocess.run(["open", "-a", "Safari", url], check=True)
                return
            except Exception:
                pass  # fall back to the system default
        webbrowser.open(url)

    threading.Thread(target=open_browser, daemon=True).start()

    print(f"Noether UI: http://{HOST}:{PORT}/")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
