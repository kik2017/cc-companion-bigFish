import http.client
import json
import logging
import math
import queue
import threading
import time

import pytest

from companion.reducer import STATUS_EVENT
from companion.server import CompanionServer, _number, sanitize_hook, sanitize_status

SENSITIVE = {
    "tool_input": {"command": "rm -rf secrets", "content": "file body"},
    "tool_response": {"stdout": "private output"},
    "prompt": "my private prompt",
    "last_assistant_message": "Claude's reply",
    "transcript_path": "C:/Users/me/.claude/projects/x/abc.jsonl",
    "custom_instructions": "focus on the auth module",
    "compact_summary": "summary of the conversation",
    "additionalContext": "injected context",
    "message": "Claude needs your permission to use Bash",
    "title": "Permission needed",
    "tool_calls": [{"tool_name": "Bash", "tool_input": {"command": "ls"}}],
    "agent_transcript_path": "C:/Users/me/sub.jsonl",
    "error_details": "stack trace",
    "permission_suggestions": [{"type": "addRules"}],
    "session_title": "Fix the login bug",
    "prompt_id": "p-1",
    "scratchpad_dir": "C:/tmp/scratch",
}


@pytest.fixture
def server():
    events: queue.Queue = queue.Queue()
    srv = CompanionServer(sink=events.put, port=0)
    srv.start_thread()
    srv.events = events
    yield srv
    srv.shutdown()
    srv.server_close()


def request(srv, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=2)
    raw = body if isinstance(body, bytes) or body is None else json.dumps(body).encode()
    conn.request(method, path, body=raw, headers=headers or {"Content-Type": "application/json"})
    response = conn.getresponse()
    payload = response.read()
    conn.close()
    return response, payload


def hook(name, **fields):
    return {"hook_event_name": name, "session_id": "abcdef1234567890", "cwd": "C:/dev/demo-project", **fields}


def queued(srv):
    """The next queued event without its receipt stamp, which must be a perf_counter float."""
    event = srv.events.get(timeout=1)
    received = event.pop("_rx")
    assert isinstance(received, float)
    assert received <= time.perf_counter()
    return event


# --- HTTP behaviour ------------------------------------------------------------------


def test_hook_returns_204_with_empty_body_and_queues_event(server):
    response, payload = request(server, "POST", "/hook", hook("PreToolUse", tool_name="Bash"))
    assert response.status == 204
    assert payload == b""
    assert response.getheader("Content-Length") is None
    event = queued(server)
    assert event == {
        "hook_event_name": "PreToolUse",
        "session_id": "abcdef1234567890",
        "tool_name": "Bash",
        "project": "demo-project",
    }


def test_permission_request_never_gets_a_body(server):
    response, payload = request(server, "POST", "/hook", hook("PermissionRequest", tool_name="Bash", **SENSITIVE))
    assert (response.status, payload) == (204, b"")
    assert server.events.get(timeout=1)["hook_event_name"] == "PermissionRequest"


@pytest.mark.parametrize("body", [b"{not json", b"[1, 2, 3]", b'"text"', b"", b'{"session_id": "x"}'])
def test_bad_bodies_get_204_and_queue_nothing(server, body):
    response, payload = request(server, "POST", "/hook", body)
    assert (response.status, payload) == (204, b"")
    assert server.events.empty()


BROWSER = {"Content-Type": "text/plain;charset=UTF-8", "Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}


@pytest.mark.parametrize("route", ["/hook", "/status"])
@pytest.mark.parametrize(
    "headers",
    [
        BROWSER,
        {"Content-Type": "text/plain;charset=UTF-8"},
        {"Content-Type": "application/json", "Origin": "https://evil.example"},
        {"Content-Type": "application/json", "Origin": "null"},
        {"Content-Type": "application/json", "Sec-Fetch-Site": "cross-site"},
        {"Content-Type": "application/x-www-form-urlencoded"},
        {},
    ],
)
def test_browser_style_posts_get_204_and_queue_nothing(server, route, headers):
    """A page can POST a CORS simple request to loopback; only Claude Code's JSON requests count."""
    body = hook("Notification", notification_type="permission_prompt", model="Visit evil.example now")
    raw = json.dumps(body).encode()
    response, payload = request(server, "POST", route, raw, headers={**headers, "Content-Length": str(len(raw))})
    assert (response.status, payload) == (204, b"")
    assert request(server, "POST", "/hook", hook("Stop"))[0].status == 204  # a real request after it
    assert queued(server)["hook_event_name"] == "Stop"
    assert server.events.empty()


@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Type": "application/json"},
        {"Content-Type": "application/json; charset=utf-8"},
        {"Content-Type": "Application/JSON"},
        {"Content-Type": "application/json", "Sec-Fetch-Site": "none", "Sec-Fetch-Mode": "cors"},
    ],
)
def test_json_posts_without_origin_still_reach_the_sink(server, headers):
    raw = json.dumps(hook("Stop")).encode()
    response, _ = request(server, "POST", "/hook", raw, headers=headers)
    assert response.status == 204
    assert queued(server)["hook_event_name"] == "Stop"


def test_status_route_queues_sanitized_status(server):
    response, _ = request(server, "POST", "/status", {"session_id": "s1", "five_hour_pct": 23.0, "transcript_path": "x"})
    assert response.status == 204
    assert queued(server) == {"hook_event_name": STATUS_EVENT, "session_id": "s1", "five_hour_pct": 23.0}


def test_status_route_basenames_project_and_drops_session_name(server):
    body = {"session_id": "s1", "project": "C:\\dev\\demo-project\\", "session_name": "Fix the login bug"}
    request(server, "POST", "/status", body)
    assert queued(server) == {"hook_event_name": STATUS_EVENT, "session_id": "s1", "project": "demo-project"}


def test_status_route_drops_non_finite_numbers_from_a_raw_body(server):
    raw = b'{"session_id": "s1", "cost_usd": NaN, "five_hour_pct": Infinity, "seven_day_pct": -Infinity,'
    raw += b' "context_pct": 1e400, "five_hour_resets_at": 1761600000}'
    response, payload = request(server, "POST", "/status", raw)
    assert (response.status, payload) == (204, b"")
    assert queued(server) == {"hook_event_name": STATUS_EVENT, "session_id": "s1", "five_hour_resets_at": 1761600000}


def test_hook_route_drops_non_finite_scalars_from_a_raw_body(server):
    raw = b'{"hook_event_name": "PostToolUseFailure", "session_id": "s1", "tool_name": "Bash", "is_interrupt": NaN}'
    request(server, "POST", "/hook", raw)
    assert queued(server) == {"hook_event_name": "PostToolUseFailure", "session_id": "s1", "tool_name": "Bash"}


def test_receipt_stamp_is_taken_at_receipt(server):
    before = time.perf_counter()
    request(server, "POST", "/hook", hook("Stop"))
    after = time.perf_counter()
    event = server.events.get(timeout=1)
    assert before <= event["_rx"] <= after


def test_health_reports_app_and_session_count(server):
    server.session_count = 2
    response, payload = request(server, "GET", "/health")
    assert response.status == 200
    assert json.loads(payload) == {
        "ok": True,
        "app": "cc-companion",
        "sessions": 2,
        "hook_p99_ms": None,
        "paint_n": 0,
        "paint_p50_ms": None,
        "paint_p99_ms": None,
        "paint_max_ms": None,
    }


def test_health_reports_paint_stats_after_record_paint(server):
    for ms in range(1, 101):
        server.record_paint(ms / 10)
    request(server, "POST", "/hook", hook("Stop"))
    deadline = time.monotonic() + 2
    while server.p99_ms() is None and time.monotonic() < deadline:  # noted just after the reply
        time.sleep(0.01)
    health = json.loads(request(server, "GET", "/health")[1])
    assert (health["paint_n"], health["paint_p50_ms"], health["paint_p99_ms"], health["paint_max_ms"]) == (
        100, 5.1, 10.0, 10.0,
    )  # fmt: skip
    assert health["hook_p99_ms"] == server.p99_ms() > 0
    assert server.paint_stats() == {"n": 100, "p50_ms": 5.1, "p99_ms": 10.0, "max_ms": 10.0}


def test_paint_stats_keep_the_last_1000(server):
    for ms in range(1500):
        server.record_paint(float(ms))
    assert server.paint_stats() == {"n": 1000, "p50_ms": 1000.0, "p99_ms": 1490.0, "max_ms": 1499.0}


def test_stats_survive_concurrent_writers_and_readers(server):
    """Paint lags, hook latencies and /health touch the same stats from different threads."""
    errors: list[BaseException] = []
    painting_done = threading.Event()
    reader_started = threading.Event()

    def catching(work):
        def body():
            try:
                work()
            except BaseException as exc:  # reported by the assert below
                errors.append(exc)

        return threading.Thread(target=body)

    def paint():
        reader_started.wait(2)
        try:
            for i in range(20_000):
                server.record_paint(i % 97 / 10)
        finally:
            painting_done.set()

    def read():
        reads = 0
        while not painting_done.is_set() or reads < 5:
            assert request(server, "GET", "/health")[0].status == 200
            server.p99_ms()
            server.paint_stats()
            reads += 1
            reader_started.set()

    def post():
        while not painting_done.is_set():
            request(server, "POST", "/hook", hook("PreToolUse", tool_name="Read"))

    threads = [catching(work) for work in (read, post, paint)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert not errors
    assert not any(thread.is_alive() for thread in threads)
    assert server.paint_stats()["n"] == 1000


def test_log_lines_never_carry_the_receipt_stamp(server, caplog):
    def logged():
        return [r for r in caplog.records if "session=" in r.getMessage()]

    with caplog.at_level(logging.INFO, logger="companion"):
        request(server, "POST", "/hook", hook("PreToolUse", tool_name="Bash"))
        request(server, "POST", "/status", {"session_id": "s1", "five_hour_pct": 3.0})
        deadline = time.monotonic() + 2
        while len(logged()) < 2 and time.monotonic() < deadline:  # written just after the reply
            time.sleep(0.01)
    messages = sorted(r.getMessage() for r in logged())
    assert [m.rsplit(" ", 1)[0] for m in messages] == [
        "PreToolUse tool=Bash session=abcdef12",
        "StatusLine tool=- session=s1",
    ]
    assert not any("_rx" in m for m in messages)


# --- allowlist -----------------------------------------------------------------------

ALL_EVENTS = [
    "SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure",
    "PostToolBatch", "PermissionRequest", "PermissionDenied", "Notification", "SubagentStart", "SubagentStop",
    "TaskCreated", "TaskCompleted", "Stop", "StopFailure", "PreCompact", "PostCompact", "PostModelSwitch",
    "CwdChanged",
]  # fmt: skip


@pytest.mark.parametrize("name", ALL_EVENTS)
def test_sensitive_fields_never_survive(name):
    event = sanitize_hook(hook(name, error="Command failed: cat ~/.ssh/id_rsa", **SENSITIVE))
    for key in SENSITIVE:
        assert key not in event
    assert "error" not in event
    assert "cwd" not in event
    flat = json.dumps(event)
    for secret in ("rm -rf", "private", "Claude's reply", "auth module", "id_rsa", ".jsonl", "login bug"):
        assert secret not in flat


@pytest.mark.parametrize(
    "name, fields, kept",
    [
        ("SessionStart", {"source": "resume", "model": "claude-opus-5-5"}, {"source": "resume", "model": "claude-opus-5-5"}),
        ("SessionEnd", {"reason": "logout"}, {"reason": "logout"}),
        ("PostToolUseFailure", {"tool_name": "Bash", "is_interrupt": True}, {"tool_name": "Bash", "is_interrupt": True}),
        ("PermissionDenied", {"tool_name": "Bash", "reason": "user said no"}, {"tool_name": "Bash"}),
        ("Notification", {"notification_type": "permission_prompt"}, {"notification_type": "permission_prompt"}),
        ("SubagentStart", {"agent_id": "a1", "agent_type": "Explore"}, {"agent_id": "a1", "agent_type": "Explore"}),
        ("TaskCompleted", {"task_id": "7", "task_subject": "Write tests"}, {"task_id": "7"}),
        ("PreCompact", {"trigger": "auto"}, {"trigger": "auto"}),
        ("PostModelSwitch", {"from_model": "sonnet", "to_model": "opus", "pricing": {"in": 1}}, {"from_model": "sonnet", "to_model": "opus"}),
        ("StopFailure", {"error": "rate_limit"}, {"error_type": "rate_limit"}),
        ("StopFailure", {"error": "Something went badly wrong"}, {}),
        ("UserPromptSubmit", {"permission_mode": "plan"}, {"permission_mode": "plan"}),
    ],
)
def test_allowlisted_fields_are_kept(name, fields, kept):
    event = sanitize_hook(hook(name, **fields))
    expected = {"hook_event_name": name, "session_id": "abcdef1234567890", "project": "demo-project", **kept}
    assert event == expected


@pytest.mark.parametrize("name", ["PreCompact", "PostCompact", "PreToolUse", "PostToolBatch", "Stop"])
def test_an_agent_id_is_kept_and_raises_the_subagent_flag(name):
    event = sanitize_hook(hook(name, agent_id="agent-abc123", agent_type="Explore"))
    assert (event["agent_id"], event["subagent"]) == ("agent-abc123", True)
    assert "agent_type" not in event  # kept only on the subagent lifecycle events


@pytest.mark.parametrize("agent_id", ["", 5, True, {"id": "agent-abc123"}, ["agent-abc123"], "a" * 201])
def test_no_subagent_flag_without_a_short_string_agent_id(agent_id):
    event = sanitize_hook(hook("PreCompact", trigger="auto", agent_id=agent_id))
    assert "subagent" not in event and "agent_id" not in event
    assert "subagent" not in sanitize_hook(hook("PreCompact", trigger="auto"))


@pytest.mark.parametrize("name", ["SubagentStart", "SubagentStop"])
def test_subagent_lifecycle_events_keep_their_own_id_and_get_no_flag(name):
    event = sanitize_hook(hook(name, agent_id="a1", agent_type="Explore"))
    assert (event["agent_id"], event["agent_type"]) == ("a1", "Explore")
    assert "subagent" not in event


def test_log_line_marks_subagent_events_without_their_id(server, caplog):
    def logged():
        return [r.getMessage() for r in caplog.records if "session=" in r.getMessage()]

    with caplog.at_level(logging.INFO, logger="companion"):
        request(server, "POST", "/hook", hook("PreCompact", trigger="auto", agent_id="agent-abc123"))
        deadline = time.monotonic() + 2
        while not logged() and time.monotonic() < deadline:  # written just after the reply
            time.sleep(0.01)
    (message,) = logged()
    assert message.startswith("PreCompact tool=- session=abcdef12 ")
    assert message.endswith("ms subagent")
    assert "agent-abc123" not in message
    assert queued(server)["subagent"] is True


@pytest.mark.parametrize(
    "cwd, project",
    [("C:/dev/demo-project", "demo-project"), ("C:\\dev\\demo-project\\", "demo-project"), ("/home/me/app", "app"), ("", None)],
)
def test_project_is_cwd_basename(cwd, project):
    assert sanitize_hook(hook("Stop", cwd=cwd)).get("project") == project


def test_effort_object_and_model_object_are_flattened():
    event = sanitize_hook(hook("PreToolUse", effort={"level": "xhigh"}, model={"id": "claude-x", "display_name": "Opus"}))
    assert (event["effort"], event["model"]) == ("xhigh", "Opus")


def test_non_scalar_and_long_values_are_dropped():
    event = sanitize_hook(hook("PreToolUse", tool_name={"nested": "x"}, permission_mode="m" * 500))
    assert "tool_name" not in event and "permission_mode" not in event


@pytest.mark.parametrize("name", ["PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest", "PermissionDenied"])
@pytest.mark.parametrize("tool", [5, 1.5, True])
def test_non_string_tool_name_is_dropped(name, tool):
    assert "tool_name" not in sanitize_hook(hook(name, tool_name=tool))


def test_hook_without_session_id_is_rejected():
    assert sanitize_hook({"hook_event_name": "Stop"}) is None
    assert sanitize_hook({"hook_event_name": "Stop", "session_id": ""}) is None


def test_status_keeps_only_known_fields_with_right_types():
    event = sanitize_status(
        {
            "session_id": "s1",
            "model": "Opus",
            "context_pct": None,
            "cost_usd": 0.42,
            "five_hour_pct": "23",
            "seven_day_pct": True,
            "five_hour_resets_at": 1761600000,
            "transcript_path": "x",
            "project": "demo-project",
        }
    )
    assert event == {
        "hook_event_name": STATUS_EVENT,
        "session_id": "s1",
        "model": "Opus",
        "project": "demo-project",
        "context_pct": None,
        "cost_usd": 0.42,
        "five_hour_resets_at": 1761600000,
    }


def test_status_without_session_id_is_rejected():
    assert sanitize_status({"model": "Opus"}) is None


@pytest.mark.parametrize(
    "value, kept",
    [
        (math.nan, None),
        (math.inf, None),
        (-math.inf, None),
        (1e13, None),
        (-1e13, None),
        (10**400, None),
        (True, None),
        (False, None),
        ("23", None),
        (None, None),
        ([1], None),
        (0, 0),
        (23.5, 23.5),
        (-4.0, -4.0),
        (1761600000, 1761600000),
        (1e12, 1e12),
    ],
)
def test_number_keeps_only_finite_bounded_numbers(value, kept):
    assert _number(value) == kept
    if kept is not None:
        assert type(_number(value)) is type(value)


def test_status_drops_non_finite_and_huge_numbers_but_keeps_null():
    event = sanitize_status(
        {
            "session_id": "s1",
            "context_pct": None,
            "cost_usd": math.nan,
            "five_hour_pct": math.inf,
            "seven_day_pct": 1e13,
            "five_hour_resets_at": 0,
            "seven_day_resets_at": 1762000000.5,
        }
    )
    assert event == {
        "hook_event_name": STATUS_EVENT,
        "session_id": "s1",
        "context_pct": None,
        "five_hour_resets_at": 0,
        "seven_day_resets_at": 1762000000.5,
    }


@pytest.mark.parametrize(
    "project, kept",
    [
        ("demo-project", "demo-project"),
        ("C:/dev/demo-project/", "demo-project"),
        ("C:\\dev\\app", "app"),
        ("/", None),
        ("", None),
        (7, None),
    ],
)
def test_status_project_is_a_basename(project, kept):
    assert sanitize_status({"session_id": "s1", "project": project}).get("project") == kept


def test_status_never_keeps_session_name():
    event = sanitize_status({"session_id": "s1", "session_name": "Fix the login bug", "model": "Opus"})
    assert event == {"hook_event_name": STATUS_EVENT, "session_id": "s1", "model": "Opus"}


def test_hook_scalars_reject_non_finite_floats():
    event = sanitize_hook(hook("PostToolUseFailure", tool_name="Bash", is_interrupt=math.inf))
    assert "is_interrupt" not in event
