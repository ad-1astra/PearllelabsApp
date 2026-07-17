#!/usr/bin/env python
"""Entry point for `lerobot-launch-lab`: starts the web server and optionally
opens the quest map in the default browser."""

from __future__ import annotations

import os
import threading
import time
import webbrowser

import uvicorn


def resolve_host_and_port() -> tuple[str, int]:
    host = os.environ.get("LAUNCH_LAB_HOST") or os.environ.get("HOST") or "0.0.0.0"
    port_value = os.environ.get("LAUNCH_LAB_PORT") or os.environ.get("PORT") or "8000"
    return host, int(port_value)


def main() -> None:
    from lerobot.robots.launch_lab.server import app, state

    host, port = resolve_host_and_port()

    try:
        from lerobot.robots.viewer_bridge import ViewerBridge

        bridge = ViewerBridge()
        bridge.start()
        state.viewer_bridge = bridge
    except Exception as e:
        print(f"3D viewer bridge unavailable ({e}); 'View 3D' will be disabled.")

    if os.environ.get("LAUNCH_LAB_OPEN_BROWSER", "0").lower() not in {"0", "false", "no"}:

        def _open_browser() -> None:
            time.sleep(0.8)
            webbrowser.open(f"http://{host}:{port}/")

        threading.Thread(target=_open_browser, daemon=True).start()

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
