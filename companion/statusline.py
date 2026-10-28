"""Status line forwarder: post a snapshot of the session to the daemon, print one line.

Claude Code runs this as the statusLine command with JSON session data on stdin, at
session start, on each assistant message and every refreshInterval, and cancels a run
that is still going when the next one is due. It runs as `python.exe -I -S statusline.py`,
so it imports the standard library only and never `companion`: -I drops the script
directory from sys.path, and site and urllib alone would cost more than the whole run.

The snapshot keeps only the documented fields read below, flattened to the keys that
server.sanitize_status accepts; paths, names, repo and PR data and anything added later
stay behind. Each run sends a full snapshot, so a cancelled run loses nothing.

Exits 0 on every path, stops its network work 150 ms after it starts, and never starts
the daemon: reviving it is launcher.py's job.
"""

from __future__ import annotations

import time

T0 = time.monotonic()  # the start of the script; the work budget counts from here

import json  # noqa: E402
import os  # noqa: E402
import socket  # noqa: E402
import sys  # noqa: E402

DEFAULT_PORT = 47621  # server.DEFAULT_PORT, which this script cannot import
TOKEN_HEADER = "X-Companion-Token"
CONNECT_TIMEOUT_S = 0.05
DEADLINE_S = 0.15
MAX_STDIN = 1 << 20
MAX_TEXT = 200
MAX_NUMBER = 1e12

# Quota windows under rate_limits, in printed order, with their printed labels. Each one
# forwards <name>_pct and <name>_resets_at (epoch seconds).
WINDOWS = (("five_hour", "5h"), ("seven_day", "7d"))


def _number(value: object) -> int | float | None:
    """An int or float (not bool) that is finite and at most 1e12 in magnitude.

    json.loads accepts NaN, Infinity and 1e400, and the daemon must never paint one.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    # NaN is the one value not equal to itself; written out to skip importing math. The
    # magnitude check also rejects both infinities.
    if value != value or abs(value) > MAX_NUMBER:
        return None
    return value


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= MAX_TEXT else None


def _label(value: object, *keys: str) -> str | None:
    """A short string, either given directly or as the first valid one of `keys` in an object."""
    if isinstance(value, dict):
        return next((text for text in map(_text, map(value.get, keys)) if text), None)
    return _text(value)


def _basename(path: object) -> str | None:
    """The last part of a path, cut the way server.py cuts hook `cwd`, so the label never flips."""
    if not isinstance(path, str):
        return None
    name = path.rstrip("\\/").replace("\\", "/").rsplit("/", 1)[-1]
    return name[:MAX_TEXT] or None


def _object(data: dict, key: str) -> dict:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def flatten(data: object) -> dict | None:
    """The forwarded fields of one payload as flat keys; None if `data` is not an object.

    The result may lack session_id, in which case main() prints the line but posts nothing.
    """
    if not isinstance(data, dict):
        return None
    texts = {
        "session_id": _text(data.get("session_id")),
        "project": _basename(_object(data, "workspace").get("current_dir")) or _basename(data.get("cwd")),
        "model": _label(data.get("model"), "display_name", "id"),
        "effort": _label(data.get("effort"), "level"),
    }
    fields: dict = {key: value for key, value in texts.items() if value is not None}
    context = _object(data, "context_window")
    if "used_percentage" in context:
        # Null early in a session and after /compact; sent as null so the ring clears.
        pct = context["used_percentage"]
        if pct is None or _number(pct) is not None:
            fields["context_pct"] = pct
    cost = _number(_object(data, "cost").get("total_cost_usd"))
    if cost is not None:
        fields["cost_usd"] = cost
    limits = _object(data, "rate_limits")
    for name, _ in WINDOWS:
        window = _object(limits, name)
        for source, suffix in (("used_percentage", "pct"), ("resets_at", "resets_at")):
            value = _number(window.get(source))
            if value is not None:
                fields[f"{name}_{suffix}"] = value
    return fields


def _countdown(seconds: float) -> str:
    """Time left in whole units, rounded down: <1m, 45m, 2h14m, 3d4h."""
    minutes = int(seconds // 60)
    if minutes < 1:
        return "<1m"
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h{minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d{hours}h"


def _printable(text: str) -> str:
    """Printable ASCII only: control characters are removed and anything else becomes '?'."""
    return "".join(c if c <= "~" else "?" for c in text if c >= " " and c != "\x7f")


def render_line(fields: dict, columns: int | None, now: float) -> str:
    """The line shown under the prompt: model and effort, context, then quota or cost.

    A quota window is printed only while its reset is unknown or still ahead of `now`
    (Claude Code drops a lapsed window too); the cost shows only when no window does.
    """
    segments: list[str] = []
    model, effort = _text(fields.get("model")), _text(fields.get("effort"))
    if model:
        segments.append(f"{model} {effort}" if effort else model)
    context = _number(fields.get("context_pct"))
    if context is not None:
        segments.append(f"ctx {round(context)}%")
    windows = 0
    for name, label in WINDOWS:
        pct, resets_at = _number(fields.get(f"{name}_pct")), _number(fields.get(f"{name}_resets_at"))
        if pct is None or (resets_at is not None and resets_at <= now):
            continue
        windows += 1
        segment = f"{label} {round(pct)}%"
        segments.append(segment if resets_at is None else f"{segment} {_countdown(resets_at - now)}")
    cost = _number(fields.get("cost_usd"))
    if cost is not None and not windows:
        segments.append(f"${cost:.2f}")
    limit = columns - 1 if columns is not None and columns > 1 else MAX_TEXT
    return _printable(" | ".join(segments))[:limit]


def post(body: bytes, port: int, token: str | None, deadline: float) -> bool:
    """POST one snapshot to /status; True if the daemon answered 204 before `deadline`.

    The request is built by hand on a raw socket because http.client and urllib take
    longer to import than the request takes to finish. `deadline` is a time.monotonic()
    value. Reading the reply means the server never writes into a socket we reset.
    """
    try:
        head = [
            "POST /status HTTP/1.1",
            f"Host: 127.0.0.1:{port}",
            "Content-Type: application/json",
            f"Content-Length: {len(body)}",
            "Connection: close",
        ]
        if token:
            if "\r" in token or "\n" in token:
                raise ValueError("token would split the header")
            head.append(f"{TOKEN_HEADER}: {token}")
        request = ("\r\n".join(head) + "\r\n\r\n").encode("latin-1") + body
        connect_timeout = min(CONNECT_TIMEOUT_S, max(0.02, deadline - time.monotonic()))
        with socket.create_connection(("127.0.0.1", port), timeout=connect_timeout) as sock:
            sock.settimeout(max(0.02, deadline - time.monotonic()))
            sock.sendall(request)
            reply = b""
            while b"\n" not in reply and len(reply) < 64:
                # Only the first read gets a 20 ms floor, so a listener trickling bytes
                # cannot hold the line past the deadline.
                remaining = deadline - time.monotonic()
                if reply and remaining <= 0:
                    break
                sock.settimeout(remaining if reply else max(0.02, remaining))
                chunk = sock.recv(64 - len(reply))
                if not chunk:
                    break
                reply += chunk
        # The server speaks HTTP/1.0, so the status line reads "HTTP/1.0 204 No Content".
        return b" 204 " in reply.split(b"\n", 1)[0]
    except (OSError, ValueError, OverflowError):
        return False


def _port(environ: dict) -> int:
    try:
        port = int(environ.get("CC_COMPANION_PORT", DEFAULT_PORT))
    except (TypeError, ValueError):
        return DEFAULT_PORT
    return port if 0 < port < 65536 else DEFAULT_PORT


def _columns(environ: dict) -> int | None:
    """Claude Code sets COLUMNS to the terminal width, since the script cannot read it."""
    try:
        return int(environ.get("COLUMNS", ""))
    except (TypeError, ValueError):
        return None


def main(stdin=None, stdout=None, environ=None, now: float | None = None) -> int:
    """Forward one payload and print its line. Streams are binary; always returns 0."""
    try:
        stdin = sys.stdin.buffer if stdin is None else stdin
        stdout = sys.stdout.buffer if stdout is None else stdout
        environ = os.environ if environ is None else environ
        raw = stdin.read(MAX_STDIN + 1)
        if len(raw) > MAX_STDIN:
            return 0
        # utf-8-sig also takes a leading byte-order mark, which json.loads would refuse.
        fields = flatten(json.loads(raw.decode("utf-8-sig", "replace")))
        if fields is None:
            return 0
        if "session_id" in fields:
            body = json.dumps(fields, allow_nan=False).encode()
            post(body, _port(environ), environ.get("CC_COMPANION_TOKEN"), T0 + DEADLINE_S)
        line = render_line(fields, _columns(environ), time.time() if now is None else now)
        if line:
            # Bytes, because -I ignores PYTHONIOENCODING and the console code page varies.
            stdout.write(line.encode("ascii", "replace") + b"\n")
            stdout.flush()
    except Exception:  # noqa: BLE001 - the status line must exit 0 whatever happens
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
