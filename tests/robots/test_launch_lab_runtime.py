import os

from lerobot.robots.launch_lab import main as launch_lab_main
from lerobot.robots.launch_lab import server as launch_lab_server


def test_runtime_defaults_come_from_environment(monkeypatch):
    monkeypatch.delenv("LAUNCH_LAB_HOST", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.delenv("LAUNCH_LAB_PORT", raising=False)

    host, port = launch_lab_main.resolve_host_and_port()

    assert host == "0.0.0.0"
    assert port == 8090


def test_runtime_prefers_explicit_environment_values(monkeypatch):
    monkeypatch.setenv("LAUNCH_LAB_HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "9090")

    host, port = launch_lab_main.resolve_host_and_port()

    assert host == "127.0.0.1"
    assert port == 9090


def test_remote_helper_mode_uses_helper_session(monkeypatch):
    monkeypatch.setattr(launch_lab_server, "_REMOTE_HELPER_ENABLED", True)
    monkeypatch.setattr(launch_lab_server, "_SESSION_HELPER_URL", "http://helper.local")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"session_id": "abc123"}'

    def fake_urlopen(req, timeout):
        assert req.full_url == "http://helper.local/api/run"
        return FakeResponse()

    monkeypatch.setattr(launch_lab_server.urllib_request, "urlopen", fake_urlopen)

    session_id = launch_lab_server._start_session("demo", "echo hi")

    assert session_id == "abc123"
    assert launch_lab_server._session_transport[session_id] == "remote"
    assert session_id not in launch_lab_server._sessions
