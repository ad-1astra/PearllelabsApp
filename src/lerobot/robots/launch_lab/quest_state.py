"""Game state: XP, ranks, level completion, found ports, motor checklist.
Single-process, single-user -- no auth needed. Persisted to a small JSON file
under the user's home directory so quests already completed (install, find
ports, motor IDs, calibrate, ...) don't need to be redone every time the app
is restarted -- same file location logic on Windows and POSIX.

State mutations call `_emit()`, which the server wires up to broadcast JSON
over the `/ws/events` WebSocket. This is kept separate from the raw terminal
byte stream (`pty_session.py`) so the terminal widget only ever shows real
terminal output, never UI bookkeeping.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lerobot.robots.setup_quest_utils import build_quest_steps

# Path.home() resolves correctly on both Windows (C:\Users\<name>) and POSIX
# (/home/<name>) -- a dot-prefixed folder there is the standard convention many CLI
# tools use (.ssh, .aws, .docker, ...) for exactly this kind of small local state.
STATE_FILE = Path.home() / ".lerobot-launch-lab" / "state.json"
_PERSISTED_FIELDS = (
    "levels_done",
    "arm_levels_done",
    "found_ports",
    "motor_status",
    "hf_user",
    "last_dataset_repo_id",
    "last_policy_repo_id",
)

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

# Levels that are run once per arm (follower, then leader) -- the level itself should
# only count as done once BOTH arms are done, not after whichever one happens first.
# Teleoperate/Record/etc need both a follower and a leader port/calibration to work at
# all, so marking e.g. "Find Ports" complete after only the follower was found was
# actively misleading, not just an ordering nicety.
ARM_LEVELS = ("find_ports", "set_motor_ids", "calibrate")


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
        self.arm_levels_done = {level: {"follower": False, "leader": False} for level in ARM_LEVELS}
        self.found_ports = {"follower": "", "leader": ""}
        self.motor_status = dict.fromkeys(MOTOR_ORDER, "pending")
        self.hf_user: str | None = None
        self.last_dataset_repo_id: str | None = None
        self.last_policy_repo_id: str | None = None
        self.quest_steps = build_quest_steps()
        self._broadcast_cb: Callable[[dict], None] | None = None
        self.viewer_bridge = None  # optional ViewerBridge, wired up by main.py if available
        self._load()

    # ── wiring ────────────────────────────────────────────────
    def set_broadcast(self, cb: Callable[[dict], None]) -> None:
        self._broadcast_cb = cb

    def _emit(self, event: dict[str, Any]) -> None:
        if self._broadcast_cb:
            self._broadcast_cb(event)

    # ── persistence ──────────────────────────────────────────
    def _load(self) -> None:
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return
        # Merge rather than replace wholesale, and only accept known keys/levels/
        # motors -- keeps a state file from an older version of this app (different
        # LEVELS/MOTOR_ORDER) from crashing a newer one instead of just partially
        # applying.
        if isinstance(data.get("levels_done"), dict):
            self.levels_done.update({k: v for k, v in data["levels_done"].items() if k in self.levels_done})
        if isinstance(data.get("arm_levels_done"), dict):
            for level, arms in data["arm_levels_done"].items():
                if level in self.arm_levels_done and isinstance(arms, dict):
                    self.arm_levels_done[level].update({a: v for a, v in arms.items() if a in self.arm_levels_done[level]})
        if isinstance(data.get("found_ports"), dict):
            self.found_ports.update({k: v for k, v in data["found_ports"].items() if k in self.found_ports})
        if isinstance(data.get("motor_status"), dict):
            self.motor_status.update({k: v for k, v in data["motor_status"].items() if k in self.motor_status})
        if isinstance(data.get("hf_user"), (str, type(None))):
            self.hf_user = data["hf_user"]
        if isinstance(data.get("last_dataset_repo_id"), (str, type(None))):
            self.last_dataset_repo_id = data["last_dataset_repo_id"]
        if isinstance(data.get("last_policy_repo_id"), (str, type(None))):
            self.last_policy_repo_id = data["last_policy_repo_id"]

    def _save(self) -> None:
        payload = {field: getattr(self, field) for field in _PERSISTED_FIELDS}
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            # Write-then-rename: os.replace is atomic on both Windows and POSIX, so a
            # crash mid-write can never leave a half-written, unparseable state file
            # behind -- worst case the rename just doesn't happen and the old file
            # (or none) is read back next time.
            tmp = STATE_FILE.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp, STATE_FILE)
        except OSError:
            pass  # progress just won't survive a restart this time; never worth crashing over

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
            "arm_levels_done": {level: dict(arms) for level, arms in self.arm_levels_done.items()},
            "hf_user": self.hf_user,
            "last_dataset_repo_id": self.last_dataset_repo_id,
            "last_policy_repo_id": self.last_policy_repo_id,
        }

    def complete_level(self, level: str, arm: str | None = None) -> None:
        if level in ARM_LEVELS and arm is not None:
            with self._lock:
                self.arm_levels_done[level][arm] = True
                both_done = all(self.arm_levels_done[level].values())
            self._save()
            self._emit({"type": "arm_level_progress", "level": level, "arm": arm, "arm_levels_done": dict(self.arm_levels_done[level])})
            if not both_done:
                return  # e.g. follower's port just found -- still waiting on leader
        with self._lock:
            self.levels_done[level] = True
        self._save()
        self._emit({"type": "level_complete", "level": level, "arm": arm, "progress": self.progress()})
        if self.viewer_bridge:
            try:
                self.viewer_bridge.send_level_complete(level, "all", arm=arm)
            except Exception:
                pass

    # ── ports ─────────────────────────────────────────────────
    def set_port(self, arm: str, port: str) -> None:
        self.found_ports[arm] = port
        self._save()
        self._emit({"type": "port_found", "arm": arm, "port": port})

    # ── motor checklist (Set IDs level) ─────────────────────
    def reset_motor_checklist(self) -> None:
        self.motor_status = dict.fromkeys(MOTOR_ORDER, "pending")
        self._save()
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
            self._save()
            self._emit({"type": "motor_status", "motor": motor, "status": status})

    # ── install checklist ────────────────────────────────────
    def install_step_status(self, key: str, status: str) -> None:
        self._emit({"type": "install_step", "key": key, "status": status})

    # ── misc ──────────────────────────────────────────────────
    def set_hf_user(self, user: str) -> None:
        self.hf_user = user
        self._save()
        self._emit({"type": "hf_user", "user": user})

    def set_last_dataset(self, repo_id: str) -> None:
        self.last_dataset_repo_id = repo_id
        self._save()
        self._emit({"type": "last_dataset", "repo_id": repo_id})

    def set_last_policy(self, repo_id: str) -> None:
        self.last_policy_repo_id = repo_id
        self._save()
        self._emit({"type": "last_policy", "repo_id": repo_id})

    def error_tip(self, session_id: str, message: str) -> None:
        self._emit({"type": "error_tip", "session_id": session_id, "message": message})
