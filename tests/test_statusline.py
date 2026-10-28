import ast
import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from companion import server, statusline
from companion.server import CompanionServer, sanitize_status

SCRIPT = Path(statusline.__file__)
NOW = 1_761_600_000
ALLOWED_KEYS = set(server.STATUS_TEXT_FIELDS) | set(server.STATUS_NUMBER_FIELDS)
NEVER_FORWARDED = ("transcript_path", "cwd", "session_name", "workspace", "repo", "pr", "prompt_cache")
MAX_LINE = "Opus high | ctx 12% | 5h 23% 2h14m | 7d 41% 3d4h"
API_LINE = "Opus | ctx 12% | $0.42"


def max_payload(now: int = NOW) -> dict:
    """A payload shaped like the documented schema: a Max plan on a model with effort."""
    return {
        "cwd": "C:/dev/demo-app",
        "session_id": "abcdef1234567890",
        "session_name": "Fix the login bug",
        "prompt_id": "550e8400-e29b-41d4-a716-446655440000",
        "transcript_path": "C:/Users/me/.claude/projects/demo/abc.jsonl",
        "model": {"id": "claude-opus-5-5", "display_name": "Opus"},
        "workspace": {
            "current_dir": "C:/dev/demo-app",
            "project_dir": "C:/dev/demo-app",
            "added_dirs": ["C:/private/notes"],
            "git_worktree": "feature-xyz",
            "repo": {"host": "example.com", "owner": "example-org", "name": "example-repo"},
        },
        "version": "2.1.90",
        "output_style": {"name": "default"},
        "cost": {
            "total_cost_usd": 0.42,
            "total_duration_ms": 45000,
            "total_api_duration_ms": 2300,
            "total_lines_added": 156,
            "total_lines_removed": 23,
        },
        "context_window": {
            "total_input_tokens": 15500,
            "total_output_tokens": 1200,
            "context_window_size": 200000,
            "used_percentage": 12,
            "remaining_percentage": 88,
            "current_usage": {"input_tokens": 8500, "output_tokens": 1200},
        },
        "exceeds_200k_tokens": False,
        "prompt_cache": {"warm": True, "hit_ratio": 0.91, "expires_at": now + 3600},
        "fast_mode": False,
        "effort": {"level": "high"},
        "thinking": {"enabled": True},
        "rate_limits": {
            # 2h14m30s and 3d4h1m40s left: both countdowns round down.
            "five_hour": {"used_percentage": 23.2, "resets_at": now + 8070},
            "seven_day": {"used_percentage": 41.2, "resets_at": now + 273700},
            "spend_limit": {"used_percentage": 62.8, "resets_at": now + 999_999, "used_usd": 314.12},
        },
        "vim": {"mode": "NORMAL"},
        "agent": {"name": "security-reviewer"},
        "pr": {"number": 1234, "url": "https://example.com/pr/1234", "review_state": "pending"},
        "worktree": {"name": "my-feature", "path": "C:/dev/demo-app/.claude/worktrees/my-feature"},
    }


def api_payload() -> dict:
    """An API-key session: no rate_limits, and a model without the effort parameter."""
    data = max_payload()
    del data["rate_limits"], data["effort"]
    return data


def run(raw: bytes, environ: dict | None = None, now: float = NOW) -> tuple[int, bytes]:
    out = io.BytesIO()
    code = statusline.main(stdin=io.BytesIO(raw), stdout=out, environ=environ or {}, now=now)
    return code, out.getvalue()


def closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def start_daemon():
    servers = []

    def start(token: str | None = None) -> tuple[CompanionServer, list]:
        events: list = []
        srv = CompanionServer(sink=events.append, port=0, token=token)
        srv.start_thread()
        servers.append(srv)
        return srv, events

    yield start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def budget(monkeypatch):
    """The real budget, restarted as if the script had just begun (T0 is set at import)."""
    monkeypatch.setattr(statusline, "T0", time.monotonic())


@pytest.fixture
def roomy(monkeypatch):
    """A wide budget for delivery tests, so a loaded machine cannot turn them into timeouts."""
    monkeypatch.setattr(statusline, "T0", time.monotonic())
    monkeypatch.setattr(statusline, "DEADLINE_S", 5.0)
    monkeypatch.setattr(statusline, "CONNECT_TIMEOUT_S", 2.0)


@pytest.fixture
def posts(monkeypatch):
    calls: list = []
    monkeypatch.setattr(statusline, "post", lambda *args: calls.append(args) or True)
    return calls


# --- flatten -----------------------------------------------------------------------------


def test_flatten_max_payload_exact():
    assert statusline.flatten(max_payload()) == {
        "session_id": "abcdef1234567890",
        "project": "demo-app",
        "model": "Opus",
        "effort": "high",
        "context_pct": 12,
        "cost_usd": 0.42,
        "five_hour_pct": 23.2,
        "five_hour_resets_at": NOW + 8070,
        "seven_day_pct": 41.2,
        "seven_day_resets_at": NOW + 273700,
    }


def test_flatten_api_key_payload_has_no_quota_keys():
    assert statusline.flatten(api_payload()) == {
        "session_id": "abcdef1234567890",
        "project": "demo-app",
        "model": "Opus",
        "context_pct": 12,
        "cost_usd": 0.42,
    }


def test_flatten_early_payload_sends_null_context():
    data = max_payload()
    data["context_window"].update(used_percentage=None, remaining_percentage=None, current_usage=None)
    fields = statusline.flatten(data)
    assert "context_pct" in fields
    assert fields["context_pct"] is None


def test_flatten_post_compact_payload_sends_null_context():
    data = max_payload()
    data["context_window"] = {
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "context_window_size": 200000,
        "used_percentage": None,
        "remaining_percentage": None,
        "current_usage": None,
    }
    fields = statusline.flatten(data)
    assert "context_pct" in fields
    assert fields["context_pct"] is None


def test_flatten_omits_context_when_the_key_is_absent():
    data = max_payload()
    del data["context_window"]["used_percentage"]
    assert "context_pct" not in statusline.flatten(data)


def test_flatten_without_effort_object_has_no_effort_key():
    data = max_payload()
    del data["effort"]
    assert "effort" not in statusline.flatten(data)


def test_flatten_falls_back_to_model_id():
    data = max_payload()
    data["model"] = {"id": "claude-opus-5-5"}
    assert statusline.flatten(data)["model"] == "claude-opus-5-5"
    data["model"] = {"id": "claude-opus-5-5", "display_name": ""}
    assert statusline.flatten(data)["model"] == "claude-opus-5-5"


def test_flatten_accepts_a_plain_string_model():
    data = max_payload()
    data["model"] = "Sonnet"
    assert statusline.flatten(data)["model"] == "Sonnet"


@pytest.mark.parametrize(
    "current_dir",
    ["C:\\Users\\me\\dev\\demo-app", "C:\\Users\\me\\dev\\demo-app\\", "/home/me/demo-app/", "C:/dev/demo-app//"],
)
def test_flatten_project_is_the_basename_of_current_dir(current_dir):
    data = max_payload()
    data["workspace"]["current_dir"] = current_dir
    data["cwd"] = "C:/elsewhere/other"
    assert statusline.flatten(data)["project"] == "demo-app"


@pytest.mark.parametrize("workspace", [None, "C:/dev/demo-app", {"project_dir": "C:/x"}, {"current_dir": 5}])
def test_flatten_project_falls_back_to_cwd(workspace):
    data = max_payload()
    data["cwd"] = "C:\\dev\\from-cwd\\"
    if workspace is None:
        del data["workspace"]
    else:
        data["workspace"] = workspace
    assert statusline.flatten(data)["project"] == "from-cwd"


def test_flatten_project_matches_the_hook_basename():
    path = "C:/dev/" + "p" * 300 + "/"
    data = {"session_id": "s", "workspace": {"current_dir": path}}
    assert statusline.flatten(data)["project"] == server._basename(path)


def test_flatten_window_without_resets_at():
    data = max_payload()
    data["rate_limits"] = {"five_hour": {"used_percentage": 5}}
    fields = statusline.flatten(data)
    assert fields["five_hour_pct"] == 5
    assert not {"five_hour_resets_at", "seven_day_pct", "seven_day_resets_at"} & set(fields)


# --- rejected values ---------------------------------------------------------------------


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1e400", "-1e400", "1e13", "true", "false", '"12"'])
def test_flatten_drops_numbers_that_are_not_finite_or_real(literal):
    raw = (
        '{"session_id": "s", "context_window": {"used_percentage": %s}, "cost": {"total_cost_usd": %s},'
        ' "rate_limits": {"five_hour": {"used_percentage": %s, "resets_at": %s},'
        ' "seven_day": {"used_percentage": %s, "resets_at": %s}}}'
    ) % ((literal,) * 6)
    assert statusline.flatten(json.loads(raw)) == {"session_id": "s"}


def test_flatten_keeps_numbers_at_the_magnitude_limit():
    data = {"session_id": "s", "cost": {"total_cost_usd": 1e12}, "context_window": {"used_percentage": -1e12}}
    assert statusline.flatten(data) == {"session_id": "s", "cost_usd": 1e12, "context_pct": -1e12}


def test_flatten_drops_text_over_200_characters():
    data = {
        "session_id": "s" * 201,
        "model": {"display_name": "m" * 201},
        "effort": {"level": "e" * 201},
    }
    assert statusline.flatten(data) == {}
    data = {"session_id": "s" * 200, "model": {"display_name": "m" * 201, "id": "short"}}
    assert statusline.flatten(data) == {"session_id": "s" * 200, "model": "short"}


@pytest.mark.parametrize("value", [None, "", 5, True, ["s"], {"id": "s"}])
def test_flatten_requires_a_non_empty_string_session_id(value):
    assert "session_id" not in statusline.flatten({"session_id": value})


@pytest.mark.parametrize(
    "key, value",
    [
        ("context_window", [12]),
        ("context_window", 12),
        ("cost", "0.42"),
        ("cost", [0.42]),
        ("rate_limits", 5),
        ("rate_limits", {"five_hour": "23", "seven_day": [41]}),
        ("effort", ["high"]),
        ("effort", {"level": 3}),
        ("model", 5),
        ("model", ["Opus"]),
        ("workspace", ["C:/dev/x"]),
    ],
)
def test_flatten_ignores_sub_objects_of_the_wrong_type(key, value):
    data = {"session_id": "s", key: value}
    assert statusline.flatten(data) == {"session_id": "s"}


@pytest.mark.parametrize("data", [None, [], [1, 2], "text", 5, True])
def test_flatten_returns_none_for_non_objects(data):
    assert statusline.flatten(data) is None


# --- privacy -----------------------------------------------------------------------------


def test_flatten_keeps_only_allowlisted_keys():
    fields = statusline.flatten(max_payload())
    assert set(fields) <= ALLOWED_KEYS
    for name in NEVER_FORWARDED:
        assert name not in fields
    body = json.dumps(fields)
    for secret in (
        "Fix the login bug",
        "abc.jsonl",
        "C:/dev",
        "C:/private",
        "example-org",
        "example-repo",
        "feature-xyz",
        "my-feature",
        "security-reviewer",
        "example.com",
        "550e8400",
        "314.12",
    ):
        assert secret not in body


# --- render_line -------------------------------------------------------------------------


def test_render_line_max_example():
    assert statusline.render_line(statusline.flatten(max_payload()), None, NOW) == MAX_LINE


def test_render_line_api_key_example():
    assert statusline.render_line(statusline.flatten(api_payload()), None, NOW) == API_LINE


def test_render_line_hides_lapsed_windows_and_shows_cost():
    data = max_payload()
    data["rate_limits"]["five_hour"]["resets_at"] = NOW - 1
    data["rate_limits"]["seven_day"]["resets_at"] = NOW  # not later than now: lapsed
    assert statusline.render_line(statusline.flatten(data), None, NOW) == "Opus high | ctx 12% | $0.42"


def test_render_line_one_live_window_still_hides_cost():
    data = max_payload()
    data["rate_limits"]["five_hour"]["resets_at"] = NOW - 60
    assert statusline.render_line(statusline.flatten(data), None, NOW) == "Opus high | ctx 12% | 7d 41% 3d4h"


def test_render_line_window_without_reset_has_no_countdown():
    fields = {"model": "Opus", "five_hour_pct": 23.2, "cost_usd": 0.42}
    assert statusline.render_line(fields, None, NOW) == "Opus | 5h 23%"


def test_render_line_omits_null_context():
    fields = statusline.flatten(api_payload())
    fields["context_pct"] = None
    assert statusline.render_line(fields, None, NOW) == "Opus | $0.42"


def test_render_line_rounds_context():
    assert statusline.render_line({"context_pct": 41.6}, None, NOW) == "ctx 42%"


def test_render_line_effort_needs_a_model():
    assert statusline.render_line({"effort": "high", "context_pct": 3}, None, NOW) == "ctx 3%"


@pytest.mark.parametrize(
    "left, text",
    [(0.5, "<1m"), (59, "<1m"), (60, "1m"), (2700, "45m"), (3599, "59m"), (3600, "1h00m"),
     (8040, "2h14m"), (8099, "2h14m"), (86399, "23h59m"), (86400, "1d0h"), (273600, "3d4h")],
)
def test_render_line_countdown(left, text):
    fields = {"seven_day_pct": 41.2, "seven_day_resets_at": NOW + left}
    assert statusline.render_line(fields, None, NOW) == f"7d 41% {text}"


def test_render_line_cuts_to_columns_minus_one():
    fields = statusline.flatten(max_payload())
    assert statusline.render_line(fields, 20, NOW) == MAX_LINE[:19]
    assert statusline.render_line(fields, 2, NOW) == MAX_LINE[:1]


@pytest.mark.parametrize("columns", [None, 1, 0, -40])
def test_render_line_cuts_to_200_without_a_usable_width(columns):
    fields = {"model": "m" * 200, "effort": "high", "context_pct": 12}
    line = statusline.render_line(fields, columns, NOW)
    assert line == ("m" * 200)[:200]
    assert statusline.render_line({"model": "Opus"}, columns, NOW) == "Opus"


def test_render_line_keeps_printable_ascii_only():
    fields = {"model": "Op\x1b[31mus\u00e9\x07", "effort": "h\u4e2digh\x7f\x9b", "context_pct": 12}
    line = statusline.render_line(fields, None, NOW)
    assert line == "Op[31mus? h?igh? | ctx 12%"
    assert all(32 <= ord(c) <= 126 for c in line)


def test_render_line_empty_fields_give_an_empty_line():
    assert statusline.render_line({}, None, NOW) == ""
    assert statusline.render_line({"session_id": "s", "project": "demo-app"}, None, NOW) == ""


# --- main against a real server ----------------------------------------------------------


def test_main_delivers_the_flattened_snapshot(start_daemon, roomy):
    srv, events = start_daemon()
    data = max_payload()
    code, out = run(json.dumps(data).encode(), {"CC_COMPANION_PORT": str(srv.server_address[1])})
    assert code == 0
    assert out == (MAX_LINE + "\n").encode()
    assert len(events) == 1
    delivered = {key: value for key, value in events[0].items() if not key.startswith("_")}
    assert delivered == sanitize_status(statusline.flatten(data))
    assert "session_name" not in delivered and "transcript_path" not in delivered


def test_main_delivers_null_context(start_daemon, roomy):
    srv, events = start_daemon()
    data = max_payload()
    data["context_window"]["used_percentage"] = None
    run(json.dumps(data).encode(), {"CC_COMPANION_PORT": str(srv.server_address[1])})
    assert events[0]["context_pct"] is None


def test_main_sends_the_right_token(start_daemon, roomy):
    srv, events = start_daemon(token="s3cret")
    environ = {"CC_COMPANION_PORT": str(srv.server_address[1]), "CC_COMPANION_TOKEN": "s3cret"}
    code, out = run(json.dumps(max_payload()).encode(), environ)
    assert (code, out) == (0, (MAX_LINE + "\n").encode())
    assert len(events) == 1


@pytest.mark.parametrize("token", ["wrong", "", None])
def test_main_with_a_wrong_token_is_dropped_and_still_prints(start_daemon, roomy, token):
    srv, events = start_daemon(token="s3cret")
    environ = {"CC_COMPANION_PORT": str(srv.server_address[1])}
    if token is not None:
        environ["CC_COMPANION_TOKEN"] = token
    code, out = run(json.dumps(max_payload()).encode(), environ)
    assert (code, out) == (0, (MAX_LINE + "\n").encode())
    assert events == []  # the 204 came after the sink would have run


def test_post_reports_the_204(start_daemon):
    srv, events = start_daemon()
    body = json.dumps({"session_id": "s", "model": "Opus"}).encode()
    assert statusline.post(body, srv.server_address[1], None, time.monotonic() + 5)
    assert events[0]["model"] == "Opus"


def test_post_refuses_a_token_that_would_split_the_header(start_daemon):
    srv, events = start_daemon()
    assert not statusline.post(b"{}", srv.server_address[1], "a\r\nX: y", time.monotonic() + 5)
    assert not statusline.post(b"{}", srv.server_address[1], "caf\u20ac", time.monotonic() + 5)
    assert events == []


# --- failure paths -----------------------------------------------------------------------


def test_closed_port_exits_fast_and_still_prints(budget):
    started = time.monotonic()
    code, out = run(json.dumps(max_payload()).encode(), {"CC_COMPANION_PORT": str(closed_port())})
    assert time.monotonic() - started < 0.3
    assert (code, out) == (0, (MAX_LINE + "\n").encode())


def test_post_to_a_closed_port_is_false(budget):
    assert not statusline.post(b"{}", closed_port(), None, statusline.T0 + statusline.DEADLINE_S)


def test_listener_that_never_answers_exits_fast(budget):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)  # the kernel completes the handshake; nobody accepts or replies
        started = time.monotonic()
        code, out = run(json.dumps(max_payload()).encode(), {"CC_COMPANION_PORT": str(listener.getsockname()[1])})
        assert time.monotonic() - started < 0.3
    assert (code, out) == (0, (MAX_LINE + "\n").encode())


def test_listener_that_trickles_bytes_cannot_hold_the_line(budget):
    """A foreign listener sending one byte at a time never ends the reply loop by itself."""
    stop = threading.Event()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)

        def trickle():
            conn, _ = listener.accept()
            with conn:
                while not stop.wait(0.015):
                    try:
                        conn.sendall(b"x")
                    except OSError:
                        return

        thread = threading.Thread(target=trickle, daemon=True)
        thread.start()
        started = time.monotonic()
        delivered = statusline.post(b"{}", listener.getsockname()[1], None, statusline.T0 + statusline.DEADLINE_S)
        elapsed = time.monotonic() - started
        stop.set()
        thread.join(2)
    assert not delivered
    assert elapsed < 0.3


def test_spent_budget_still_finishes_fast(monkeypatch):
    monkeypatch.setattr(statusline, "T0", time.monotonic() - 10)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        started = time.monotonic()
        code, out = run(json.dumps(max_payload()).encode(), {"CC_COMPANION_PORT": str(listener.getsockname()[1])})
        assert time.monotonic() - started < 0.3
    assert (code, out) == (0, (MAX_LINE + "\n").encode())


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"not json",
        b"[1,2]",
        b'"text"',
        b"null",
        b"\xff\xfe\xfd",
        b"x" * (2 << 20),
        json.dumps({"session_id": "s", "model": "Opus", "pad": "x" * (2 << 20)}).encode(),
        b"[" * 100_000,
    ],
    ids=["empty", "not-json", "list", "string", "null", "bad-utf8", "2mib-junk", "2mib-object", "deep"],
)
def test_garbage_stdin_prints_nothing_and_never_posts(raw, posts):
    assert run(raw) == (0, b"")
    assert posts == []


def test_stdin_up_to_the_limit_is_read():
    head = json.dumps({"model": "Opus", "pad": ""}).encode()
    raw = head[:-2] + b"x" * (statusline.MAX_STDIN - len(head)) + head[-2:]
    assert len(raw) == statusline.MAX_STDIN
    assert run(raw) == (0, b"Opus\n")


def test_byte_order_mark_is_accepted():
    assert run(b"\xef\xbb\xbf" + json.dumps({"model": "Opus"}).encode()) == (0, b"Opus\n")


def test_dict_without_session_id_prints_but_never_posts(posts):
    data = api_payload()
    del data["session_id"]
    assert run(json.dumps(data).encode()) == (0, (API_LINE + "\n").encode())
    assert posts == []


def test_empty_line_prints_nothing_but_still_posts(posts):
    assert run(json.dumps({"session_id": "s", "cwd": "C:/dev/demo-app"}).encode()) == (0, b"")
    assert len(posts) == 1


def test_main_posts_to_the_configured_port_and_falls_back_on_bad_values(posts):
    raw = json.dumps({"session_id": "s"}).encode()
    for value, port in [("5555", 5555), ("junk", 47621), ("", 47621), ("0", 47621), ("70000", 47621)]:
        posts.clear()
        run(raw, {"CC_COMPANION_PORT": value})
        assert posts[0][1] == port
    posts.clear()
    run(raw)
    assert posts[0][1:3] == (47621, None)


@pytest.mark.parametrize(
    "columns, line",
    [("20", MAX_LINE[:19]), ("2", MAX_LINE[:1]), ("1", MAX_LINE), ("0", MAX_LINE), ("-5", MAX_LINE),
     ("abc", MAX_LINE), ("", MAX_LINE), ("1.5", MAX_LINE), ("500", MAX_LINE)],
)
def test_main_reads_columns(columns, line, posts):
    assert run(json.dumps(max_payload()).encode(), {"COLUMNS": columns}) == (0, (line + "\n").encode())


def test_main_survives_a_closed_stdout(posts):
    out = io.BytesIO()
    out.close()
    assert statusline.main(stdin=io.BytesIO(json.dumps(max_payload()).encode()), stdout=out, environ={}) == 0


# --- the installed command form ----------------------------------------------------------


def script_env(port: int) -> dict:
    env = {key: value for key, value in os.environ.items() if key != "CC_COMPANION_TOKEN"}
    env.update(CC_COMPANION_PORT=str(port), COLUMNS="120")
    return env


@pytest.mark.parametrize("daemon_up", [True, False], ids=["daemon-up", "daemon-down"])
def test_subprocess_run(daemon_up, start_daemon):
    events: list = []
    if daemon_up:
        srv, events = start_daemon()
        port = srv.server_address[1]
    else:
        port = closed_port()
    now = int(time.time())
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-I", "-S", str(SCRIPT)],
        input=json.dumps(max_payload(now)).encode(),
        capture_output=True,
        env=script_env(port),
        timeout=10,
    )
    elapsed = time.monotonic() - started
    assert proc.returncode == 0
    assert proc.stderr == b""
    assert proc.stdout == (MAX_LINE + "\n").encode()
    assert proc.stdout.decode("ascii")
    assert elapsed < 1.0
    assert len(events) == (1 if daemon_up else 0)


def test_subprocess_garbage_exits_zero_silently():
    proc = subprocess.run(
        [sys.executable, "-I", "-S", str(SCRIPT)],
        input=b"\x00not json",
        capture_output=True,
        env=script_env(closed_port()),
        timeout=10,
    )
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, b"", b"")


# --- constants and imports ---------------------------------------------------------------


def test_constants_match_the_server():
    assert statusline.DEFAULT_PORT == server.DEFAULT_PORT
    assert statusline.MAX_TEXT == server.MAX_TEXT
    assert statusline.TOKEN_HEADER == "X-Companion-Token"
    assert (statusline.CONNECT_TIMEOUT_S, statusline.DEADLINE_S) == (0.05, 0.15)
    assert (statusline.MAX_STDIN, statusline.MAX_NUMBER) == (1 << 20, 1e12)


def test_imports_only_the_standard_library_it_needs():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert imported <= {"__future__", "json", "os", "socket", "sys", "time"}
