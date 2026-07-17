#!/usr/bin/env python
"""Entry point for `lerobot-launch-lab`: starts the web server and optionally
opens the quest map in the default browser."""

from __future__ import annotations

import os
import socket
import threading
import time
import webbrowser

import uvicorn


def _find_available_port(preferred_port: int) -> int:
    candidates = [preferred_port, *range(preferred_port + 1, preferred_port + 20)]
    for port in candidates:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    return preferred_port


def resolve_host_and_port() -> tuple[str, int]:
    host = os.environ.get("LAUNCH_LAB_HOST") or os.environ.get("HOST") or "0.0.0.0"
    port_value = os.environ.get("LAUNCH_LAB_PORT") or os.environ.get("PORT")
    preferred_port = int(port_value) if port_value else 8090
    port = _find_available_port(preferred_port)
    if port != preferred_port:
        print(f"Port {preferred_port} is busy; using {port} instead.")
    return host, port


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
