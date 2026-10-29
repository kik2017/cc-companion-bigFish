"""POST /say: the persona's voice. Token, browser checks, persona switch, sanitizing, rate limit."""

import http.client
import json
import logging
import queue

import pytest

from companion import settings as prefs
from companion.model import MOODS
from companion.server import (
    SAY_DEFAULT_TTL_S,
    SAY_INTERVAL_S,
    SAY_MAX_CHARS,
    SAY_TOKEN_HEADER,
    SAY_TTL_S,
    REPLY_WINDOW_S,
    CompanionServer,
    sanitize_say,
)

TOKEN = "test-say-token"


@pytest.fixture
def server():
    lines: queue.Queue = queue.Queue()
    hooks: queue.Queue = queue.Queue()
    clock = [100.0]
    srv = CompanionServer(
        sink=hooks.put, port=0, say_token=TOKEN, say_sink=lines.put, say_enabled=lambda: srv.enabled, clock=lambda: clock[0]
    )
    srv.enabled = True
    srv.lines, srv.hooks, srv.now = lines, hooks, clock
    srv.start_thread()
    yield srv
    srv.shutdown()
    srv.server_close()


def say(srv, body, headers=None, token=TOKEN):
    sent = {"Content-Type": "application/json"}
    if token is not None:
        sent[SAY_TOKEN_HEADER] = token
    sent.update(headers or {})
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=2)
    conn.request("POST", "/say", body=body if isinstance(body, bytes) else json.dumps(body).encode(), headers=sent)
    response = conn.getresponse()
    payload = response.read()
    conn.close()
    return response.status, payload


def test_a_valid_line_is_shown_and_queued_sanitized(server):
    assert say(server, {"text": "  早呀\n今天也一起加油  ", "mood": "happy", "ttl_s": 6}) == (204, b"")
    assert server.lines.get(timeout=1) == {"text": "早呀 今天也一起加油", "mood": "happy", "ttl_s": 6.0, "reply": False, "cue": None}
    assert server.hooks.empty()  # a line never reaches the hook sink


@pytest.mark.parametrize(
    "token, headers",
    [
        (None, {}),
        ("wrong", {}),
        ("", {}),
        ("tést", {}),  # non-ASCII must not crash the compare
        (TOKEN, {"Origin": "https://example.com"}),
        (TOKEN, {"Sec-Fetch-Site": "cross-site"}),
        (TOKEN, {"Content-Type": "text/plain"}),
    ],
)
def test_a_missing_wrong_or_browser_request_is_refused(server, token, headers):
    status, payload = say(server, {"text": "hi"}, headers=headers, token=token)
    assert (status, payload) == (403, b"")
    assert server.lines.empty()


def test_no_token_configured_refuses_everything():
    srv = CompanionServer(sink=lambda e: None, port=0, say_enabled=lambda: True)
    srv.start_thread()
    try:
        assert say(srv, {"text": "hi"}, token="")[0] == 403
        assert say(srv, {"text": "hi"}, token="anything")[0] == 403
    finally:
        srv.shutdown()
        srv.server_close()


def test_persona_off_answers_409(server):
    server.enabled = False
    assert say(server, {"text": "hi"})[0] == 409
    assert server.lines.empty()


@pytest.mark.parametrize("body", [b"", b"not json", [], {}, {"text": ""}, {"text": "   "}, {"mood": "angry"}, {"text": "x", "mood": "angry"}])
def test_an_invalid_line_answers_400(server, body):
    assert say(server, body)[0] == 400
    assert server.lines.empty()


def test_a_mood_with_no_words_is_accepted(server):
    assert say(server, {"mood": "happy"})[0] == 204
    assert server.lines.get(timeout=1) == {"text": None, "mood": "happy", "ttl_s": SAY_DEFAULT_TTL_S, "reply": False, "cue": None}


def test_a_null_text_is_not_a_mood_only_line(server):
    assert say(server, {"text": None, "mood": "happy"})[0] == 400


def test_a_second_line_inside_the_interval_answers_429(server):
    assert say(server, {"text": "one"})[0] == 204
    server.now[0] += SAY_INTERVAL_S - 0.5
    assert say(server, {"text": "two"})[0] == 429
    server.now[0] += 0.5
    assert say(server, {"text": "three"})[0] == 204
    assert [server.lines.get(timeout=1)["text"] for _ in range(2)] == ["one", "three"]


def test_a_granted_reply_passes_the_interval_once(server):
    assert say(server, {"text": "a line"})[0] == 204
    server.grant_reply()
    server.now[0] += 1
    assert say(server, {"text": "not a reply"})[0] == 429, "the credit is only for a reply"
    assert say(server, {"text": "her answer", "reply": True, "cue": None})[0] == 204
    assert say(server, {"text": "another", "reply": True, "cue": None})[0] == 429, "one reply per grant"
    assert [server.lines.get(timeout=1) for _ in range(2)][1] == {"text": "her answer", "mood": "calm", "ttl_s": SAY_DEFAULT_TTL_S, "reply": True, "cue": None}


def test_a_reply_without_a_grant_or_after_it_expires_waits_its_turn(server):
    assert say(server, {"text": "a line"})[0] == 204
    assert say(server, {"text": "unasked", "reply": True, "cue": None})[0] == 429
    server.grant_reply()
    server.now[0] += REPLY_WINDOW_S + 1
    assert say(server, {"text": "a later line"})[0] == 204
    server.now[0] += 1
    assert say(server, {"text": "too late", "reply": True, "cue": None})[0] == 429, "the grant has expired"


def test_no_pause_lets_lines_through_back_to_back_until_restored(server):
    server.set_say_interval(0)
    assert [say(server, {"text": f"line {n}"})[0] for n in range(3)] == [204, 204, 204]
    server.set_say_interval(SAY_INTERVAL_S)
    assert say(server, {"text": "spaced again"})[0] == 429


def test_reply_must_be_exactly_true():
    assert sanitize_say({"text": "hi", "reply": "yes"})["reply"] is False
    assert sanitize_say({"text": "hi", "reply": 1})["reply"] is False


def test_a_refused_line_does_not_use_up_the_slot(server):
    assert say(server, {"text": "x", "mood": "angry"})[0] == 400
    assert say(server, {"text": "ok"})[0] == 204


def test_the_log_carries_length_and_mood_never_the_text(server, caplog):
    caplog.set_level(logging.INFO, logger="companion")
    say(server, {"text": "a secret line", "mood": "proud"})
    server.lines.get(timeout=1)
    say(server, {"text": "another secret"})  # rate-limited
    messages = [r.getMessage() for r in caplog.records]
    assert "say len=13 mood=proud" in messages
    assert "say refused (429)" in messages
    assert not any("secret" in m for m in messages)


# --- sanitize_say ------------------------------------------------------------------------


def test_defaults_for_mood_and_lifetime():
    assert sanitize_say({"text": "hi"}) == {"text": "hi", "mood": MOODS[0], "ttl_s": SAY_DEFAULT_TTL_S, "reply": False, "cue": None}


@pytest.mark.parametrize("ttl, expected", [(0, SAY_TTL_S[0]), (-5, SAY_TTL_S[0]), (9.5, 9.5), (600, SAY_TTL_S[1])])
def test_lifetime_is_clamped(ttl, expected):
    assert sanitize_say({"text": "hi", "ttl_s": ttl})["ttl_s"] == expected


@pytest.mark.parametrize("ttl", ["8", None, True, float("nan"), float("inf")])
def test_a_lifetime_that_is_not_a_finite_number_is_rejected(ttl):
    assert sanitize_say({"text": "hi", "ttl_s": ttl}) is None


def test_line_breaks_and_unprintable_characters_go():
    text = "a\r\nb\tc​d‮e\x00f\x1b[31m"
    assert sanitize_say({"text": text})["text"] == "a b cdef[31m"


def test_length_is_capped_after_cleanup():
    assert sanitize_say({"text": "x" * SAY_MAX_CHARS})["text"] == "x" * SAY_MAX_CHARS
    assert sanitize_say({"text": "x" * (SAY_MAX_CHARS + 1)}) is None
    assert sanitize_say({"text": " " * 300 + "ok" + "\n" * 100})["text"] == "ok"


@pytest.mark.parametrize("payload", [None, "hi", 3, {"text": 3}, {"text": ["hi"]}, {"text": "hi", "mood": 1}])
def test_bad_shapes_are_rejected(payload):
    assert sanitize_say(payload) is None


def test_every_mood_is_accepted():
    for mood in MOODS:
        assert sanitize_say({"text": "hi", "mood": mood})["mood"] == mood


# --- the token file ----------------------------------------------------------------------


def test_the_token_file_is_replaced_whole(isolated_app_dir):
    prefs.write_say_token("first")
    prefs.write_say_token("second")
    assert prefs.say_token_path().read_text(encoding="utf-8") == "second"
    assert prefs.say_token_path().parent == isolated_app_dir
    assert not list(isolated_app_dir.glob("*.tmp"))


@pytest.mark.parametrize("cue, kept", [("greet", "greet"), ("sleep", "sleep"), ("dance", None), (1, None), (None, None)])
def test_a_cue_is_kept_only_if_known_and_never_refuses_the_line(cue, kept):
    speech = sanitize_say({"text": "hi", "cue": cue})
    assert speech is not None and speech["cue"] == kept and speech["text"] == "hi"


def test_a_cue_rides_with_a_mood_alone(server):
    assert say(server, {"mood": "sleepy", "cue": "sleep"})[0] == 204
    assert server.lines.get(timeout=1)["cue"] == "sleep"
