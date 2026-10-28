"""Session records, the display state, the state priority order and the persona's moods. No Qt imports."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum


class State(StrEnum):
    IDLE = "idle"
    THINKING = "thinking"
    SEARCHING = "searching"
    EDITING = "editing"
    RUNNING = "running"
    DELEGATING = "delegating"
    WAITING = "waiting"
    DONE = "done"
    ERROR = "error"
    COMPACTING = "compacting"


# The persona's moods, as POST /say names them; the first is the default. A skin maps
# them to clips (skin.Skin.mood_clip), and a mood with no clip only colours her words.
MOODS = ("calm", "happy", "proud", "worried", "sleepy", "curious")


# Highest first. Within one session the effective state is the highest-ranked of the base
# state and any unexpired overlays, so a finished-turn `done` never hides new activity and
# an `error` flash shows over whatever the session is doing.
PRIORITY = (
    State.WAITING,
    State.ERROR,
    State.COMPACTING,
    State.RUNNING,
    State.EDITING,
    State.SEARCHING,
    State.DELEGATING,
    State.THINKING,
    State.DONE,
    State.IDLE,
)
RANK = {state: rank for rank, state in enumerate(PRIORITY)}

# Tool name -> verb, first match wins. Tools not listed (TodoWrite, Skill, ...) read as thinking.
# MCP tools are neutral (None): per-turn bookkeeping calls share a batch with real tools and
# PreToolUse order is not fixed, so letting them change anything would hide the real verb.
TOOL_VERBS: tuple[tuple[re.Pattern[str], State | None], ...] = (
    (re.compile(r"Read|Grep|Glob|WebSearch|WebFetch"), State.SEARCHING),
    (re.compile(r"Edit|Write|NotebookEdit|MultiEdit"), State.EDITING),
    (re.compile(r"Bash|PowerShell"), State.RUNNING),
    (re.compile(r"Agent|Task|Workflow"), State.DELEGATING),
    (re.compile(r"mcp__.*"), None),
)


def verb_for_tool(tool_name: str | None) -> State | None:
    """The state a call to `tool_name` shows, or None for a neutral tool that changes nothing."""
    if isinstance(tool_name, str) and tool_name:
        for pattern, state in TOOL_VERBS:
            if pattern.fullmatch(tool_name):
                return state
    return State.THINKING


def is_neutral_tool(tool_name: str | None) -> bool:
    return tool_name is not None and verb_for_tool(tool_name) is None


@dataclass
class SessionRecord:
    session_id: str
    state: State = State.IDLE
    # Transient states (error flash, done, compacting) mapped to the time they lapse.
    overlays: dict[State, float] = field(default_factory=dict)
    tool_name: str | None = None
    project: str | None = None
    model: str | None = None
    effort: str | None = None
    permission_mode: str | None = None
    waiting_reason: str | None = None
    error_reason: str | None = None
    # True once any status line report arrived for this session; hosts that never run the
    # status line leave it False, which the controller logs.
    status_seen: bool = False
    subagents: dict[str, str] = field(default_factory=dict)
    # Prompts raised by subagents: agent_id -> (waiting reason, tool name or None). Kept apart
    # from `state`, so the parent's own state machine never has to undo a subagent's wait.
    agent_waits: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    # Task ids, so a repeated TaskCreated or TaskCompleted is counted once.
    tasks_created: set[str] = field(default_factory=set)
    tasks_completed: set[str] = field(default_factory=set)
    context_pct: float | None = None
    cost_usd: float | None = None
    # last_active orders sessions for display and moves only on hook events;
    # last_seen also moves on status line refreshes and drives expiry.
    last_active: float = 0.0
    last_seen: float = 0.0

    def effective_state(self, now: float) -> State:
        active = [state for state, until in self.overlays.items() if now < until]
        if self.agent_waits:
            active.append(State.WAITING)
        return min([self.state, *active], key=RANK.__getitem__)


@dataclass(frozen=True)
class Quota:
    """Account-wide subscription usage from the status line; latest report wins.

    `seen` marks that a report this run carried quota, so the cost line stays hidden on a
    subscription after every window lapses. It lives in memory only and is never saved.
    """

    five_hour_pct: float | None = None
    five_hour_resets_at: float | None = None
    seven_day_pct: float | None = None
    seven_day_resets_at: float | None = None
    seen: bool = False


@dataclass
class Store:
    sessions: dict[str, SessionRecord] = field(default_factory=dict)
    quota: Quota = field(default_factory=Quota)


@dataclass(frozen=True)
class DisplayState:
    state: State = State.IDLE
    session_id: str | None = None
    project: str | None = None
    tool_name: str | None = None
    model: str | None = None
    effort: str | None = None
    permission_mode: str | None = None
    waiting_reason: str | None = None
    error_reason: str | None = None
    subagents: int = 0
    tasks_created: int = 0
    tasks_completed: int = 0
    context_pct: float | None = None
    cost_usd: float | None = None
    quota: Quota = field(default_factory=Quota)
    sessions: int = 0
    waiting_sessions: int = 0
