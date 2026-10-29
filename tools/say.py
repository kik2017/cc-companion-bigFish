"""Send the companion a line to say, the way the persona will.

    python tools/say.py "早呀～" --mood happy --ttl 8
    python tools/say.py "早呀～" --cue greet      # her greeting clip, as her mind sends it

Reads this run's token from the app data folder (the daemon writes it at start) and
POSTs to /say. The persona must be On or Quiet in the menu. Standard library only.
"""

from __future__ import annotations

import argparse
import http.client
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from companion import settings as prefs  # noqa: E402
from companion.model import MOODS  # noqa: E402
from companion.server import HOST, SAY_TOKEN_HEADER, port_from_env  # noqa: E402

MEANING = {
    204: "shown",
    400: "not a valid line (empty, over 120 characters, or a bad mood or ttl)",
    403: "refused: the token is missing or stale; is this the running companion's app folder?",
    409: "the persona is off; turn it on from the menu (Persona > On or Quiet)",
    429: "too soon after the last line; wait 20 s",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("text")
    parser.add_argument("--mood", choices=MOODS, default=MOODS[0])
    parser.add_argument("--ttl", type=float, default=8.0, help="seconds on screen, 4-15")
    parser.add_argument("--cue", choices=("greet", "sleep"), help="a clip for her body: greeting you, or falling asleep")
    args = parser.parse_args(argv)
    try:
        token = prefs.say_token_path().read_text(encoding="utf-8").strip()
    except OSError:
        print(f"no token at {prefs.say_token_path()}; is the companion running?", file=sys.stderr)
        return 1
    body = json.dumps({"text": args.text, "mood": args.mood, "ttl_s": args.ttl, **({"cue": args.cue} if args.cue else {})}).encode()
    try:
        conn = http.client.HTTPConnection(HOST, port_from_env(), timeout=3)
        conn.request("POST", "/say", body=body, headers={"Content-Type": "application/json", SAY_TOKEN_HEADER: token})
        status = conn.getresponse().status
        conn.close()
    except OSError as error:
        print(f"cannot reach the companion: {error}", file=sys.stderr)
        return 1
    print(f"{status} {MEANING.get(status, 'unexpected')}")
    return 0 if status == 204 else 1


if __name__ == "__main__":
    sys.exit(main())
