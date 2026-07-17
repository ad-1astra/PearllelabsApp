#!/usr/bin/env python
"""Local helper for Launch Lab remote deployments.

This service runs on the machine that has access to the robot hardware. The
publicly-hosted Launch Lab instance can post to this helper to start real PTY
sessions and stream terminal output back over a WebSocket.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from lerobot.robots.launch_lab import commands
from lerobot.robots.launch_lab.pty_session import PtySession

DEFAULT_PORT = int(os.environ.get("LAUNCH_LAB_HELPER_PORT", "8091"))
REPO_ROOT = str(Path(commands.REPO_ROOT).resolve())

app = FastAPI(title="Launch Lab Robot Helper")


class RunRequest(BaseModel):
    action: str
    cmd: str | None = None
    cwd: str | None = None
    params: dict[str, Any] | None = None


_sessions: dict[str, PtySession] = {}
_session_output: dict[str, str] = {}
_session_exit_code: dict[str, int | None] = {}
_term_clients: dict[str, set[WebSocket]] = {}
_main_loop: asyncio.AbstractEventLoop | None = None


@app.on_event("startup")
async def _on_startup() -> None:
    global _main_loop
    _main_loop = asyncio.get_running_loop()


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/run")
def start_session(req: RunRequest) -> dict[str, Any]:
    session_id = uuid.uuid4().hex[:12]
    cwd = req.cwd or REPO_ROOT
    cmd = req.cmd or req.action

    def on_output(data: bytes) -> None:
        text = data.decode("utf-8", errors="replace")
        if not text:
            return
        buf = _session_output.get(session_id, "") + text
        _session_output[session_id] = buf[-400_000:]
        if _main_loop is None:
            return
        asyncio.run_coroutine_threadsafe(_send_term_output(session_id, text), _main_loop)

    def on_exit(code: int) -> None:
        _session_exit_code[session_id] = code

    session = PtySession(
        session_id=session_id,
        cmd=cmd,
        cwd=cwd,
        on_output=on_output,
        on_exit=on_exit,
    )
    _sessions[session_id] = session
    _session_output[session_id] = ""
    _session_exit_code[session_id] = None
    session.start()
    return {"session_id": session_id}


async def _send_term_output(session_id: str, text: str) -> None:
    dead: list[WebSocket] = []
    for ws in list(_term_clients.get(session_id, ())):
        try:
            await ws.send_json({"type": "output", "data": text})
        except Exception:
            dead.append(ws)
    for ws in dead:
        _term_clients.get(session_id, set()).discard(ws)


@app.websocket("/ws/term/{session_id}")
async def ws_term(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    if session_id not in _sessions:
        await websocket.send_json({"type": "output", "data": "\r\n[session not found]\r\n"})
        await websocket.close()
        return

    _term_clients.setdefault(session_id, set()).add(websocket)
    buffered = _session_output.get(session_id, "")
    if buffered:
        await websocket.send_json({"type": "output", "data": buffered})
    if _session_exit_code.get(session_id) is not None:
        await websocket.send_json({"type": "exit", "code": _session_exit_code[session_id]})

    session = _sessions[session_id]
    try:
        while True:
            msg = await websocket.receive_json()
            kind = msg.get("type")
            if kind == "input":
                session.write(msg.get("data", "").encode("utf-8", errors="replace"))
            elif kind == "resize":
                session.resize(int(msg.get("rows", 30)), int(msg.get("cols", 100)))
            elif kind == "interrupt":
                session.interrupt()
            elif kind == "stop":
                session.stop()
    except WebSocketDisconnect:
        pass
    finally:
        _term_clients.get(session_id, set()).discard(websocket)


def main() -> None:
    import uvicorn

    host = os.environ.get("LAUNCH_LAB_HELPER_HOST", "0.0.0.0")
    port = int(os.environ.get("LAUNCH_LAB_HELPER_PORT", str(DEFAULT_PORT)))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
