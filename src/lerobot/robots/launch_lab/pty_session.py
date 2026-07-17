"""A single real pseudo-terminal running one shell command.

Streams raw bytes both ways so the browser-side xterm.js terminal renders
exactly what a real terminal would (colors, cursor movement, progress bars)
and so keystrokes typed in the browser -- including arrow keys and Ctrl-C --
reach the child process exactly as they would in a real terminal. This is
what lets interactive flows like `lerobot-record` (arrow-key episode
controls) and `lerobot-calibrate` (Enter-driven prompts) work from the app.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable

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
        self._proc: ptyprocess.PtyProcess | None = None
        self._reader_thread: threading.Thread | None = None
        self._line_buf = ""
        self._stopped = False

    def start(self) -> None:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["TERM"] = "xterm-256color"
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
            if self.on_exit and not self._stopped:
                self.on_exit(exit_code)

    def _feed_lines(self, data: bytes) -> None:
        self._line_buf += strip_ansi(data)
        while "\n" in self._line_buf:
            line, self._line_buf = self._line_buf.split("\n", 1)
            line = line.strip()
            if line:
                self.on_line(line)

    def write(self, data: bytes) -> None:
        if self._proc is not None and self._proc.isalive():
            self._proc.write(data)

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
                self._proc.sendintr()
            except Exception:
                pass

    def stop(self) -> None:
        """Force-stop the session (used when the user taps Stop or closes it)."""
        self._stopped = True
        if self._proc is not None and self._proc.isalive():
            try:
                self._proc.terminate(force=True)
            except Exception:
                pass

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.isalive()
