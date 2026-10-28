"""reduce() folds one event into the store; project() derives what to show. Pure, no Qt.

Events arrive already allowlisted and flattened by server.py: `project` (cwd basename),
`model`, `effort` and `permission_mode` are plain strings when present. Status line
reports arrive as events named STATUS_EVENT with the flat keys statusline.py sends.
Handlers read only the keys they know, so anything else (such as the server's `_rx`
receipt stamp) is ignored and never stored.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable

from .model import DisplayState, Quota, SessionRecord, State, Store, is_neutral_tool, verb_for_tool

ERROR_FLASH_S = 3.0
DONE_HOLD_S = 20.0
COMPACT_CAP_S = 600.0  # in case PostCompact never arrives
SUBAGENT_HOLD_S = 120.0  # a subagent can think for a while between tool calls
SESSION_TTL_S = 1800.0  # a crashed Claude Code never sends SessionEnd

STATUS_EVENT = "StatusLine"

# idle_prompt is left out on purpose: it fires once a finished session has sat at the prompt
# for a while, so mapping it to waiting would let every idle session outrank active ones.
WAITING_NOTIFICATIONS = frozenset(
    {"permission_prompt", "agent_needs_input", "elicitation_dialog", "elicitation_url_dialog"}
)
# Only an MCP tool raises an elicitation, so a neutral tool finishing ends that wait.
ELICITATION_REASONS = frozenset({"elicitation_dialog", "elicitation_url_dialog"})
DONE_NOTIFICATIONS = frozenset({"agent_completed"})
# The main conversation does none of these while it compacts, so one from the session itself
# ends `compacting` even when PostCompact never arrives. (Subagent events never get here.)
MAIN_ACTIVITY = frozenset(
    {"UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure", "PostToolBatch",
     "PermissionRequest", "Stop", "StopFailure"}
)  # fmt: skip

QUOTA_KEYS = ("five_hour_pct", "five_hour_resets_at", "seven_day_pct", "seven_day_resets_at")
QUOTA_WINDOWS = ("five_hour", "seven_day")

log = logging.getLogger("companion")

Handler = Callable[[SessionRecord, dict, float], None]


def reduce(store: Store, event: dict, now: float) -> Store:
    sessions = {sid: rec for sid, rec in store.sessions.items() if now - rec.last_seen < SESSION_TTL_S}
    quota = fresh_quota(store.quota, now)
    name = event.get("hook_event_name")
    session_id = event.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return Store(sessions, quota)
    if name == "SessionEnd":
        sessions.pop(session_id, None)
        return Store(sessions, quota)

    existing = sessions.get(session_id)
    rec = dataclasses.replace(
        existing,
        overlays=dict(existing.overlays),
        subagents=dict(existing.subagents),
        agent_waits=dict(existing.agent_waits),
        tasks_created=set(existing.tasks_created),
        tasks_completed=set(existing.tasks_completed),
    ) if existing else SessionRecord(session_id)

    if name == STATUS_EVENT:
        quota = _apply_status(rec, event, quota)
        rec.last_seen = now
    elif event.get("subagent"):
        _subagent_event(rec, event, now)
        rec.last_active = rec.last_seen = now
    else:
        _apply_common(rec, event)
        if name in MAIN_ACTIVITY:
            rec.overlays.pop(State.COMPACTING, None)
        handler = HANDLERS.get(name)
        if handler is None:
            log.info("unhandled hook event %s", name)
        else:
            handler(rec, event, now)
        rec.last_active = rec.last_seen = now
    sessions[session_id] = rec
    return Store(sessions, quota)


def project(store: Store, now: float) -> DisplayState:
    live = [rec for rec in store.sessions.values() if now - rec.last_seen < SESSION_TTL_S]
    quota = fresh_quota(store.quota, now)
    if not live:
        return DisplayState(quota=quota)
    states = {rec.session_id: rec.effective_state(now) for rec in live}
    waiting = [rec for rec in live if states[rec.session_id] is State.WAITING]
    # Waiting needs a human, so it wins across sessions; otherwise show the most recent.
    shown = max(waiting or live, key=lambda rec: (rec.last_active, rec.session_id))
    reason = shown.waiting_reason
    if reason is None and shown.agent_waits:  # waiting on a subagent's prompt
        reason = next(iter(shown.agent_waits.values()))[0]
    return DisplayState(
        state=states[shown.session_id],
        session_id=shown.session_id,
        project=shown.project,
        tool_name=shown.tool_name,
        model=shown.model,
        effort=shown.effort,
        permission_mode=shown.permission_mode,
        waiting_reason=reason,
        error_reason=shown.error_reason,
        subagents=len(shown.subagents),
        # A task created before the daemon started can complete after; never show 3/2.
        tasks_created=len(shown.tasks_created | shown.tasks_completed),
        tasks_completed=len(shown.tasks_completed),
        context_pct=shown.context_pct,
        cost_usd=shown.cost_usd,
        quota=quota,
        sessions=len(live),
        waiting_sessions=len(waiting),
    )


def next_transition(store: Store, now: float) -> float | None:
    """Earliest future time project() could change with no new event.

    That is an overlay lapsing, a session expiring or a quota window reaching its reset.
    """
    times = [rec.last_seen + SESSION_TTL_S for rec in store.sessions.values()]
    times += [until for rec in store.sessions.values() for until in rec.overlays.values()]
    quota = store.quota
    times += [t for t in (quota.five_hour_resets_at, quota.seven_day_resets_at) if t is not None]
    future = [t for t in times if t > now]
    return min(future) if future else None


def fresh_quota(quota: Quota, now: float) -> Quota:
    """`quota` without the windows whose reset time has passed, as Claude Code drops them; `seen` is kept."""
    updates: dict[str, None] = {}
    for window in QUOTA_WINDOWS:
        resets_at = getattr(quota, f"{window}_resets_at")
        if resets_at is not None and resets_at <= now:
            updates[f"{window}_pct"] = updates[f"{window}_resets_at"] = None
    return dataclasses.replace(quota, **updates) if updates else quota


def _apply_common(rec: SessionRecord, event: dict) -> None:
    for key in ("project", "model", "effort", "permission_mode"):
        value = event.get(key)
        if isinstance(value, str) and value:
            setattr(rec, key, value)


def _apply_status(rec: SessionRecord, event: dict, quota: Quota) -> Quota:
    rec.status_seen = True
    for key in ("project", "model", "effort"):
        value = event.get(key)
        if isinstance(value, str) and value:
            setattr(rec, key, value)
    if "context_pct" in event:  # null after /compact until the next response
        rec.context_pct = event["context_pct"]
    if event.get("cost_usd") is not None:
        rec.cost_usd = event["cost_usd"]
    updates = {key: event[key] for key in QUOTA_KEYS if event.get(key) is not None}
    return dataclasses.replace(quota, **updates, seen=True) if updates else quota


def _set_state(rec: SessionRecord, state: State) -> None:
    rec.state = state
    if state is not State.WAITING:
        rec.waiting_reason = None
    if state is not State.ERROR:
        rec.error_reason = None


def _session_start(rec: SessionRecord, event: dict, now: float) -> None:
    if event.get("source") == "compact":
        rec.overlays.pop(State.COMPACTING, None)
        return
    rec.overlays.clear()
    rec.agent_waits.clear()
    rec.tool_name = None
    _set_state(rec, State.IDLE)


def _user_prompt(rec: SessionRecord, event: dict, now: float) -> None:
    rec.overlays.pop(State.DONE, None)
    rec.tool_name = None
    _set_state(rec, State.THINKING)


def _pre_tool(rec: SessionRecord, event: dict, now: float) -> None:
    tool_name = event.get("tool_name")
    state = verb_for_tool(tool_name)
    if state is None:  # neutral: leave the verb of whatever real tool is running
        return
    rec.tool_name = tool_name
    _set_state(rec, state)


def _post_tool(rec: SessionRecord, event: dict, now: float) -> None:
    # No event marks a permission being granted; the tool finishing is the first sign.
    # A neutral tool finishing says nothing about a prompt raised for another tool, except an
    # elicitation: the Notification names no tool, and only an MCP tool can raise one.
    if rec.state is not State.WAITING:
        return
    tool_name = event.get("tool_name")
    if not is_neutral_tool(tool_name) or rec.tool_name == tool_name or rec.waiting_reason in ELICITATION_REASONS:
        _set_state(rec, State.THINKING)


def _post_tool_failure(rec: SessionRecord, event: dict, now: float) -> None:
    _post_tool(rec, event, now)
    tool_name = event.get("tool_name")
    if not is_neutral_tool(tool_name):
        rec.tool_name = tool_name or rec.tool_name
    if event.get("is_interrupt") is not True:  # pressing Esc is not an error
        rec.overlays[State.ERROR] = now + ERROR_FLASH_S


def _post_tool_batch(rec: SessionRecord, event: dict, now: float) -> None:
    rec.tool_name = None
    _set_state(rec, State.THINKING)


def _permission_request(rec: SessionRecord, event: dict, now: float) -> None:
    rec.tool_name = event.get("tool_name") or rec.tool_name
    _set_state(rec, State.WAITING)
    rec.waiting_reason = "permission"


def _permission_denied(rec: SessionRecord, event: dict, now: float) -> None:
    if rec.state is State.WAITING:
        _set_state(rec, State.THINKING)


def _notification(rec: SessionRecord, event: dict, now: float) -> None:
    kind = event.get("notification_type")
    if kind in WAITING_NOTIFICATIONS:
        _set_state(rec, State.WAITING)
        rec.waiting_reason = kind
    elif kind in DONE_NOTIFICATIONS:
        rec.overlays[State.DONE] = now + DONE_HOLD_S
    else:
        log.info("notification type %r ignored", kind)


def _stop(rec: SessionRecord, event: dict, now: float) -> None:
    rec.tool_name = None
    _set_state(rec, State.IDLE)
    rec.overlays[State.DONE] = now + DONE_HOLD_S


def _stop_failure(rec: SessionRecord, event: dict, now: float) -> None:
    rec.tool_name = None
    rec.overlays.pop(State.DONE, None)
    _set_state(rec, State.ERROR)
    rec.error_reason = event.get("error_type") or "error"


def _subagent_start(rec: SessionRecord, event: dict, now: float) -> None:
    agent_id = event.get("agent_id")
    if agent_id:
        rec.subagents[agent_id] = event.get("agent_type") or "agent"
    rec.overlays[State.DELEGATING] = now + SUBAGENT_HOLD_S


def _subagent_stop(rec: SessionRecord, event: dict, now: float) -> None:
    agent_id = event.get("agent_id")
    rec.subagents.pop(agent_id, None)
    rec.agent_waits.pop(agent_id, None)
    if not rec.subagents:
        rec.overlays.pop(State.DELEGATING, None)


def _subagent_event(rec: SessionRecord, event: dict, now: float) -> None:
    """An event from a subagent working under this session (the server flags them).

    It never sets the parent's verb, tool or labels, so the parent does not flicker through
    its subagents' tools. It holds the parent at delegating instead, which the parent's own
    tool work, a wait, an error or a compaction still outranks. A prompt the subagent raises
    shows as waiting until that subagent moves on or stops; tasks it creates still count.
    """
    name, agent = event.get("hook_event_name"), event.get("agent_id")
    rec.overlays[State.DELEGATING] = now + SUBAGENT_HOLD_S
    kind = event.get("notification_type")
    if name == "PermissionRequest":
        rec.agent_waits[agent] = ("permission", event.get("tool_name"))
    elif name == "Notification" and kind in WAITING_NOTIFICATIONS:
        rec.agent_waits[agent] = (kind, None)
    elif name in ("TaskCreated", "TaskCompleted"):
        HANDLERS[name](rec, event, now)
    elif agent in rec.agent_waits:
        _end_agent_wait(rec, agent, name, event.get("tool_name"))


def _end_agent_wait(rec: SessionRecord, agent: str, name: str, tool_name: object) -> None:
    """End a subagent's prompt when that subagent shows it moved past it."""
    _, waited_tool = rec.agent_waits[agent]
    if waited_tool is None:  # a question with no tool behind it: any next step ends it
        moved_on = name in ("PreToolUse", "PostToolUse", "PostToolUseFailure", "PostToolBatch", "PermissionDenied")
    else:  # a permission prompt: its tool finishing or being denied, or the batch ending
        moved_on = name == "PostToolBatch" or (
            name in ("PostToolUse", "PostToolUseFailure", "PermissionDenied") and tool_name == waited_tool
        )
    if moved_on:
        del rec.agent_waits[agent]


def _task_key(event: dict, seen: set[str]) -> str:
    task_id = event.get("task_id")
    return task_id if isinstance(task_id, str) and task_id else f"anonymous-{len(seen)}"


def _task_created(rec: SessionRecord, event: dict, now: float) -> None:
    rec.tasks_created.add(_task_key(event, rec.tasks_created))


def _task_completed(rec: SessionRecord, event: dict, now: float) -> None:
    rec.tasks_completed.add(_task_key(event, rec.tasks_completed))


def _pre_compact(rec: SessionRecord, event: dict, now: float) -> None:
    # A subagent compacting its own context never gets here: it arrives flagged under the
    # session's id with no PostCompact after it, and would hold compacting for the whole cap.
    rec.overlays[State.COMPACTING] = now + COMPACT_CAP_S


def _post_compact(rec: SessionRecord, event: dict, now: float) -> None:
    rec.overlays.pop(State.COMPACTING, None)


def _model_switch(rec: SessionRecord, event: dict, now: float) -> None:
    model = event.get("to_model")
    if isinstance(model, str) and model:
        rec.model = model


HANDLERS: dict[str, Handler] = {
    "SessionStart": _session_start,
    "UserPromptSubmit": _user_prompt,
    "PreToolUse": _pre_tool,
    "PostToolUse": _post_tool,
    "PostToolUseFailure": _post_tool_failure,
    "PostToolBatch": _post_tool_batch,
    "PermissionRequest": _permission_request,
    "PermissionDenied": _permission_denied,
    "Notification": _notification,
    "Stop": _stop,
    "StopFailure": _stop_failure,
    "SubagentStart": _subagent_start,
    "SubagentStop": _subagent_stop,
    "TaskCreated": _task_created,
    "TaskCompleted": _task_completed,
    "PreCompact": _pre_compact,
    "PostCompact": _post_compact,
    "PostModelSwitch": _model_switch,
}
