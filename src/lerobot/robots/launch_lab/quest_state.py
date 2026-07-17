"""In-memory game state: XP, ranks, level completion, found ports, motor
checklist. Single-process, single-user -- no persistence, no auth needed.

State mutations call `_emit()`, which the server wires up to broadcast JSON
over the `/ws/events` WebSocket. This is kept separate from the raw terminal
byte stream (`pty_session.py`) so the terminal widget only ever shows real
terminal output, never UI bookkeeping.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from typing import Any

from lerobot.robots.setup_quest_utils import build_quest_steps

LEVELS = [
    "install",
    "find_ports",
    "set_motor_ids",
    "calibrate",
    "teleoperate",
    "record",
    "train",
    "evaluate",
]

LEVEL_TITLES = {
    "install": "Install",
    "find_ports": "Find Ports",
    "set_motor_ids": "Set IDs",
    "calibrate": "Calibrate",
    "teleoperate": "Teleoperate",
    "record": "Record",
    "train": "Train",
    "evaluate": "Evaluate",
}

RANKS = [
    (0, "Rookie Roboticist"),
    (25, "Apprentice Roboticist"),
    (50, "Journeyman Roboticist"),
    (75, "Senior Roboticist"),
    (100, "Master Roboticist"),
]

MOTOR_ORDER = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]

# Matched against lerobot-setup-motors' real stdout (so_follower.py / so_leader.py setup_motors()).
_MOTOR_PROMPT_RE = re.compile(r"Connect the controller board to the '(\w+)' motor only")
_MOTOR_DONE_RE = re.compile(r"'(\w+)' motor id set to (\d+)")


def rank_for_xp(xp: int) -> str:
    rank = RANKS[0][1]
    for threshold, name in RANKS:
        if xp >= threshold:
            rank = name
    return rank


class AppState:
    def __init__(self):
        self._lock = threading.Lock()
        self.levels_done = dict.fromkeys(LEVELS, False)
        self.found_ports = {"follower": "", "leader": ""}
        self.motor_status = dict.fromkeys(MOTOR_ORDER, "pending")
        self.hf_user: str | None = None
        self.last_dataset_repo_id: str | None = None
        self.last_policy_repo_id: str | None = None
        self.quest_steps = build_quest_steps()
        self._broadcast_cb: Callable[[dict], None] | None = None
        self.viewer_bridge = None  # optional ViewerBridge, wired up by main.py if available

    # ── wiring ────────────────────────────────────────────────
    def set_broadcast(self, cb: Callable[[dict], None]) -> None:
        self._broadcast_cb = cb

    def _emit(self, event: dict[str, Any]) -> None:
        if self._broadcast_cb:
            self._broadcast_cb(event)

    # ── progress / XP ────────────────────────────────────────
    def progress(self) -> dict:
        done = sum(self.levels_done.values())
        total = len(LEVELS)
        xp = int(done / total * 100)
        return {
            "done": done,
            "total": total,
            "xp": xp,
            "rank": rank_for_xp(xp),
            "levels_done": dict(self.levels_done),
        }

    def snapshot(self) -> dict:
        """Full state snapshot sent to a freshly-connected client."""
        return {
            "type": "snapshot",
            "progress": self.progress(),
            "found_ports": dict(self.found_ports),
            "motor_status": dict(self.motor_status),
            "hf_user": self.hf_user,
            "last_dataset_repo_id": self.last_dataset_repo_id,
            "last_policy_repo_id": self.last_policy_repo_id,
        }

    def complete_level(self, level: str, arm: str | None = None) -> None:
        with self._lock:
            self.levels_done[level] = True
        self._emit({"type": "level_complete", "level": level, "arm": arm, "progress": self.progress()})
        if self.viewer_bridge:
            try:
                self.viewer_bridge.send_level_complete(level, "all", arm=arm)
            except Exception:
                pass

    # ── ports ─────────────────────────────────────────────────
    def set_port(self, arm: str, port: str) -> None:
        self.found_ports[arm] = port
        self._emit({"type": "port_found", "arm": arm, "port": port})

    # ── motor checklist (Set IDs level) ─────────────────────
    def reset_motor_checklist(self) -> None:
        self.motor_status = dict.fromkeys(MOTOR_ORDER, "pending")
        self._emit({"type": "motor_reset", "motor_status": dict(self.motor_status)})

    def on_setup_motor_line(self, line: str) -> None:
        m = _MOTOR_PROMPT_RE.search(line)
        if m:
            self._set_motor_status(m.group(1), "active")
            return
        m = _MOTOR_DONE_RE.search(line)
        if m:
            self._set_motor_status(m.group(1), "done")

    def _set_motor_status(self, motor: str, status: str) -> None:
        if motor in self.motor_status:
            self.motor_status[motor] = status
            self._emit({"type": "motor_status", "motor": motor, "status": status})

    # ── install checklist ────────────────────────────────────
    def install_step_status(self, key: str, status: str) -> None:
        self._emit({"type": "install_step", "key": key, "status": status})

    # ── misc ──────────────────────────────────────────────────
    def set_hf_user(self, user: str) -> None:
        self.hf_user = user
        self._emit({"type": "hf_user", "user": user})

    def set_last_dataset(self, repo_id: str) -> None:
        self.last_dataset_repo_id = repo_id
        self._emit({"type": "last_dataset", "repo_id": repo_id})

    def set_last_policy(self, repo_id: str) -> None:
        self.last_policy_repo_id = repo_id
        self._emit({"type": "last_policy", "repo_id": repo_id})

    def error_tip(self, session_id: str, message: str) -> None:
        self._emit({"type": "error_tip", "session_id": session_id, "message": message})
