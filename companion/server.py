"""Loopback HTTP server: POST /hook, POST /status, POST /say, GET /health. No Qt imports.

Every hook and status POST answers 204 with an empty body, which Claude Code reads as
success with no decision. Payloads are cut down to an allowlist before they reach the sink, so prompt
text, tool input and output, transcripts and any field added later are never kept.
A POST that is not application/json, or that carries an Origin or a cross-site
Sec-Fetch-Site header, is dropped the same way, so a web page cannot feed the bubble.

POST /say is the persona's voice: {"text", "mood", "ttl_s"}, or {"mood"} alone for a mood
with no words, from a local process that
holds the per-run say token (the daemon writes it to the app data folder). It answers
with an empty body and a status: 204 shown, 400 not a valid line, 403 no or wrong token
or browser-like, 409 the persona is off, 429 too soon after the last line. The line is
cut to one clean line before it reaches the say sink, and its text is never logged.
A body with "reply": true answers the chat box: grant_reply() (called when the daemon
passes a chat to the mind) lets one such line through within REPLY_WINDOW_S however
soon after the last line it comes. set_say_interval(0) lifts the interval altogether
(the persona's "No pause between lines").

Each queued event also carries `_rx`, the perf_counter at receipt, so the controller can
time receipt to repaint; it is never logged. /health reports hook and paint timings.
"""

from __future__ import annotations

import collections
import hmac
import json
import logging
import math
import os
import re
import threading
import time
import unicodedata
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .model import MOODS
from .reducer import STATUS_EVENT

HOST = "127.0.0.1"
DEFAULT_PORT = 47621
MAX_BODY = 16 * 1024 * 1024
MAX_TEXT = 200
MAX_NUMBER = 1e12
APP_ID = "cc-companion"

COMMON_FIELDS = ("hook_event_name", "session_id", "permission_mode")
EVENT_FIELDS: dict[str, tuple[str, ...]] = {
    "SessionStart": ("source",),
    "SessionEnd": ("reason",),
    "PreToolUse": ("tool_name",),
    "PostToolUse": ("tool_name",),
    "PostToolUseFailure": ("tool_name", "is_interrupt"),
    "PermissionRequest": ("tool_name",),
    "PermissionDenied": ("tool_name",),
    "Notification": ("notification_type",),
    "SubagentStart": ("agent_id", "agent_type"),
    "SubagentStop": ("agent_id", "agent_type"),
    "TaskCreated": ("task_id",),
    "TaskCompleted": ("task_id",),
    "PreCompact": ("trigger",),
    "PostCompact": ("trigger",),
    "PostModelSwitch": ("from_model", "to_model"),
}
SUBAGENT_LIFECYCLE = frozenset({"SubagentStart", "SubagentStop"})
# StopFailure's `error` is an error type such as rate_limit; PostToolUseFailure's `error`
# is free text and is dropped. Only token-shaped values survive the rename.
TOKEN = re.compile(r"[a-z0-9_]{1,64}")

STATUS_TEXT_FIELDS = ("session_id", "project", "model", "effort")
STATUS_NUMBER_FIELDS = (
    "context_pct",
    "cost_usd",
    "five_hour_pct",
    "five_hour_resets_at",
    "seven_day_pct",
    "seven_day_resets_at",
)

SAY_MAX_CHARS = 120
SAY_TTL_S = (4.0, 15.0)
SAY_DEFAULT_TTL_S = 8.0
# A backstop for a runaway caller; the persona's own speech budget is far stricter.
SAY_INTERVAL_S = 20.0
REPLY_WINDOW_S = 60.0
SAY_TOKEN_HEADER = "X-Companion-Say-Token"
# Control, format (bidi overrides, zero-width), surrogate, private-use and unassigned characters.
UNPRINTABLE = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})

log = logging.getLogger("companion")

Sink = Callable[[dict], None]


def port_from_env() -> int:
    try:
        return int(os.environ.get("CC_COMPANION_PORT", DEFAULT_PORT))
    except ValueError:
        return DEFAULT_PORT


def _scalar(value: object) -> object | None:
    if isinstance(value, float) and not math.isfinite(value):  # json.loads reads NaN, Infinity, 1e400
        return None
    if isinstance(value, bool | int | float):
        return value
    if isinstance(value, str) and 0 < len(value) <= MAX_TEXT:
        return value
    return None


def _number(value: object) -> float | int | None:
    """An int or float (not a bool) that is finite and no larger than MAX_NUMBER, else None."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value if abs(value) <= MAX_NUMBER else None


def _basename(path: object) -> str | None:
    if not isinstance(path, str):
        return None
    name = re.split(r"[\\/]", path.rstrip("\\/"))[-1]
    return name[:MAX_TEXT] or None


def _label(value: object, *keys: str) -> str | None:
    """A short string, either given directly or as the first of `keys` in an object."""
    if isinstance(value, dict):
        value = next((value[k] for k in keys if isinstance(value.get(k), str) and value[k]), None)
    text = _scalar(value)
    return text if isinstance(text, str) else None


def sanitize_hook(payload: object) -> dict | None:
    if not isinstance(payload, dict):
        return None
    name, session_id = payload.get("hook_event_name"), payload.get("session_id")
    if not isinstance(name, str) or not isinstance(session_id, str) or not session_id:
        return None
    event: dict = {}
    for key in COMMON_FIELDS + EVENT_FIELDS.get(name, ()):
        value = _scalar(payload.get(key))
        if value is not None:
            event[key] = value
    if not isinstance(event.get("tool_name"), str):  # the reducer matches it against patterns
        event.pop("tool_name", None)
    if name == "StopFailure" and isinstance(payload.get("error"), str) and TOKEN.fullmatch(payload["error"]):
        event["error_type"] = payload["error"]
    extras = {
        "project": _basename(payload.get("cwd")),
        "effort": _label(payload.get("effort"), "level"),
        "model": _label(payload.get("model"), "display_name", "id"),
    }
    event.update({k: v for k, v in extras.items() if v is not None})
    # agent_id is an opaque id for the subagent a hook fired in (no content). Outside the
    # subagent lifecycle events, where it names the subagent being tracked, it also raises the
    # `subagent` flag: the reducer then shows the parent as delegating instead of taking on
    # the subagent's verb, and matches a subagent's prompt to the subagent that raised it.
    agent_id = payload.get("agent_id")
    if isinstance(agent_id, str) and 0 < len(agent_id) <= MAX_TEXT:
        event["agent_id"] = agent_id
        if name not in SUBAGENT_LIFECYCLE:
            event["subagent"] = True
    return event


def sanitize_status(payload: object) -> dict | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("session_id"), str):
        return None
    event: dict = {"hook_event_name": STATUS_EVENT}
    for key in STATUS_TEXT_FIELDS:
        value = _basename(payload.get(key)) if key == "project" else _scalar(payload.get(key))
        if isinstance(value, str):
            event[key] = value
    for key in STATUS_NUMBER_FIELDS:
        if key not in payload:
            continue
        # An explicit null is kept: a null context_pct clears the ring after /compact.
        value = payload[key]
        number = _number(value)
        if value is None or number is not None:
            event[key] = number
    return event


def sanitize_say(payload: object) -> dict | None:
    """A /say body as {"text", "mood", "ttl_s", "reply"}: one line of printable text, a known mood, a clamped lifetime.

    Whitespace runs, line breaks included, become one space and unprintable characters
    are dropped. None if the text is then empty or longer than SAY_MAX_CHARS, the mood is
    unknown, or ttl_s is given but is not a finite number. A body with a mood and no
    "text" key is a mood with no words: text None. "reply" is True only if the body says
    exactly true.
    """
    if not isinstance(payload, dict):
        return None
    text = payload.get("text")
    if "text" not in payload and "mood" in payload:
        text = None
    elif not isinstance(text, str) or len(text) > 4 * SAY_MAX_CHARS:
        return None
    else:
        spaced = (" " if ch.isspace() else ch for ch in text)
        text = " ".join("".join(ch for ch in spaced if unicodedata.category(ch) not in UNPRINTABLE).split())
        if not text or len(text) > SAY_MAX_CHARS:
            return None
    mood = payload.get("mood", MOODS[0])
    if mood not in MOODS:
        return None
    ttl = _number(payload.get("ttl_s", SAY_DEFAULT_TTL_S))
    if ttl is None:
        return None
    ttl_s = float(min(max(ttl, SAY_TTL_S[0]), SAY_TTL_S[1]))
    return {"text": text, "mood": mood, "ttl_s": ttl_s, "reply": payload.get("reply") is True}


def _same(given: str, expected: str) -> bool:
    """Constant-time compare; as bytes, since compare_digest raises on non-ASCII str."""
    return hmac.compare_digest(given.encode("utf-8", "surrogateescape"), expected.encode("utf-8"))


def _percentile(ordered: list[float], fraction: float) -> float:
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


class CompanionServer(ThreadingHTTPServer):
    daemon_threads = True
    # Fail on a taken port instead of sharing it; on Windows SO_REUSEADDR would let a
    # second daemon bind the same port silently.
    allow_reuse_address = False

    def __init__(
        self,
        sink: Sink,
        port: int = DEFAULT_PORT,
        token: str | None = None,
        *,
        say_token: str | None = None,
        say_sink: Sink | None = None,
        say_enabled: Callable[[], bool] = lambda: False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.sink = sink
        self.token = token
        # /say: refused without a token; say_enabled is read from handler threads.
        self.say_token = say_token
        self.say_sink = say_sink
        self.say_enabled = say_enabled
        self.clock = clock
        self.say_lock = threading.Lock()
        self.last_say: float | None = None
        self.reply_until: float | None = None  # one chat reply may pass the interval until then
        self.say_interval_s = SAY_INTERVAL_S
        self.session_count = 0  # written by the main thread, read by /health
        # Handler threads append hook latencies and the main thread appends paint lags while
        # /health reads both, so every access takes the lock and readers sort a snapshot.
        self.stats_lock = threading.Lock()
        self.latencies_ms: collections.deque[float] = collections.deque(maxlen=1000)
        self.paint_lags_ms: collections.deque[float] = collections.deque(maxlen=1000)
        super().__init__((HOST, port), _Handler)

    def p99_ms(self) -> float | None:
        with self.stats_lock:
            ordered = list(self.latencies_ms)
        ordered.sort()
        return _percentile(ordered, 0.99) if ordered else None

    def record_paint(self, ms: float) -> None:
        """Note one receipt-to-repaint time; called from the main thread after the bubble repaints."""
        with self.stats_lock:
            self.paint_lags_ms.append(ms)

    def paint_stats(self) -> dict:
        with self.stats_lock:
            ordered = list(self.paint_lags_ms)
        ordered.sort()
        if not ordered:
            return {"n": 0, "p50_ms": None, "p99_ms": None, "max_ms": None}
        return {
            "n": len(ordered),
            "p50_ms": _percentile(ordered, 0.5),
            "p99_ms": _percentile(ordered, 0.99),
            "max_ms": ordered[-1],
        }

    def take_say_slot(self, reply: bool = False) -> bool:
        """Claim the next /say slot; False if the last line was under say_interval_s ago.

        A reply uses up the credit grant_reply() left instead, while it lasts; with no
        credit it waits its turn like any line.
        """
        with self.say_lock:
            now = self.clock()
            if reply and self.reply_until is not None and now <= self.reply_until:
                self.reply_until = None
                self.last_say = now
                return True
            if self.last_say is not None and now - self.last_say < self.say_interval_s:
                return False
            self.last_say = now
            return True

    def set_say_interval(self, seconds: float) -> None:
        """The least time between two lines: SAY_INTERVAL_S, or 0 for No pause between lines."""
        with self.say_lock:
            self.say_interval_s = max(0.0, seconds)

    def grant_reply(self) -> None:
        """Let one reply through within REPLY_WINDOW_S: the daemon just passed a chat to the mind."""
        with self.say_lock:
            self.reply_until = self.clock() + REPLY_WINDOW_S

    def start_thread(self) -> threading.Thread:
        thread = threading.Thread(target=self.serve_forever, name="companion-server", daemon=True)
        thread.start()
        return thread


class _Handler(BaseHTTPRequestHandler):
    server: CompanionServer

    def do_POST(self) -> None:
        started = time.perf_counter()
        # Hook URLs carry ?from=cc-companion so install.py can tell its own entries apart.
        route = urlsplit(self.path).path
        if route == "/say":
            self._say()
            return
        sanitize = {"/hook": sanitize_hook, "/status": sanitize_status}.get(route)
        if sanitize is None:
            self._empty(404)
            return
        payload = self._read_json()
        event = sanitize(payload) if payload is not None and self._authorized() else None
        if event is not None:
            self.server.sink({**event, "_rx": started})
        self._empty(204)
        elapsed = (time.perf_counter() - started) * 1000
        if event is None:
            log.info("dropped %s body (unparseable, unauthorized or not an event)", self.path)
            return
        with self.server.stats_lock:
            self.server.latencies_ms.append(elapsed)
        log.info(
            "%s tool=%s session=%s %.2fms%s",
            event.get("hook_event_name"),
            event.get("tool_name", "-"),
            str(event.get("session_id", ""))[:8],
            elapsed,
            " subagent" if event.get("subagent") else "",
        )

    def _say(self) -> None:
        payload = self._read_json()
        speech = None
        if not self._say_authorized():
            status = 403
        elif not self.server.say_enabled():
            status = 409
        elif (speech := sanitize_say(payload)) is None:
            status = 400
        elif not self.server.take_say_slot(speech["reply"]):
            status = 429
        else:
            status = 204
        if status == 204 and self.server.say_sink is not None:
            self.server.say_sink(speech)
        self._empty(status)
        if status == 204:
            log.info("say len=%d mood=%s%s", len(speech["text"] or ""), speech["mood"], " reply" if speech["reply"] else "")
        else:
            log.info("say refused (%d)", status)

    def do_GET(self) -> None:
        if urlsplit(self.path).path != "/health":
            self._empty(404)
            return
        paint = self.server.paint_stats()
        body = json.dumps(
            {
                "ok": True,
                "app": APP_ID,
                "sessions": self.server.session_count,
                "hook_p99_ms": self.server.p99_ms(),
                "paint_n": paint["n"],
                "paint_p50_ms": paint["p50_ms"],
                "paint_p99_ms": paint["p99_ms"],
                "paint_max_ms": paint["max_ms"],
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> object | None:
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return None
        if length <= 0:
            return None
        if length > MAX_BODY:
            remaining = length
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 1 << 20))
                if not chunk:
                    break
                remaining -= len(chunk)
            return None
        try:
            return json.loads(self.rfile.read(length))
        except ValueError:
            return None

    def _from_a_local_tool(self) -> bool:
        # A web page can reach loopback with a CORS simple request (text/plain, no preflight).
        # Claude Code, the launcher and statusline.py send application/json with no Origin;
        # requiring that also forces a browser preflight, which fails since there is no do_OPTIONS.
        if self.headers.get_content_type() != "application/json" or "Origin" in self.headers:
            return False
        return self.headers.get("Sec-Fetch-Site", "none") == "none"

    def _authorized(self) -> bool:
        if not self._from_a_local_tool():
            return False
        if not self.server.token:
            return True
        return _same(self.headers.get("X-Companion-Token", ""), self.server.token)

    def _say_authorized(self) -> bool:
        token = self.server.say_token
        if not token or not self._from_a_local_tool():
            return False
        return _same(self.headers.get(SAY_TOKEN_HEADER, ""), token)

    def _empty(self, status: int) -> None:
        self.send_response(status)
        if status != 204:  # a 204 must not carry Content-Length (RFC 9110)
            self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass
