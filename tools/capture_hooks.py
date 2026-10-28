"""Record raw hook payloads to a directory, for building test fixtures.

    python tools/capture_hooks.py settings OUT [--port 47622] > capture-settings.json
    python tools/capture_hooks.py serve OUT [--port 47622]    # HTTP hooks POST here
    python tools/capture_hooks.py stdin OUT                   # command hook (SessionStart)

Point a throwaway session at it with `claude --settings capture-settings.json`, never
your user settings. Payloads hold prompts, tool input and output, and local paths
verbatim: scrub them before copying anything into tests/fixtures.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 47622


def events() -> list[str]:
    snippet = json.loads((ROOT / "hooks" / "settings.snippet.json").read_text(encoding="utf-8"))
    return list(snippet["hooks"])


def save(out: Path, body: bytes) -> None:
    try:
        name = json.loads(body).get("hook_event_name", "unknown")
    except (ValueError, AttributeError):
        name = "unparsed"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{time.time_ns()}-{name}.json").write_bytes(body)


def capture_settings(out: Path, port: int) -> dict:
    posix = lambda p: str(p).replace("\\", "/")  # noqa: E731
    command = {
        "type": "command",
        "command": posix(sys.executable),
        "args": [posix(Path(__file__).resolve()), "stdin", posix(out.resolve())],
        "timeout": 10,
    }
    http = {"type": "http", "url": f"http://127.0.0.1:{port}/capture", "timeout": 1}
    return {"hooks": {e: [{"hooks": [command if e == "SessionStart" else http]}] for e in events()}}


def serve(out: Path, port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            save(out, self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args) -> None:
            pass

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=["settings", "serve", "stdin"])
    parser.add_argument("out", type=Path)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    if args.mode == "settings":
        print(json.dumps(capture_settings(args.out, args.port), indent=2))
    elif args.mode == "serve":
        serve(args.out, args.port)
    else:
        save(args.out, sys.stdin.buffer.read())
    return 0


if __name__ == "__main__":
    sys.exit(main())
