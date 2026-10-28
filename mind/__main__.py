"""Entry point: python -m mind, started by the daemon with its stdin as the message pipe.

Exits when stdin closes (the daemon quit or stopped her). Exit code 3 means she cannot
run here (no API key, or the anthropic package is missing), so the daemon does not
restart her; the reason is in mind.log.
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import queue
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

from companion import settings as prefs
from companion.server import HOST, SAY_TOKEN_HEADER, port_from_env

from .budget import describe_limit
from .core import Mind
from .files import Files
from .voice import Voice, find_api_key

NOT_CONFIGURED = 3
TICK_S = 30.0

log = logging.getLogger("mind")


def setup_logging() -> None:
    path = prefs.app_dir() / "mind.log"
    prefs.rotate_log_if_large(path)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def say_sender(token: str):
    def say(text: str | None, mood: str, reply: bool = False) -> int:
        """POST one line (or a mood alone); a reply answers the chat box and stays up longer."""
        body = {"mood": mood} if text is None else {"text": text, "mood": mood, "ttl_s": 15 if reply else 10}
        if reply:
            body["reply"] = True
        try:
            conn = http.client.HTTPConnection(HOST, port_from_env(), timeout=3)
            conn.request(
                "POST", "/say", body=json.dumps(body).encode(),
                headers={"Content-Type": "application/json", SAY_TOKEN_HEADER: token},
            )
            status = conn.getresponse().status
            conn.close()
            return status
        except OSError as error:
            log.warning("could not reach /say: %s", type(error).__name__)
            return 0

    return say


def main() -> int:
    setup_logging()
    files = Files()
    files.setup()
    config = files.load_config()
    key = find_api_key()
    if key is None:
        log.warning("no API key: set CC_COMPANION_API_KEY (or ANTHROPIC_API_KEY); staying off")
        return NOT_CONFIGURED
    try:
        import anthropic
    except ImportError:
        log.warning("the anthropic package is missing: pip install -e .[mind]; staying off")
        return NOT_CONFIGURED
    token = os.environ.get("CC_COMPANION_SAY_TOKEN") or prefs.say_token_path().read_text(encoding="utf-8").strip()
    voice = Voice(anthropic.Anthropic(api_key=key, timeout=30.0, max_retries=1), config["model"])
    mind = Mind(files, config, voice, say_sender(token))
    log.info("mind up: model %s, %s", config["model"], describe_limit(config))

    inbox: queue.Queue = queue.Queue()

    def read() -> None:
        for line in sys.stdin:
            inbox.put(line)
        inbox.put(None)

    threading.Thread(target=read, name="mind-stdin", daemon=True).start()
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="mind-voice") as worker:
        while True:
            try:
                line = inbox.get(timeout=TICK_S)
            except queue.Empty:
                jobs = mind.tick()
            else:
                if line is None:
                    break
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                jobs = mind.handle(message) + mind.tick()
            for job in jobs:
                worker.submit(_guarded, job)
    log.info("mind stopped")
    return 0


def _guarded(job) -> None:
    try:
        job()
    except Exception:
        log.exception("a job failed")


if __name__ == "__main__":
    # pythonw has no console: give print-style output somewhere harmless.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    sys.exit(main())
