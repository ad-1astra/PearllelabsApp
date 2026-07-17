#!/usr/bin/env python
"""Entry point for `lerobot-launch-lab`: starts the local web server and
opens the quest map in the default browser."""

from __future__ import annotations

import threading
import time
import webbrowser

import uvicorn

HOST = "127.0.0.1"
PORT = 8090


def main() -> None:
    from lerobot.robots.launch_lab.server import app, state

    try:
        from lerobot.robots.viewer_bridge import ViewerBridge

        bridge = ViewerBridge()
        bridge.start()
        state.viewer_bridge = bridge
    except Exception as e:
        print(f"3D viewer bridge unavailable ({e}); 'View 3D' will be disabled.")

    def _open_browser() -> None:
        time.sleep(0.8)
        webbrowser.open(f"http://{HOST}:{PORT}/")

    threading.Thread(target=_open_browser, daemon=True).start()
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
