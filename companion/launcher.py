"""Command hook that starts the daemon if it is not up, then forwards the event.

    launcher.py            SessionStart: always forward, since no HTTP hook carries it
    launcher.py --revive   UserPromptSubmit, run async: restart a daemon that died
                           mid-session, forwarding only if it had to start one (when
                           the daemon was up, the event's HTTP hook delivered it)

Prints nothing (SessionStart stdout would be added to Claude's context) and exits 0 on
every path, so a broken companion can never block or slow a session for long.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

# Taken before the urllib imports, so the spawn note covers nearly all of the launcher's run.
_T0 = time.monotonic()

import urllib.error  # noqa: E402
import urllib.request  # noqa: E402
from pathlib import Path  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from companion import frozen  # noqa: E402
from companion import settings as prefs  # noqa: E402
from companion.server import APP_ID, port_from_env  # noqa: E402

HEALTH_TIMEOUT_S = 0.2
FORWARD_DEADLINE_S = 3.0
RETRY_PAUSE_S = 0.1


def healthy(port: int, timeout: float = HEALTH_TIMEOUT_S) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as response:
            return json.loads(response.read()).get("app") == APP_ID
    except (OSError, ValueError, AttributeError):
        return False


def daemon_command() -> list[str]:
    """pythonw.exe next to the running interpreter on Windows, or the portable build's pet, so no console appears."""
    return frozen.daemon_command()


def spawn() -> None:
    prefs.rotate_log_if_large()
    with open(prefs.log_path(), "ab") as log:
        options: dict = {
            "cwd": str(frozen.home()),
            "stdin": subprocess.DEVNULL,
            "stdout": log,
            "stderr": log,
            "close_fds": True,
        }
        if sys.platform == "win32":
            options["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True
        subprocess.Popen(daemon_command(), **options)


def note_spawn(after_ms: int) -> None:
    """Append a `launcher spawn after N ms` line to the daemon log, in its own line format.

    Read next to the new daemon's `first paint` line, it times a relaunch. The log is
    rotated first, as spawn() would, so both lines land in the same file.
    """
    now = time.time()
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)) + f",{int(now % 1 * 1000):03d}"
    try:
        prefs.rotate_log_if_large()
        with open(prefs.log_path(), "a", encoding="utf-8") as log:
            log.write(f"{stamp} INFO launcher spawn after {after_ms} ms\n")
    except OSError:
        pass


def forward(body: bytes, port: int, deadline: float) -> bool:
    """POST the hook body, retrying while a freshly spawned daemon is still starting."""
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/hook", data=body, headers={"Content-Type": "application/json"}
    )
    token = os.environ.get("CC_COMPANION_TOKEN")
    if token:
        request.add_header("X-Companion-Token", token)
    while True:
        try:
            with urllib.request.urlopen(request, timeout=0.5):
                return True
        except (OSError, ValueError):
            if time.monotonic() + RETRY_PAUSE_S >= deadline:
                return False
            time.sleep(RETRY_PAUSE_S)


def main(argv: list[str] | None = None) -> int:
    try:
        revive = "--revive" in (sys.argv[1:] if argv is None else argv)
        body = sys.stdin.buffer.read()
        port = port_from_env()
        deadline = time.monotonic() + FORWARD_DEADLINE_S
        started = not healthy(port)
        if started:
            note_spawn(int((time.monotonic() - _T0) * 1000))
            spawn()
        if body.strip() and (started or not revive):
            forward(body, port, deadline)
    except Exception:  # noqa: BLE001 - the hook must exit 0 whatever happens
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
