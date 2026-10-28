"""Real hook payloads recorded with tools/capture_hooks.py, paths and text scrubbed.

The 01-04 set is one headless session whose turn failed authentication: SessionStart,
UserPromptSubmit, StopFailure, SessionEnd, in the order Claude Code sent them.
"""

import json
from pathlib import Path

import pytest

from companion.model import State, Store
from companion.reducer import project, reduce
from companion.server import sanitize_hook

FIXTURES = sorted((Path(__file__).parent / "fixtures").glob("*.json"))
CONTENT = ("FIXTURE-PROMPT-TEXT", "FIXTURE-REPLY-TEXT", "FIXTURE.jsonl", "Users")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_fixtures_exist():
    assert FIXTURES


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_real_payload_keeps_only_allowlisted_fields(path):
    raw = load(path)
    kept = sanitize_hook(raw)
    assert kept["hook_event_name"] == raw["hook_event_name"]
    assert kept["session_id"] == raw["session_id"]
    assert kept["project"] == "gate-project"
    text = json.dumps(kept)
    assert not any(marker in text for marker in CONTENT), text
    assert not {"transcript_path", "cwd", "prompt", "prompt_id", "last_assistant_message"} & set(kept)


def test_stop_failure_payload_flattens_effort_and_error():
    raw = load(next(p for p in FIXTURES if p.stem.endswith("StopFailure")))
    kept = sanitize_hook(raw)
    assert kept["effort"] == "medium"
    assert kept["error_type"] == "authentication_failed"
    assert "error" not in kept


def test_recorded_session_drives_the_expected_states():
    store, now, seen = Store(), 1_000.0, []
    for path in FIXTURES:
        store = reduce(store, sanitize_hook(load(path)), now)
        display = project(store, now)
        seen.append((display.state, display.sessions, display.error_reason))
        now += 0.05
    assert seen == [
        (State.IDLE, 1, None),
        (State.THINKING, 1, None),
        (State.ERROR, 1, "authentication_failed"),
        (State.IDLE, 0, None),
    ]
