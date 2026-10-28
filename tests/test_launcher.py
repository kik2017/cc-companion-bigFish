import io
import queue
import re
import subprocess
import sys
import threading
import time

import pytest

from companion import launcher
from companion.server import CompanionServer


def free_port() -> int:
    srv = CompanionServer(sink=lambda e: None, port=0)
    port = srv.server_address[1]
    srv.server_close()
    return port


@pytest.fixture
def running():
    events: queue.Queue = queue.Queue()
    srv = CompanionServer(sink=events.put, port=0)
    srv.start_thread()
    yield srv, events
    srv.shutdown()
    srv.server_close()


def test_healthy_true_for_companion(running):
    srv, _ = running
    assert launcher.healthy(srv.server_address[1])


def test_healthy_false_when_nothing_listens():
    assert not launcher.healthy(free_port())


def test_forward_delivers_body(running):
    srv, events = running
    body = b'{"hook_event_name": "SessionStart", "session_id": "s1", "source": "startup", "cwd": "C:/x/app"}'
    assert launcher.forward(body, srv.server_address[1], time.monotonic() + 1)
    assert events.get(timeout=1)["source"] == "startup"


def test_forward_retries_until_daemon_comes_up():
    port = free_port()
    events: queue.Queue = queue.Queue()
    started = {}

    def late_start():
        time.sleep(0.4)
        started["srv"] = CompanionServer(sink=events.put, port=port)
        started["srv"].start_thread()

    threading.Thread(target=late_start).start()
    try:
        ok = launcher.forward(b'{"hook_event_name": "SessionStart", "session_id": "s1"}', port, time.monotonic() + 3)
        assert ok
        assert events.get(timeout=1)["session_id"] == "s1"
    finally:
        time.sleep(0.5)
        if "srv" in started:
            started["srv"].shutdown()
            started["srv"].server_close()


def test_forward_gives_up_at_deadline():
    started = time.monotonic()
    assert not launcher.forward(b"{}", free_port(), started + 0.3)
    assert time.monotonic() - started < 1.5


def test_main_exits_0_and_prints_nothing_when_daemon_cannot_start(monkeypatch, capsys):
    monkeypatch.setenv("CC_COMPANION_PORT", str(free_port()))
    monkeypatch.setattr(launcher, "FORWARD_DEADLINE_S", 0.3)
    monkeypatch.setattr(launcher, "spawn", lambda: None)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b'{"hook_event_name": "SessionStart"}')))
    assert launcher.main([]) == 0
    assert capsys.readouterr().out == ""


def test_main_exits_0_even_if_spawn_raises(monkeypatch):
    def boom():
        raise RuntimeError("no python")

    monkeypatch.setenv("CC_COMPANION_PORT", str(free_port()))
    monkeypatch.setattr(launcher, "spawn", boom)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"garbage")))
    assert launcher.main([]) == 0


def test_main_skips_spawn_when_daemon_is_up(running, monkeypatch):
    srv, events = running
    monkeypatch.setenv("CC_COMPANION_PORT", str(srv.server_address[1]))
    monkeypatch.setattr(launcher, "spawn", lambda: pytest.fail("spawned although healthy"))
    body = b'{"hook_event_name": "SessionStart", "session_id": "s1", "source": "resume"}'
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(body)))
    assert launcher.main([]) == 0
    assert events.get(timeout=1)["source"] == "resume"


def test_revive_does_not_forward_when_daemon_is_up(running, monkeypatch):
    srv, events = running
    monkeypatch.setenv("CC_COMPANION_PORT", str(srv.server_address[1]))
    monkeypatch.setattr(launcher, "spawn", lambda: pytest.fail("spawned although healthy"))
    body = b'{"hook_event_name": "UserPromptSubmit", "session_id": "s1"}'
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(body)))
    assert launcher.main(["--revive"]) == 0
    time.sleep(0.2)
    assert events.empty(), "the HTTP hook already delivered this event"


def test_revive_restarts_a_dead_daemon_and_forwards(monkeypatch):
    port = free_port()
    events: queue.Queue = queue.Queue()
    started = []

    def fake_spawn():
        srv = CompanionServer(sink=events.put, port=port)
        srv.start_thread()
        started.append(srv)

    monkeypatch.setenv("CC_COMPANION_PORT", str(port))
    monkeypatch.setattr(launcher, "spawn", fake_spawn)
    body = b'{"hook_event_name": "UserPromptSubmit", "session_id": "s1"}'
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(body)))
    try:
        assert launcher.main(["--revive"]) == 0
        assert len(started) == 1
        assert events.get(timeout=1)["hook_event_name"] == "UserPromptSubmit"
    finally:
        for srv in started:
            srv.shutdown()
            srv.server_close()


SPAWN_LINE = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} INFO launcher spawn after 123 ms$")


def test_note_spawn_writes_one_line_in_the_log_format():
    launcher.note_spawn(123)
    lines = launcher.prefs.log_path().read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert SPAWN_LINE.match(lines[0]), lines[0]


def test_note_spawn_appends_after_existing_log_lines():
    log = launcher.prefs.log_path()
    log.write_text("2026-01-01 00:00:00,000 INFO daemon stopped\n", encoding="utf-8")
    launcher.note_spawn(123)
    lines = log.read_text(encoding="utf-8").splitlines()
    assert lines[0].endswith("daemon stopped")
    assert SPAWN_LINE.match(lines[1])


def test_note_spawn_swallows_write_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher.prefs, "log_path", lambda: tmp_path)  # a directory cannot be opened
    launcher.note_spawn(123)


def test_main_notes_the_spawn_before_spawning(monkeypatch):
    calls = []
    monkeypatch.setenv("CC_COMPANION_PORT", str(free_port()))
    monkeypatch.setattr(launcher, "FORWARD_DEADLINE_S", 0.3)
    monkeypatch.setattr(launcher, "note_spawn", lambda after_ms: calls.append(("note", after_ms)))
    monkeypatch.setattr(launcher, "spawn", lambda: calls.append(("spawn",)))
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b'{"hook_event_name": "SessionStart"}')))
    assert launcher.main([]) == 0
    assert [call[0] for call in calls] == ["note", "spawn"]
    after_ms = calls[0][1]
    assert isinstance(after_ms, int) and after_ms >= 0


def test_main_on_a_dead_port_logs_the_spawn(monkeypatch):
    monkeypatch.setenv("CC_COMPANION_PORT", str(free_port()))
    monkeypatch.setattr(launcher, "FORWARD_DEADLINE_S", 0.3)
    monkeypatch.setattr(launcher, "spawn", lambda: None)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"")))
    assert launcher.main([]) == 0
    (line,) = launcher.prefs.log_path().read_text(encoding="utf-8").splitlines()
    assert re.fullmatch(r".* INFO launcher spawn after \d+ ms", line)


def test_main_does_not_note_a_spawn_when_daemon_is_up(running, monkeypatch):
    srv, events = running
    monkeypatch.setenv("CC_COMPANION_PORT", str(srv.server_address[1]))
    monkeypatch.setattr(launcher, "note_spawn", lambda after_ms: pytest.fail("noted although healthy"))
    monkeypatch.setattr(launcher, "spawn", lambda: pytest.fail("spawned although healthy"))
    body = b'{"hook_event_name": "SessionStart", "session_id": "s1", "source": "startup"}'
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(body)))
    assert launcher.main([]) == 0
    assert events.get(timeout=1)["session_id"] == "s1"


def test_spawn_is_detached_with_output_to_log(monkeypatch, isolated_app_dir):
    calls = []
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda cmd, **kw: calls.append((cmd, kw)))
    launcher.spawn()
    (cmd, kw), = calls
    assert cmd[-2:] == ["-m", "companion"]
    assert kw["stdin"] is subprocess.DEVNULL
    assert kw["stdout"] is kw["stderr"]
    assert kw["stdout"].name == str(launcher.prefs.log_path())
    if sys.platform == "win32":
        assert cmd[0].lower().endswith("pythonw.exe")
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        assert kw["creationflags"] & flags == flags
    else:
        assert kw["start_new_session"] is True
