"""FastAPI app: serves the frontend, exposes a small REST API to start quest
actions, and two WebSocket channels:

  /ws/term/{session_id}  -- raw PTY input/output for one running command
  /ws/events             -- structured game-state events (XP, level-complete,
                             motor checklist, install progress, error tips)
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any
from urllib import request as urllib_request

try:
    import websockets
except ImportError:  # pragma: no cover - optional dependency in some environments
    websockets = None

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from lerobot.robots.launch_lab import commands
from lerobot.robots.launch_lab.pty_session import PtySession, strip_ansi
from lerobot.robots.launch_lab.quest_state import LEVEL_TITLES, LEVELS, AppState
from lerobot.robots.setup_quest_utils import detect_error_suggestion

STATIC_DIR = Path(__file__).parent / "static"

# This server can run purely locally (the `lerobot-launch-lab` CLI serves both the
# frontend and this API from the same origin) or as a "local helper" behind a
# separately-hosted frontend (e.g. Firebase Hosting) -- the PTY/USB/sudo access this
# app needs only makes sense on the machine actually running the robot, so the API
# always stays local; only the static page may be hosted elsewhere. In that split
# setup the hosted page is cross-origin from this server's point of view, so both
# REST (CORS) and the two WebSocket endpoints (which CORS does NOT cover -- browsers
# don't apply CORS preflight to WebSocket handshakes) need an explicit origin
# allow-list, or any website open in another tab could drive this machine's terminal.
_DEFAULT_ORIGINS = (
    "http://localhost:8090,http://127.0.0.1:8090,"
    "https://pearllelab.web.app,https://pearllelab.firebaseapp.com"
)
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("LAUNCH_LAB_ALLOWED_ORIGINS", _DEFAULT_ORIGINS).split(",") if o.strip()]

app = FastAPI(title="LeRobot Launch Lab")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    # A public HTTPS page (Firebase Hosting) fetching a loopback address (this local
    # helper) triggers Chrome's Private Network Access preflight on top of normal CORS
    # -- without opting in here, Chrome blocks the request regardless of the Origin
    # allow-list above.
    allow_private_network=True,
)

state = AppState()


def _origin_allowed(websocket: WebSocket) -> bool:
    """Reject cross-origin WebSocket handshakes from anywhere not on the allow-list.

    A missing Origin header means a non-browser client (curl, tests, another local
    script) -- those can't be a drive-by webpage, so they're let through.
    """
    origin = websocket.headers.get("origin")
    return origin is None or origin in ALLOWED_ORIGINS


# ── event broadcast plumbing ────────────────────────────────────

_event_clients: set[WebSocket] = set()
_main_loop: asyncio.AbstractEventLoop | None = None


def _broadcast(event: dict[str, Any]) -> None:
    if _main_loop is None:
        return
    asyncio.run_coroutine_threadsafe(_broadcast_async(event), _main_loop)


async def _broadcast_async(event: dict[str, Any]) -> None:
    dead = []
    for ws in list(_event_clients):
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _event_clients.discard(ws)


state.set_broadcast(_broadcast)


@app.on_event("startup")
async def _on_startup() -> None:
    global _main_loop
    _main_loop = asyncio.get_running_loop()


@app.websocket("/ws/events")
async def ws_events(websocket: WebSocket) -> None:
    if not _origin_allowed(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    _event_clients.add(websocket)
    await websocket.send_json(state.snapshot())
    try:
        while True:
            await websocket.receive_text()  # events channel is server -> client only
    except WebSocketDisconnect:
        pass
    finally:
        _event_clients.discard(websocket)


# ── session registry ─────────────────────────────────────────────

_SESSION_HELPER_URL = (os.environ.get("LAUNCH_LAB_HELPER_URL") or os.environ.get("LAUNCH_LAB_REMOTE_HELPER_URL") or "").rstrip("/")
_REMOTE_HELPER_ENABLED = bool(_SESSION_HELPER_URL)

_sessions: dict[str, PtySession] = {}
_session_transport: dict[str, str] = {}
_session_kind: dict[str, str] = {}  # session_id -> action key, for on_exit bookkeeping
_active_session_id: str | None = None

# The PTY starts streaming the instant the session is created (before the
# browser's terminal WebSocket has a chance to connect), so we buffer output
# per session and replay it to any client that connects late. Capped so a
# long-running train/record session can't grow this unbounded.
_OUTPUT_BUFFER_CAP = 400_000
_session_output: dict[str, str] = {}
_session_exit_code: dict[str, int | None] = {}
_session_line_buf: dict[str, str] = {}
_session_on_line: dict[str, Any] = {}
_session_on_exit_extra: dict[str, Any] = {}


def _start_local_session(action: str, cmd: str, on_line=None, on_exit_extra=None, filter_output=None) -> str:
    global _active_session_id
    session_id = uuid.uuid4().hex[:12]
    _session_output[session_id] = ""
    _session_exit_code[session_id] = None
    _session_line_buf[session_id] = ""
    _session_on_line[session_id] = on_line
    _session_on_exit_extra[session_id] = on_exit_extra

    def on_output(data: bytes) -> None:
        text = data.decode("utf-8", errors="replace")
        if filter_output:
            text = filter_output(text)
        if not text:
            return
        buf = _session_output[session_id] + text
        if len(buf) > _OUTPUT_BUFFER_CAP:
            buf = buf[-_OUTPUT_BUFFER_CAP:]
        _session_output[session_id] = buf
        if _main_loop is None:
            return
        asyncio.run_coroutine_threadsafe(_send_term_output(session_id, text), _main_loop)

    def on_line_wrapped(line: str) -> None:
        lower = line.lower()
        if any(k in lower for k in ("error", "failed", "no status packet", "traceback", "exception")):
            state.error_tip(session_id, detect_error_suggestion(line))
        if on_line:
            on_line(line)

    def on_exit(code: int) -> None:
        global _active_session_id
        if _active_session_id == session_id:
            _active_session_id = None
        _session_exit_code[session_id] = code
        stopped = _sessions[session_id].stopped

        # Level-progression callback (e.g. "calibrate finished successfully" ->
        # complete_level) fires FIRST and unconditionally (aside from the deliberate
        # `stopped` check below) -- this must never be skipped just because something
        # else afterward (the shell auto-continuation) had a problem. A manual Stop is
        # a deliberate interruption, not a real completion, so it's excluded here even
        # though we now always notify the frontend either way.
        if on_exit_extra and not stopped:
            on_exit_extra(code)

        # Once the specific command finishes -- whether it ran to completion or was
        # manually Stopped -- the terminal would otherwise go inert: typing into it
        # does nothing, since PtySession.write() is a no-op once the process has
        # exited. Auto-continue into a plain interactive shell in the same terminal so
        # it stays usable for pasting any follow-up command directly, exactly like a
        # normal terminal. Guarded so a shell's own exit (e.g. typing "exit") doesn't
        # chain into another shell forever. Wrapped defensively: this is a nice-to-have
        # on top of the command that already ran, and must never be allowed to prevent
        # the session_exit notification itself from reaching the frontend (previously,
        # an exception here -- unverified on Windows -- would have silently swallowed
        # everything below it, including the notification that makes "Done" ever show
        # up at all).
        next_session_id = None
        if action != "shell":
            try:
                next_session_id = _start_local_session("shell", commands.shell_cmd())
            except Exception as exc:
                print(f"Auto-continuation shell failed to start (non-fatal): {exc}")
        _broadcast(
            {"type": "session_exit", "session_id": session_id, "action": action, "code": code, "next_session_id": next_session_id}
        )

    session = PtySession(
        session_id=session_id,
        cmd=cmd,
        cwd=commands.REPO_ROOT,
        on_output=on_output,
        on_line=on_line_wrapped,
        on_exit=on_exit,
    )
    _sessions[session_id] = session
    _session_transport[session_id] = "local"
    _session_kind[session_id] = action
    _active_session_id = session_id
    session.start()
    return session_id


def _start_remote_session(action: str, cmd: str, on_line=None, on_exit_extra=None) -> str | None:
    if not _REMOTE_HELPER_ENABLED:
        return None

    payload = json.dumps({"action": action, "cmd": cmd, "cwd": commands.REPO_ROOT}).encode("utf-8")
    req = urllib_request.Request(
        f"{_SESSION_HELPER_URL}/api/run",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib_request.urlopen(req, timeout=10) as response:
            body = json.loads(response.read().decode("utf-8"))
            session_id = body.get("session_id")
    except Exception as exc:  # pragma: no cover - depends on remote helper availability
        print(f"Remote helper unavailable: {exc}")
        return None

    if not session_id:
        return None

    _session_output[session_id] = ""
    _session_exit_code[session_id] = None
    _session_line_buf[session_id] = ""
    _session_on_line[session_id] = on_line
    _session_on_exit_extra[session_id] = on_exit_extra
    _session_transport[session_id] = "remote"
    _session_kind[session_id] = action
    _active_session_id = session_id
    return session_id


def _start_session(action: str, cmd: str, on_line=None, on_exit_extra=None, filter_output=None) -> str:
    if _REMOTE_HELPER_ENABLED:
        remote_session_id = _start_remote_session(action, cmd, on_line=on_line, on_exit_extra=on_exit_extra)
        if remote_session_id is not None:
            return remote_session_id
    return _start_local_session(action, cmd, on_line=on_line, on_exit_extra=on_exit_extra, filter_output=filter_output)


async def _send_term_output(session_id: str, text: str) -> None:
    dead = []
    for ws in list(_term_clients.get(session_id, ())):
        try:
            await ws.send_json({"type": "output", "data": text})
        except Exception:
            dead.append(ws)
    for ws in dead:
        _term_clients.get(session_id, set()).discard(ws)


_term_clients: dict[str, set[WebSocket]] = {}


def _dispatch_output(session_id: str, text: str) -> None:
    if not text:
        return
    buf = _session_line_buf.get(session_id, "") + strip_ansi(text.encode("utf-8", errors="replace"))
    while "\n" in buf:
        line, buf = buf.split("\n", 1)
        line = line.strip()
        if line:
            on_line = _session_on_line.get(session_id)
            if on_line:
                on_line(line)
    _session_line_buf[session_id] = buf


async def _proxy_remote_terminal(websocket: WebSocket, session_id: str) -> None:
    if websockets is None:
        await websocket.send_json({"type": "output", "data": "\r\n[remote helper unavailable]\r\n"})
        await websocket.close()
        return

    helper_url = _SESSION_HELPER_URL.replace("http://", "ws://").replace("https://", "wss://")
    helper_ws_url = f"{helper_url}/ws/term/{session_id}"
    try:
        async with websockets.connect(helper_ws_url) as helper_ws:  # type: ignore[attr-defined]
            async def forward_helper_to_browser() -> None:
                async for payload in helper_ws:
                    msg = json.loads(payload)
                    if msg.get("type") == "output":
                        _dispatch_output(session_id, msg.get("data", ""))
                    elif msg.get("type") == "exit":
                        _session_exit_code[session_id] = msg.get("code")
                        on_exit_extra = _session_on_exit_extra.get(session_id)
                        if on_exit_extra:
                            on_exit_extra(msg.get("code", 0))
                    await websocket.send_json(msg)

            async def forward_browser_to_helper() -> None:
                while True:
                    msg = await websocket.receive_json()
                    await helper_ws.send(json.dumps(msg))

            await asyncio.gather(forward_helper_to_browser(), forward_browser_to_helper())
    except Exception as exc:  # pragma: no cover - depends on remote helper availability
        await websocket.send_json({"type": "output", "data": f"\r\n[remote helper error: {exc}]\r\n"})
        await websocket.close()


@app.websocket("/ws/term/{session_id}")
async def ws_term(websocket: WebSocket, session_id: str) -> None:
    if not _origin_allowed(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    if session_id not in _sessions and session_id not in _session_output:
        await websocket.send_json({"type": "output", "data": "\r\n[session not found]\r\n"})
        await websocket.close()
        return

    if _session_transport.get(session_id) == "remote":
        await _proxy_remote_terminal(websocket, session_id)
        return

    # Register before replaying the buffer so no output that arrives during
    # the replay itself can be missed.
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


# ── REST: quest actions ───────────────────────────────────────────


class RunRequest(BaseModel):
    action: str
    params: dict[str, Any] = {}


@app.get("/api/state")
def get_state() -> dict:
    return state.snapshot()


@app.get("/api/install/steps")
def get_install_steps() -> dict:
    steps = commands.install_steps()
    return {"steps": [{"key": s["key"], "label": s["label"], "already_installed": s["already_installed"]} for s in steps]}


@app.get("/api/quest")
def get_quest() -> dict:
    return {
        "levels": LEVELS,
        "level_titles": LEVEL_TITLES,
        "steps": [dataclasses.asdict(s) for s in state.quest_steps],
    }


@app.post("/api/run")
def run_action(req: RunRequest) -> dict:
    if _active_session_id is not None and _sessions[_active_session_id].is_alive():
        # An idle auto-continuation shell (see _start_local_session's on_exit) isn't a
        # real "something is busy" state -- it's just sitting at a prompt. Without this,
        # every action after the very first one would be permanently blocked, since
        # there's always an idle shell "running" once anything has ever completed.
        if _session_kind.get(_active_session_id) == "shell":
            _sessions[_active_session_id].stop()
        else:
            return {"error": "A command is already running. Stop it first, or wait for it to finish."}

    action = req.action
    p = req.params

    if action == "install_run":
        return {"session_id": _start_install_session()}

    if action == "hf_login":
        return {"session_id": _start_session("hf_login", commands.hf_login_cmd())}

    if action == "find_port":
        return {"session_id": _start_session("find_port", commands.find_port_cmd(), on_line=_on_find_port_line(p.get("arm", "follower")))}

    if action == "find_cameras":
        return {"session_id": _start_session("find_cameras", commands.find_cameras_cmd())}

    if action == "setup_motors":
        arm = p["arm"]
        port = state.found_ports.get(arm, "")
        if not port:
            return {"error": f"No port stored for {arm}. Run Find Ports first."}
        state.reset_motor_checklist()
        cmd = commands.setup_motors_cmd(arm, port)
        return {
            "session_id": _start_session(
                "setup_motors",
                cmd,
                on_line=state.on_setup_motor_line,
                on_exit_extra=lambda code: state.complete_level("set_motor_ids", arm=arm) if code == 0 else None,
            )
        }

    if action == "calibrate":
        arm = p["arm"]
        port = state.found_ports.get(arm, "") or (commands.DEFAULT_FOLLOWER_PORT if arm == "follower" else commands.DEFAULT_LEADER_PORT)
        cmd = commands.calibrate_cmd(arm, port)
        return {
            "session_id": _start_session(
                "calibrate", cmd, on_exit_extra=lambda code: state.complete_level("calibrate", arm=arm) if code == 0 else None
            )
        }

    if action == "teleoperate":
        cmd = commands.teleoperate_cmd(
            state.found_ports.get("follower", commands.DEFAULT_FOLLOWER_PORT),
            state.found_ports.get("leader", commands.DEFAULT_LEADER_PORT),
            p.get("cameras", []),
        )
        return {
            "session_id": _start_session(
                "teleoperate", cmd, on_exit_extra=lambda code: state.complete_level("teleoperate") if code == 0 else None
            )
        }

    if action == "record":
        repo_id = p["repo_id"]
        cmd = commands.record_cmd(
            state.found_ports.get("follower", commands.DEFAULT_FOLLOWER_PORT),
            state.found_ports.get("leader", commands.DEFAULT_LEADER_PORT),
            p.get("cameras", []),
            repo_id,
            p.get("task", ""),
            int(p.get("num_episodes", 50)),
            int(p.get("episode_time_s", 30)),
            int(p.get("reset_time_s", 10)),
        )

        def _done(code: int) -> None:
            if code == 0:
                state.set_last_dataset(repo_id)
                state.complete_level("record")

        return {"session_id": _start_session("record", cmd, on_exit_extra=_done)}

    if action == "replay":
        cmd = commands.replay_cmd(
            state.found_ports.get("follower", commands.DEFAULT_FOLLOWER_PORT), p["repo_id"], int(p.get("episode", 0))
        )
        return {"session_id": _start_session("replay", cmd)}

    if action == "train":
        policy_repo_id = p.get("policy_repo_id") or None
        cmd = commands.train_cmd(
            repo_id=p["repo_id"],
            policy_type=p.get("policy_type", "act"),
            device=p.get("device", "cuda"),
            batch_size=int(p.get("batch_size", 8)),
            steps=int(p["steps"]) if p.get("steps") else None,
            wandb_enable=bool(p.get("wandb_enable", False)),
            policy_repo_id=policy_repo_id,
        )

        def _done(code: int) -> None:
            if code == 0 and policy_repo_id:
                state.set_last_policy(policy_repo_id)
                state.complete_level("train")
            elif code == 0:
                state.complete_level("train")

        return {"session_id": _start_session("train", cmd, on_exit_extra=_done)}

    if action == "eval_record":
        cmd = commands.eval_record_cmd(
            state.found_ports.get("follower", commands.DEFAULT_FOLLOWER_PORT),
            p.get("cameras", []),
            p["repo_id"],
            p.get("task", ""),
            p["policy_path"],
            int(p.get("num_episodes", 10)),
        )
        return {
            "session_id": _start_session(
                "eval_record", cmd, on_exit_extra=lambda code: state.complete_level("evaluate") if code == 0 else None
            )
        }

    if action == "eval_sim":
        cmd = commands.eval_sim_cmd(
            p["policy_path"],
            p["env_type"],
            int(p.get("n_episodes", 50)),
            int(p.get("batch_size", 10)),
            p.get("device", "cuda"),
        )
        return {
            "session_id": _start_session(
                "eval_sim", cmd, on_exit_extra=lambda code: state.complete_level("evaluate") if code == 0 else None
            )
        }

    if action == "custom":
        cmd = p.get("cmd", "")
        if not cmd.strip():
            return {"error": "Empty command."}
        return {"session_id": _start_session("custom", cmd)}

    return {"error": f"Unknown action: {action}"}


@app.post("/api/session/{session_id}/stop")
def stop_session(session_id: str) -> dict:
    session = _sessions.get(session_id)
    if not session:
        return {"error": "No such session."}
    session.stop()
    return {"ok": True}


@app.post("/api/session/{session_id}/interrupt")
def interrupt_session(session_id: str) -> dict:
    session = _sessions.get(session_id)
    if not session:
        return {"error": "No such session."}
    session.interrupt()
    return {"ok": True}


@app.get("/api/hf_user")
def get_hf_user() -> dict:
    try:
        out = subprocess.run(
            ["hf", "auth", "whoami"],
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "NO_COLOR": "1"},
        )
        m = re.search(r"^([\w.\-]+)", out.stdout.strip())
        if out.returncode == 0 and m:
            state.set_hf_user(m.group(1))
            return {"user": m.group(1)}
        return {"user": None, "detail": out.stdout.strip() or out.stderr.strip()}
    except Exception as e:
        return {"user": None, "detail": str(e)}


# ── Install level: run all pending checks as one script ──────────

_INSTALL_MARKER_RE = re.compile(r"@@INSTALL:(\w+):(start|skip|exit):?(\d+)?@@")
# The marker token only (no trailing newline) -- stripped from what the browser's
# terminal renders so the user sees a readable log line ("Installing X...", "X --
# already installed") instead of our bookkeeping protocol, while `_INSTALL_MARKER_RE`
# still finds the token via `.search()` for state parsing since it's on the same line.
_INSTALL_MARKER_TOKEN_RE = re.compile(r"@@INSTALL:\w+:(?:start|skip|exit:\d+)@@ ?")


def _strip_install_markers(text: str) -> str:
    return _INSTALL_MARKER_TOKEN_RE.sub("", text)


def _build_install_script(steps: list[dict]) -> str:
    if commands.IS_WINDOWS:
        return _build_install_script_windows(steps)
    parts = []
    for step in steps:
        key = step["key"]
        label = step["label"]
        if step["already_installed"]:
            parts.append(f"echo '@@INSTALL:{key}:skip@@ {label} -- already installed'")
        else:
            parts.append(f"echo '@@INSTALL:{key}:start@@ Installing {label}...'")
            parts.append(f"{step['cmd']}")
            parts.append(
                f'code=$?; if [ "$code" = 0 ]; then '
                f'echo "@@INSTALL:{key}:exit:$code@@ {label} -- done"; else '
                f'echo "@@INSTALL:{key}:exit:$code@@ {label} -- failed (exit $code)"; fi'
            )
    return " ; ".join(parts) if parts else "echo 'Nothing to install -- everything is ready.'"


def _build_install_script_windows(steps: list[dict]) -> str:
    """PowerShell equivalent of `_build_install_script`.

    Joined with newlines (this becomes the body of a temp .ps1 file, see
    pty_session.py) rather than `;` since each step's own cmd may already be a
    multi-line block (winget install steps use `if (...) { ... }`).

    Each step's `cmd` (see commands.py) sets `$__stepOk` explicitly at its own real
    point of success/failure, rather than this reading $LASTEXITCODE afterwards --
    that variable is only set by *external* processes, not cmdlets (Invoke-WebRequest,
    Expand-Archive, etc used by the ffmpeg fallback), so a cmdlet-only step left it
    holding an unrelated *earlier* step's leftover value, silently misreporting
    success/failure by coincidence rather than by checking anything real.
    """
    lines = []
    for step in steps:
        key = step["key"]
        label = step["label"]
        if step["already_installed"]:
            lines.append(f"Write-Host '@@INSTALL:{key}:skip@@ {label} -- already installed'")
        else:
            lines.append(f"Write-Host '@@INSTALL:{key}:start@@ Installing {label}...'")
            lines.append("$__stepOk = $false")
            lines.append(step["cmd"])
            lines.append(
                f'if ($__stepOk) {{ Write-Host "@@INSTALL:{key}:exit:0@@ {label} -- done" }} '
                f'else {{ Write-Host "@@INSTALL:{key}:exit:1@@ {label} -- failed" }}'
            )
    return "\n".join(lines) if lines else "Write-Host 'Nothing to install -- everything is ready.'"


def _start_install_session() -> str:
    steps = commands.install_steps()
    for step in steps:
        state.install_step_status(step["key"], "checking")
    script = _build_install_script(steps)

    def on_line(line: str) -> None:
        m = _INSTALL_MARKER_RE.search(line)
        if not m:
            return
        key, kind, code = m.group(1), m.group(2), m.group(3)
        if kind == "skip":
            state.install_step_status(key, "already_installed")
        elif kind == "start":
            state.install_step_status(key, "installing")
        elif kind == "exit":
            state.install_step_status(key, "installed" if code == "0" else "failed")

    def on_exit(_code: int) -> None:
        fresh = commands.install_steps()
        if all(s["already_installed"] for s in fresh):
            state.complete_level("install")

    return _start_session(
        "install_run", script, on_line=on_line, on_exit_extra=on_exit, filter_output=_strip_install_markers
    )


# ── helpers used by find_port to persist the discovered port ─────

# Linux/macOS: /dev/ttyACM0-style paths. Windows: COM7-style names (confirmed against
# real lerobot-find-port output there -- "The port of this MotorsBus is 'COM7'"). The
# original only matched the POSIX form, so Find Ports silently never completed on
# Windows: the port printed correctly (that's lerobot-find-port's own output, not this
# app's), but nothing here ever recognized it to mark the level done.
_PORT_RE = re.compile(r"(/dev/tty[^\s'\"\\,\]]+|COM\d+)")


def _on_find_port_line(arm: str):
    seen_prompt = {"flag": False}

    def _handler(line: str) -> None:
        lower = line.lower()
        if not seen_prompt["flag"] and ("press enter" in lower or "remove" in lower):
            seen_prompt["flag"] = True
            return
        if seen_prompt["flag"] and not state.found_ports.get(arm):
            m = _PORT_RE.search(line)
            if m:
                state.set_port(arm, m.group(1))
                state.complete_level("find_ports", arm=arm)

    return _handler


# ── static frontend ───────────────────────────────────────────────
# Mounted last (and at the root) so it only catches requests that didn't match one of
# the API/WebSocket routes above -- Starlette tries routes in registration order, and
# a "/" mount would otherwise shadow everything if declared first. `html=True` serves
# `index.html` for `/` and unknown sub-paths, matching how Firebase Hosting would serve
# this same directory when the frontend is deployed separately from this API.
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
