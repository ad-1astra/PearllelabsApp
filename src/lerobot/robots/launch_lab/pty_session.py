"""A single real pseudo-terminal running one shell command.

Streams raw bytes both ways so the browser-side xterm.js terminal renders
exactly what a real terminal would (colors, cursor movement, progress bars)
and so keystrokes typed in the browser -- including arrow keys and Ctrl-C --
reach the child process exactly as they would in a real terminal. This is
what lets interactive flows like `lerobot-record` (arrow-key episode
controls) and `lerobot-calibrate` (Enter-driven prompts) work from the app.

Two backends, chosen at import time by platform:
  - POSIX (Linux/macOS): `ptyprocess`, wrapping a real POSIX pty via
    fcntl/pty/termios. Spawns `bash -lc <cmd>` directly.
  - Windows: `pywinpty`, wrapping ConPTY (Windows' native pseudo-console API,
    added in Windows 10 1809+). `ptyprocess` cannot be imported on Windows at
    all -- it hard-imports fcntl/pty/termios/resource, none of which exist
    outside POSIX -- so the import itself is platform-gated below, not just
    the usage.

The Windows path is written from documented pywinpty behavior but has not
been run on an actual Windows machine (none available in this environment);
treat it as needing real verification before relying on it.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import threading
import uuid
from collections.abc import Callable
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import winpty  # pywinpty; only installed on Windows (see pyproject.toml)
else:
    import ptyprocess

_ANSI_RE = re.compile(rb"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]|\r")


def strip_ansi(data: bytes) -> str:
    """Best-effort strip of ANSI escape sequences, for line-based detectors only.

    The raw bytes (with escapes intact) are always what gets sent to the
    browser; this stripped text is only used server-side to feed the
    existing regex-based game-state detectors (motor prompts, error
    keywords) without needing them to understand terminal control codes.
    """
    return _ANSI_RE.sub(b"", data).decode("utf-8", errors="replace")


class PtySession:
    def __init__(
        self,
        session_id: str,
        cmd: str,
        cwd: str,
        on_output: Callable[[bytes], None],
        on_line: Callable[[str], None] | None = None,
        on_exit: Callable[[int], None] | None = None,
        rows: int = 30,
        cols: int = 100,
    ):
        self.session_id = session_id
        self.cmd = cmd
        self.cwd = cwd
        self.on_output = on_output
        self.on_line = on_line
        self.on_exit = on_exit
        self._proc = None
        self._reader_thread: threading.Thread | None = None
        self._line_buf = ""
        self._stopped = False
        self._script_path: Path | None = None

    def start(self) -> None:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["TERM"] = "xterm-256color"
        if IS_WINDOWS:
            # A temp .ps1 file sidesteps Windows/PowerShell command-line quoting
            # entirely -- `self.cmd` can contain arbitrary quotes (e.g. the embedded
            # YAML-ish --robot.cameras="{...}" dict) without needing to be re-escaped
            # for a second layer of shell parsing.
            self._script_path = Path(tempfile.gettempdir()) / f"launch_lab_{uuid.uuid4().hex[:8]}.ps1"
            self._script_path.write_text(self.cmd, encoding="utf-8")
            self._proc = winpty.PtyProcess.spawn(
                f'powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "{self._script_path}"',
                cwd=self.cwd,
                env=env,
                dimensions=(30, 100),
            )
        else:
            self._proc = ptyprocess.PtyProcess.spawn(
                ["bash", "-lc", self.cmd],
                cwd=self.cwd,
                env=env,
                dimensions=(30, 100),
            )
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def _read_loop(self) -> None:
        assert self._proc is not None
        exit_code = 1
        try:
            while True:
                try:
                    data = self._proc.read(4096)
                except EOFError:
                    break
                if not data:
                    break
                if isinstance(data, str):
                    data = data.encode("utf-8", errors="replace")
                self.on_output(data)
                if self.on_line:
                    self._feed_lines(data)
        finally:
            try:
                self._proc.wait()
                exit_code = self._proc.exitstatus or 0
            except Exception:
                exit_code = 1
            self._cleanup_script()
            if self.on_exit and not self._stopped:
                self.on_exit(exit_code)

    def _cleanup_script(self) -> None:
        if self._script_path is not None:
            try:
                self._script_path.unlink(missing_ok=True)
            except Exception:
                pass

    def _feed_lines(self, data: bytes) -> None:
        self._line_buf += strip_ansi(data)
        while "\n" in self._line_buf:
            line, self._line_buf = self._line_buf.split("\n", 1)
            line = line.strip()
            if line:
                self.on_line(line)

    def write(self, data: bytes) -> None:
        if self._proc is not None and self._proc.isalive():
            # pywinpty's PTY is text-mode (ConPTY deals in text, not raw bytes);
            # ptyprocess's is byte-mode. The public interface here stays bytes-in
            # either way since that's what the browser's WebSocket payloads are.
            self._proc.write(data.decode("utf-8", errors="replace") if IS_WINDOWS else data)

    def resize(self, rows: int, cols: int) -> None:
        if self._proc is not None and self._proc.isalive():
            try:
                self._proc.setwinsize(rows, cols)
            except Exception:
                pass

    def interrupt(self) -> None:
        """Send Ctrl-C (SIGINT) to the foreground process, like a real terminal."""
        if self._proc is not None and self._proc.isalive():
            try:
                if IS_WINDOWS:
                    # ConPTY has no sendintr() equivalent exposed by pywinpty; writing
                    # the raw ETX byte is what a real terminal sends on Ctrl-C, and
                    # ConPTY translates it into the appropriate console control event.
                    self._proc.write("\x03")
                else:
                    self._proc.sendintr()
            except Exception:
                pass

    def stop(self) -> None:
        """Force-stop the session (used when the user taps Stop or closes it)."""
        self._stopped = True
        if self._proc is not None and self._proc.isalive():
            try:
                self._proc.terminate(force=True)
            except TypeError:
                self._proc.terminate()
            except Exception:
                pass
        self._cleanup_script()

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.isalive()
