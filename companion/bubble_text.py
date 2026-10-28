"""What the bubble says: built from a DisplayState, ready to draw. Pure, no Qt.

Rows, top to bottom, each left out when empty:
  speech    the persona's current line, when she has one (see compose())
  header    tone dot, title, and the context ring when the status line reported it
  labels    project, model, effort, and the live session count when there is more than one
  counters  permission mode, subagents, tasks done of created
  footer    one bar per fresh quota window, or else the session cost

No tool input is ever shown; none reaches the display state. A countdown is rounded
down to whole minutes, so the text changes only on a minute rollover and next_tick()
says when the next one is.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass

from .model import DisplayState, Quota, State

SEPARATOR = " · "  # a middle dot between spaces
TICK_MARGIN_S = 0.05  # land just after a rollover, never on it

ATTENTION_STATES: frozenset[State] = frozenset({State.WAITING, State.ERROR, State.DONE})

# Quota window field prefix on Quota -> bar label, in display order.
WINDOW_LABELS = (("five_hour", "5h"), ("seven_day", "7d"))

STATE_TITLES: dict[State, str] = {
    State.IDLE: "Ready",
    State.THINKING: "Thinking",
    State.SEARCHING: "Searching",
    State.EDITING: "Editing",
    State.RUNNING: "Running a command",
    State.DELEGATING: "Delegating",
    State.WAITING: "Needs you",
    State.DONE: "Done",
    State.ERROR: "Stopped on an error",
    State.COMPACTING: "Compacting context",
}
# A more specific title for the tool behind the verb; any other tool keeps the state title.
TOOL_TITLES: dict[State, dict[str, str]] = {
    State.SEARCHING: {
        "Read": "Reading files",
        "Grep": "Searching the code",
        "Glob": "Searching the code",
        "WebSearch": "Searching the web",
        "WebFetch": "Reading a web page",
    },
    State.EDITING: {"Write": "Writing a file", "NotebookEdit": "Editing a notebook"},
    State.DELEGATING: {"Workflow": "Running a workflow"},
}
# waiting_reason -> title; any elicitation_* reason asks a question.
WAITING_TITLES = {
    "permission": "Needs your permission",
    "permission_prompt": "Needs your permission",
    "agent_needs_input": "Needs your answer",
}
PERMISSION_TAGS = {
    "plan": "plan mode",
    "acceptEdits": "auto-accept edits",
    "bypassPermissions": "bypassing permissions",
}


@dataclass(frozen=True)
class Bar:
    label: str  # "5h" or "7d"
    pct: float  # 0-100
    level: str  # "ok", "warn" or "high"
    note: str | None  # countdown to the reset, when the reset time is known


@dataclass(frozen=True)
class BubbleText:
    """Everything the bubble draws. Equal values draw the same pixels, so the window repaints only on a change."""

    tone: str  # "none" with no live session, else the state's value; picks the dot colour
    title: str
    ring_pct: float | None  # 0-100, or None to leave the ring out
    ring_level: str | None
    labels: str | None
    counters: str | None
    bars: tuple[Bar, ...]
    cost: str | None
    speech: str | None = None  # her line, drawn above the header
    status: bool = True  # False: the bubble shows only her line, with no header or rows


def finite(v: object) -> float | None:
    """`v` as a float if it is a real, finite number (a bool is not), else None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        number = float(v)
    except OverflowError:  # an int beyond the float range
        return None
    return number if math.isfinite(number) else None


def level(pct: float) -> str:
    """Colour band of a percentage: ok below 70, warn below 90, high from 90."""
    if pct >= 90:
        return "high"
    if pct >= 70:
        return "warn"
    return "ok"


def countdown(seconds: float) -> str:
    """Time left in whole units, rounded down: <1m, 45m, 2h 14m, 3d 4h."""
    minutes = int(seconds // 60)
    if minutes < 1:
        return "<1m"
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def title_for(display: DisplayState) -> str:
    if display.sessions == 0:
        return "No live session"
    if display.waiting_sessions > 1:
        return f"{display.waiting_sessions} sessions need you"
    state = display.state
    if state is State.WAITING:
        reason = display.waiting_reason or ""
        if reason.startswith("elicitation_"):
            return "Has a question for you"
        return WAITING_TITLES.get(reason, STATE_TITLES[state])
    if state is State.ERROR:
        reason = display.error_reason
        if not reason:  # a failed tool flashes the error with no reason
            return "A tool failed"
        if reason == "error":
            return STATE_TITLES[state]
        return "Stopped: " + reason.replace("_", " ")
    return TOOL_TITLES.get(state, {}).get(display.tool_name or "", STATE_TITLES[state])


def bubble_text(display: DisplayState, *, show_subagents: bool, now: float) -> BubbleText:
    """The bubble for `display` at wall-clock time `now` (epoch seconds, for the countdowns)."""
    ring_pct = finite(display.context_pct)
    if ring_pct is not None:
        ring_pct = _percent(ring_pct)
    bars = tuple(
        Bar(label, pct, level(pct), None if resets_at is None else countdown(resets_at - now))
        for label, pct, resets_at in _windows(display.quota, now)
    )
    return BubbleText(
        tone="none" if display.sessions == 0 else display.state.value,
        title=title_for(display),
        ring_pct=ring_pct,
        ring_level=None if ring_pct is None else level(ring_pct),
        labels=_labels(display),
        counters=_counters(display, show_subagents),
        bars=bars,
        cost=None if bars else _cost(display),
    )


def visible(mode: str, display: DisplayState, text: BubbleText) -> bool:
    """Whether the bubble shows in bubble mode `mode`; the window also needs the pet visible."""
    if mode == "always":
        # With no session the quota bars still show, under "No live session".
        return display.sessions > 0 or bool(text.bars)
    if mode == "attention":
        return display.state in ATTENTION_STATES
    return False  # "hidden"


def compose(mode: str, display: DisplayState, text: BubbleText, speech: str | None) -> BubbleText | None:
    """What the bubble shows in bubble mode `mode`: the status, her line, both, or nothing (None).

    Her line shows in every mode but "hidden". When the mode would hide the status, the
    bubble carries her line alone.
    """
    status = visible(mode, display, text)
    if speech and mode != "hidden":
        return dataclasses.replace(text, speech=speech, status=status)
    return text if status else None


def next_tick(display: DisplayState, now: float) -> float | None:
    """When a shown countdown next changes, or None when no bar shows one.

    A countdown reading m minutes changes as soon as the time left drops below m whole
    minutes, which is `remaining % 60` seconds from now.
    """
    ticks = [
        now + (resets_at - now) % 60 + TICK_MARGIN_S
        for _, _, resets_at in _windows(display.quota, now)
        if resets_at is not None
    ]
    return min(ticks) if ticks else None


def _percent(value: float) -> float:
    return min(max(value, 0.0), 100.0)


def _windows(quota: Quota, now: float) -> list[tuple[str, float, float | None]]:
    """(label, pct, resets_at) of each window to show: a finite pct and a reset unknown or still ahead.

    The display state's quota is fresh when projected, but the tick timer redraws it
    later, so a window that lapsed since then is dropped here as well.
    """
    shown = []
    for name, label in WINDOW_LABELS:
        pct = finite(getattr(quota, f"{name}_pct"))
        resets_at = finite(getattr(quota, f"{name}_resets_at"))
        if pct is None or (resets_at is not None and resets_at <= now):
            continue
        shown.append((label, _percent(pct), resets_at))
    return shown


def _labels(display: DisplayState) -> str | None:
    # "high effort" rather than a bare "high", which reads oddly when no model name is known.
    effort = f"{display.effort} effort" if display.effort else None
    parts = [part for part in (display.project, display.model, effort) if part]
    if display.sessions > 1:
        parts.append(f"{display.sessions} sessions")
    return SEPARATOR.join(parts) or None


def _counters(display: DisplayState, show_subagents: bool) -> str | None:
    parts = []
    tag = PERMISSION_TAGS.get(display.permission_mode or "")
    if tag:
        parts.append(tag)
    if show_subagents and display.subagents > 0:
        parts.append(f"{display.subagents} subagent{'' if display.subagents == 1 else 's'}")
    if display.tasks_created > 0:
        parts.append(f"{display.tasks_completed}/{display.tasks_created} tasks")
    return SEPARATOR.join(parts) or None


def _cost(display: DisplayState) -> str | None:
    """The cost line, for API-key sessions: only when no quota was seen this run and the cost is above zero."""
    cost = finite(display.cost_usd)
    if display.quota.seen or cost is None or cost <= 0:
        return None
    return f"${cost:.2f} this session (est.)"
