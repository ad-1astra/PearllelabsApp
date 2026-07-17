#!/usr/bin/env python
"""WebSocket + static-asset bridge between LeRobot GUI control logic and the browser-based 3D viewer.

Kept free of any Kivy/KivyMD imports so it can be started, driven, and tested
independently of the GUI.

Message schema (kept minimal and consistent across every call site):
  level_complete : {"event": "level_complete", "level": <str>, "joint": <str>, "arm": <str, optional>}
  joint angle    : {"joint": <str>, "angle": <float degrees>}
"""

from __future__ import annotations

import asyncio
import json
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import websockets

ASSETS_DIR = Path(__file__).parent / "viewer_assets"


class ViewerBridge:
    """Runs a local WebSocket server (robot state) + static file server (the viewer page)."""

    def __init__(self, ws_host="localhost", ws_port=8765, http_host="localhost", http_port=8000):
        self.ws_host = ws_host
        self.ws_port = ws_port
        self.http_host = http_host
        self.http_port = http_port
        self._clients: set = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._http_server: ThreadingHTTPServer | None = None
        self._stop_event: asyncio.Event | None = None

    def start(self):
        threading.Thread(target=self._run_ws_loop, daemon=True).start()

        def _handler(*args, **kwargs):
            return SimpleHTTPRequestHandler(*args, directory=str(ASSETS_DIR), **kwargs)

        self._http_server = ThreadingHTTPServer((self.http_host, self.http_port), _handler)
        threading.Thread(target=self._http_server.serve_forever, daemon=True).start()

    def _run_ws_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        async def _handle_client(websocket):
            self._clients.add(websocket)
            try:
                async for _ in websocket:
                    pass  # the viewer is a passive listener; it doesn't send commands back
            finally:
                self._clients.discard(websocket)

        async def _main():
            self._stop_event = asyncio.Event()
            async with websockets.serve(_handle_client, self.ws_host, self.ws_port):
                await self._stop_event.wait()

        try:
            self._loop.run_until_complete(_main())
        finally:
            self._loop.close()

    def stop(self):
        if self._http_server:
            self._http_server.shutdown()
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)

    def _broadcast(self, message: dict):
        if not self._loop:
            return
        payload = json.dumps(message)

        async def _send_all():
            dead = []
            for ws in list(self._clients):
                try:
                    await ws.send(payload)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self._clients.discard(ws)

        asyncio.run_coroutine_threadsafe(_send_all(), self._loop)

    def send_level_complete(self, level: str, joint: str = "all", arm: str | None = None):
        message = {"event": "level_complete", "level": level, "joint": joint}
        if arm:
            message["arm"] = arm
        self._broadcast(message)

    def send_joint_angle(self, joint: str, angle: float):
        self._broadcast({"joint": joint, "angle": angle})

    def open_viewer(self):
        webbrowser.open(f"http://{self.http_host}:{self.http_port}/viewer.html")
