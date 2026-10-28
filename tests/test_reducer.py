import pytest

import dataclasses

from companion.model import (
    PRIORITY,
    DisplayState,
    Quota,
    SessionRecord,
    State,
    Store,
    is_neutral_tool,
    verb_for_tool,
)
from companion.reducer import (
    COMPACT_CAP_S,
    DONE_HOLD_S,
    ERROR_FLASH_S,
    SESSION_TTL_S,
    STATUS_EVENT,
    SUBAGENT_HOLD_S,
    fresh_quota,
    next_transition,
    project,
    reduce,
)

T0 = 1_000_000.0


def ev(name, sid="s1", **fields):
    return {"hook_event_name": name, "session_id": sid, **fields}


def run(*events, store=None, start=T0, step=1.0):
    """Fold events one second apart; return the store and the time just after the last one."""
    store = store or Store()
    now = start
    for event in events:
        store = reduce(store, event, now)
        now += step
    return store, now


def shown(store, now):
    return project(store, now).state


# --- mapping table, one test per row -------------------------------------------------


def test_session_start_is_idle_and_keeps_project_and_model():
    store, now = run(ev("SessionStart", source="startup", project="demo-project", model="Opus"))
    display = project(store, now)
    assert display.state is State.IDLE
    assert display.project == "demo-project"
    assert display.model == "Opus"


def test_user_prompt_submit_is_thinking():
    store, now = run(ev("SessionStart", source="startup"), ev("UserPromptSubmit"))
    assert shown(store, now) is State.THINKING


@pytest.mark.parametrize(
    "tool, state",
    [
        ("Read", State.SEARCHING),
        ("Grep", State.SEARCHING),
        ("Glob", State.SEARCHING),
        ("WebSearch", State.SEARCHING),
        ("WebFetch", State.SEARCHING),
        ("Edit", State.EDITING),
        ("Write", State.EDITING),
        ("NotebookEdit", State.EDITING),
        ("MultiEdit", State.EDITING),
        ("Bash", State.RUNNING),
        ("PowerShell", State.RUNNING),
        ("Agent", State.DELEGATING),
        ("Task", State.DELEGATING),
        ("Workflow", State.DELEGATING),
        ("TodoWrite", State.THINKING),
        ("BashOutput", State.THINKING),
    ],
)
def test_pre_tool_use_verb_follows_tool_name(tool, state):
    store, now = run(ev("UserPromptSubmit"), ev("PreToolUse", tool_name=tool))
    display = project(store, now)
    assert display.state is state
    assert display.tool_name == tool


def test_verb_for_missing_tool_is_thinking():
    assert verb_for_tool(None) is State.THINKING
    assert verb_for_tool("") is State.THINKING


# --- MCP tools are neutral -----------------------------------------------------------


@pytest.mark.parametrize("tool", ["mcp__x__y", "mcp__github__create_issue", "mcp__ccd_turn__update_plan"])
def test_mcp_tools_have_no_verb(tool):
    assert verb_for_tool(tool) is None
    assert is_neutral_tool(tool)


@pytest.mark.parametrize("tool", [None, "", "Bash", "TodoWrite", "Workflow", "my_mcp__tool"])
def test_other_tools_are_not_neutral(tool):
    assert not is_neutral_tool(tool)


def test_mcp_call_after_prompt_stays_thinking_with_no_tool():
    store, now = run(ev("UserPromptSubmit"), ev("PreToolUse", tool_name="mcp__github__create_issue"))
    display = project(store, now)
    assert display.state is State.THINKING
    assert display.tool_name is None


def test_mcp_call_in_a_batch_keeps_the_running_tool():
    store, now = run(
        ev("UserPromptSubmit"),
        ev("PreToolUse", tool_name="Bash"),
        ev("PreToolUse", tool_name="mcp__ccd_turn__update_plan"),
        ev("PostToolUse", tool_name="mcp__ccd_turn__update_plan"),
    )
    display = project(store, now)
    assert display.state is State.RUNNING
    assert display.tool_name == "Bash"


def test_mcp_call_finishing_during_a_bash_permission_wait_stays_waiting():
    store, now = run(
        ev("PreToolUse", tool_name="Bash"),
        ev("PermissionRequest", tool_name="Bash"),
        ev("PostToolUse", tool_name="mcp__x__y"),
    )
    display = project(store, now)
    assert display.state is State.WAITING
    assert (display.tool_name, display.waiting_reason) == ("Bash", "permission")


def test_mcp_permission_wait_ends_when_that_tool_finishes():
    store, now = run(ev("UserPromptSubmit"), ev("PermissionRequest", tool_name="mcp__x__y"))
    display = project(store, now)
    assert (display.state, display.tool_name) == (State.WAITING, "mcp__x__y")
    store, now = run(ev("PostToolUse", tool_name="mcp__other__z"), store=store, start=now)
    assert shown(store, now) is State.WAITING
    store, now = run(ev("PostToolUse", tool_name="mcp__x__y"), store=store, start=now)
    display = project(store, now)
    assert display.state is State.THINKING
    assert display.waiting_reason is None


@pytest.mark.parametrize("kind", ["elicitation_dialog", "elicitation_url_dialog"])
@pytest.mark.parametrize("running", [[], [ev("PreToolUse", tool_name="Bash")]])
def test_mcp_elicitation_wait_ends_when_the_mcp_tool_finishes(kind, running):
    """Only an MCP tool raises an elicitation, so its own PostToolUse means it was answered."""
    asked = (
        ev("UserPromptSubmit"),
        *running,
        ev("PreToolUse", tool_name="mcp__srv__ask"),
        ev("Notification", notification_type=kind),
    )
    store, now = run(*asked)
    assert (project(store, now).state, project(store, now).waiting_reason) == (State.WAITING, kind)
    store, now = run(ev("PostToolUse", tool_name="mcp__srv__ask"), store=store, start=now)
    display = project(store, now)
    assert (display.state, display.waiting_reason) == (State.THINKING, None)
    store, now = run(*asked, ev("PostToolUseFailure", tool_name="mcp__srv__ask"))
    assert shown(store, now) is State.ERROR  # the declined elicitation's flash is not hidden


def test_mcp_call_finishing_during_an_agent_needs_input_wait_stays_waiting():
    store, now = run(
        ev("UserPromptSubmit"),
        ev("Notification", notification_type="agent_needs_input"),
        ev("PostToolUse", tool_name="mcp__x__y"),
    )
    assert project(store, now).waiting_reason == "agent_needs_input"


@pytest.mark.parametrize("tool", [5, 1.5, True])
def test_non_string_tool_names_are_not_neutral(tool):
    assert not is_neutral_tool(tool)
    assert verb_for_tool(tool) is State.THINKING


@pytest.mark.parametrize("name", ["PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest"])
def test_non_string_tool_name_never_raises(name):
    waiting = (ev("PreToolUse", tool_name="Bash"), ev("PermissionRequest", tool_name="Bash"))
    store, now = run(*waiting, ev(name, tool_name=5))
    assert shown(store, now) in (State.WAITING, State.THINKING, State.ERROR)


def test_mcp_failure_flashes_error_but_keeps_the_running_tool():
    store, now = run(
        ev("PreToolUse", tool_name="Bash"),
        ev("PostToolUseFailure", tool_name="mcp__x__y"),
    )
    display = project(store, now)
    assert (display.state, display.tool_name) == (State.ERROR, "Bash")
    assert shown(store, now - 1.0 + ERROR_FLASH_S) is State.RUNNING


def test_post_tool_use_leaves_state_unchanged():
    store, now = run(ev("PreToolUse", tool_name="Bash"), ev("PostToolUse", tool_name="Bash"))
    display = project(store, now)
    assert display.state is State.RUNNING
    assert display.tool_name == "Bash"


def test_post_tool_batch_returns_to_thinking_and_clears_tool():
    store, now = run(ev("PreToolUse", tool_name="Edit"), ev("PostToolUse"), ev("PostToolBatch"))
    display = project(store, now)
    assert display.state is State.THINKING
    assert display.tool_name is None


def test_post_tool_use_failure_flashes_error_then_previous():
    store, now = run(ev("PreToolUse", tool_name="Bash"), ev("PostToolUseFailure", tool_name="Bash"))
    failed_at = now - 1.0
    assert shown(store, failed_at + 0.1) is State.ERROR
    assert shown(store, failed_at + ERROR_FLASH_S - 0.01) is State.ERROR
    assert shown(store, failed_at + ERROR_FLASH_S) is State.RUNNING


def test_error_flash_outlasts_the_batch_end():
    store, now = run(ev("PreToolUse", tool_name="Bash"), ev("PostToolUseFailure"), ev("PostToolBatch"), step=0.5)
    assert shown(store, now) is State.ERROR
    assert shown(store, now + ERROR_FLASH_S) is State.THINKING


def test_interrupted_tool_does_not_flash_error():
    store, now = run(ev("PreToolUse", tool_name="Bash"), ev("PostToolUseFailure", is_interrupt=True))
    assert shown(store, now) is State.RUNNING


def test_permission_request_is_waiting():
    store, now = run(ev("PreToolUse", tool_name="Bash"), ev("PermissionRequest", tool_name="Bash"))
    display = project(store, now)
    assert display.state is State.WAITING
    assert display.waiting_reason == "permission"
    assert display.tool_name == "Bash"


def test_tool_finishing_after_permission_ends_waiting():
    store, now = run(ev("PreToolUse", tool_name="Bash"), ev("PermissionRequest"), ev("PostToolUse"))
    assert shown(store, now) is State.THINKING


def test_permission_denied_ends_waiting():
    store, now = run(ev("PreToolUse", tool_name="Bash"), ev("PermissionRequest"), ev("PermissionDenied"))
    display = project(store, now)
    assert display.state is State.THINKING
    assert display.waiting_reason is None


@pytest.mark.parametrize(
    "kind", ["permission_prompt", "agent_needs_input", "elicitation_dialog", "elicitation_url_dialog"]
)
def test_attention_notifications_are_waiting(kind):
    store, now = run(ev("UserPromptSubmit"), ev("Notification", notification_type=kind))
    display = project(store, now)
    assert display.state is State.WAITING
    assert display.waiting_reason == kind


@pytest.mark.parametrize("kind", ["idle_prompt", "auth_success", "plugin_notification", "something_new", None])
def test_other_notifications_change_nothing(kind):
    store, now = run(ev("UserPromptSubmit"), ev("Notification", notification_type=kind))
    assert shown(store, now) is State.THINKING


def test_agent_completed_notification_shows_done_when_idle():
    store, now = run(ev("SessionStart", source="startup"), ev("Notification", notification_type="agent_completed"))
    assert shown(store, now) is State.DONE


def test_agent_completed_notification_does_not_hide_activity():
    store, now = run(ev("PreToolUse", tool_name="Edit"), ev("Notification", notification_type="agent_completed"))
    assert shown(store, now) is State.EDITING


def test_stop_is_done_then_idle_after_hold():
    store, now = run(ev("UserPromptSubmit"), ev("Stop"))
    stopped_at = now - 1.0
    assert shown(store, stopped_at) is State.DONE
    assert shown(store, stopped_at + DONE_HOLD_S - 0.01) is State.DONE
    assert shown(store, stopped_at + DONE_HOLD_S) is State.IDLE


def test_new_prompt_clears_done():
    store, now = run(ev("Stop"), ev("UserPromptSubmit"))
    assert shown(store, now) is State.THINKING
    assert State.DONE not in store.sessions["s1"].overlays


def test_stop_failure_is_error_with_reason_until_next_prompt():
    store, now = run(ev("UserPromptSubmit"), ev("StopFailure", error_type="rate_limit"))
    display = project(store, now + 600)
    assert display.state is State.ERROR
    assert display.error_reason == "rate_limit"
    store, now = run(ev("UserPromptSubmit"), store=store, start=now)
    display = project(store, now)
    assert display.state is State.THINKING
    assert display.error_reason is None


def test_stop_failure_without_type_still_errors():
    store, now = run(ev("StopFailure"))
    assert project(store, now).error_reason == "error"


def test_subagents_are_counted_and_state_unchanged():
    store, now = run(
        ev("PreToolUse", tool_name="Agent"),
        ev("SubagentStart", agent_id="a1", agent_type="Explore"),
        ev("SubagentStart", agent_id="a2", agent_type="Plan"),
        ev("SubagentStart", agent_id="a2", agent_type="Plan"),
    )
    display = project(store, now)
    assert display.subagents == 2
    assert display.state is State.DELEGATING
    store, now = run(ev("SubagentStop", agent_id="a1"), ev("SubagentStop", agent_id="nope"), store=store, start=now)
    assert project(store, now).subagents == 1


def test_tasks_are_counted_once_per_id():
    store, now = run(
        ev("UserPromptSubmit"),
        ev("TaskCreated", task_id="1"),
        ev("TaskCreated", task_id="2"),
        ev("TaskCreated", task_id="2"),
        ev("TaskCompleted", task_id="1"),
    )
    display = project(store, now)
    assert (display.tasks_completed, display.tasks_created) == (1, 2)
    assert display.state is State.THINKING


def test_tasks_without_ids_still_count():
    store, now = run(ev("TaskCreated"), ev("TaskCreated"), ev("TaskCompleted"))
    display = project(store, now)
    assert (display.tasks_completed, display.tasks_created) == (1, 2)


def test_completed_never_exceeds_created():
    store, now = run(ev("TaskCompleted", task_id="from-before-restart"))
    display = project(store, now)
    assert (display.tasks_completed, display.tasks_created) == (1, 1)


def test_compacting_then_previous():
    store, now = run(ev("PreToolUse", tool_name="Read"), ev("PreCompact", trigger="auto"))
    assert shown(store, now) is State.COMPACTING
    store, now = run(ev("PostCompact", trigger="auto"), store=store, start=now)
    assert shown(store, now) is State.SEARCHING


def test_session_start_compact_also_ends_compacting():
    store, now = run(ev("UserPromptSubmit"), ev("PreCompact", trigger="manual"), ev("SessionStart", source="compact"))
    assert shown(store, now) is State.THINKING


def test_a_subagent_compacting_its_own_context_does_not_compact_the_session():
    store, now = run(ev("PreToolUse", tool_name="Bash"), ev("PreCompact", trigger="auto", subagent=True))
    assert shown(store, now) is State.RUNNING
    assert State.COMPACTING not in store.sessions["s1"].overlays


def test_a_subagent_post_compact_does_not_end_the_session_compacting():
    store, now = run(ev("UserPromptSubmit"), ev("PreCompact", trigger="auto"), ev("PostCompact", subagent=True))
    assert shown(store, now) is State.COMPACTING


@pytest.mark.parametrize(
    "event",
    [
        ev("UserPromptSubmit"),
        ev("PreToolUse", tool_name="Edit"),
        ev("PostToolUse", tool_name="Edit"),
        ev("PostToolUseFailure", tool_name="Bash"),
        ev("PostToolBatch"),
        ev("PermissionRequest", tool_name="Bash"),
        ev("Stop"),
        ev("StopFailure", error_type="rate_limit"),
    ],
    ids=lambda e: e["hook_event_name"],
)
def test_main_session_activity_ends_compacting_without_post_compact(event):
    store, now = run(ev("UserPromptSubmit"), ev("PreCompact", trigger="auto"), event)
    assert State.COMPACTING not in store.sessions["s1"].overlays
    assert shown(store, now) is not State.COMPACTING


def test_subagent_activity_does_not_end_the_session_compacting():
    store, now = run(
        ev("UserPromptSubmit"),
        ev("PreCompact", trigger="auto"),
        ev("PreToolUse", tool_name="Bash", subagent=True),
        ev("PostToolUse", tool_name="Bash", subagent=True),
        ev("PostToolBatch", subagent=True),
    )
    assert shown(store, now) is State.COMPACTING


def test_bookkeeping_events_do_not_end_compacting():
    store, now = run(
        ev("UserPromptSubmit"),
        ev("PreCompact", trigger="auto"),
        ev("Notification", notification_type="idle_prompt"),
        ev("SubagentStop", agent_id="a1"),
        ev("TaskCompleted", task_id="1"),
        ev("PreToolUse", tool_name="mcp__ccd_turn__update_plan", subagent=True),
    )
    assert shown(store, now) is State.COMPACTING


def test_replay_of_the_stuck_compacting_seen_on_2026_10_27():
    # 18:45: a workflow subagent compacted its own context under the parent's session id, no
    # PostCompact came, and the pet showed compacting for the whole 600 s cap over real work.
    store, now = run(
        ev("PreToolUse", tool_name="Bash", subagent=True),
        ev("PreCompact", trigger="auto", subagent=True),
        ev("PostToolUse", tool_name="Bash", subagent=True),
        ev("PostToolBatch", subagent=True),
        ev("PreToolUse", tool_name="Grep", subagent=True),
        ev("PostToolUseFailure", tool_name="Grep", subagent=True),
        ev("PostToolBatch", subagent=True),
    )
    # Subagent events now hold the parent at delegating and never flash its error.
    assert shown(store, now) is State.DELEGATING
    assert all(shown(store, now + t) is not State.COMPACTING for t in range(0, int(COMPACT_CAP_S), 30))


def test_compacting_lapses_if_post_compact_never_arrives():
    store, now = run(ev("UserPromptSubmit"), ev("PreCompact", trigger="auto"))
    compacted_at = now - 1.0
    assert shown(store, compacted_at + COMPACT_CAP_S - 1) is State.COMPACTING
    assert shown(store, compacted_at + COMPACT_CAP_S) is State.THINKING


def test_post_model_switch_updates_model_only():
    store, now = run(ev("SessionStart", source="startup", model="Sonnet"), ev("UserPromptSubmit"))
    store, now = run(ev("PostModelSwitch", to_model="claude-opus-5-5"), store=store, start=now)
    display = project(store, now)
    assert display.model == "claude-opus-5-5"
    assert display.state is State.THINKING


def test_session_end_removes_session():
    store, now = run(ev("SessionStart", source="startup"), ev("SessionEnd", reason="prompt_input_exit"))
    assert "s1" not in store.sessions
    assert project(store, now).sessions == 0


def test_effort_and_permission_mode_are_kept():
    store, now = run(ev("PreToolUse", tool_name="Read", effort="high", permission_mode="acceptEdits"))
    display = project(store, now)
    assert (display.effort, display.permission_mode) == ("high", "acceptEdits")


def test_unknown_event_only_refreshes_activity():
    store, now = run(ev("UserPromptSubmit"), ev("CwdChanged"))
    assert shown(store, now) is State.THINKING
    assert store.sessions["s1"].last_active == now - 1.0


def test_event_without_session_id_is_ignored():
    store, now = run({"hook_event_name": "UserPromptSubmit"}, ev("UserPromptSubmit", sid=""))
    assert store.sessions == {}


# --- priority order ------------------------------------------------------------------


def test_priority_order_matches_plan():
    assert [s.value for s in PRIORITY] == [
        "waiting", "error", "compacting", "running", "editing",
        "searching", "delegating", "thinking", "done", "idle",
    ]  # fmt: skip


def test_effective_state_takes_highest_ranked_of_base_and_overlays():
    rec = SessionRecord("s", state=State.EDITING, overlays={State.DONE: T0 + 10, State.ERROR: T0 + 10})
    assert rec.effective_state(T0) is State.ERROR
    rec.overlays.pop(State.ERROR)
    assert rec.effective_state(T0) is State.EDITING
    rec.state = State.IDLE
    assert rec.effective_state(T0) is State.DONE
    assert rec.effective_state(T0 + 10) is State.IDLE


def test_waiting_beats_error_flash_in_one_session():
    store, now = run(ev("PostToolUseFailure", tool_name="Bash"), ev("PermissionRequest", tool_name="Bash"), step=0.1)
    assert shown(store, now) is State.WAITING


# --- expiry --------------------------------------------------------------------------


def test_session_expires_after_30_minutes_without_events():
    store, now = run(ev("UserPromptSubmit"))
    last = now - 1.0
    assert project(store, last + SESSION_TTL_S - 1).sessions == 1
    display = project(store, last + SESSION_TTL_S)
    assert display.sessions == 0
    assert display.state is State.IDLE


def test_reduce_prunes_expired_sessions():
    store, now = run(ev("UserPromptSubmit", sid="old"))
    store, _ = run(ev("UserPromptSubmit", sid="new"), store=store, start=now + SESSION_TTL_S)
    assert set(store.sessions) == {"new"}


def test_status_reports_keep_a_quiet_session_alive():
    store, now = run(ev("Stop"))
    store, now = run(ev(STATUS_EVENT, model="Opus"), store=store, start=now + SESSION_TTL_S - 10)
    assert project(store, now + 60).sessions == 1


# --- multiple sessions ---------------------------------------------------------------


def test_most_recently_active_session_is_shown_with_count():
    store, now = run(
        ev("PreToolUse", sid="a", tool_name="Edit", project="alpha"),
        ev("PreToolUse", sid="b", tool_name="Bash", project="beta"),
    )
    display = project(store, now)
    assert (display.session_id, display.project, display.state) == ("b", "beta", State.RUNNING)
    assert display.sessions == 2


def test_waiting_wins_across_sessions_even_when_older():
    store, now = run(
        ev("PermissionRequest", sid="a", tool_name="Bash", project="alpha"),
        ev("PreToolUse", sid="b", tool_name="Edit", project="beta"),
        ev("PostToolUseFailure", sid="b", tool_name="Edit"),
    )
    display = project(store, now)
    assert (display.session_id, display.state) == ("a", State.WAITING)
    assert display.waiting_sessions == 1


def test_most_recent_waiting_session_wins_among_several():
    store, now = run(
        ev("PermissionRequest", sid="a"),
        ev("Notification", sid="b", notification_type="permission_prompt"),
        ev("PreToolUse", sid="c", tool_name="Read"),
    )
    display = project(store, now)
    assert display.session_id == "b"
    assert display.waiting_sessions == 2


def test_status_refresh_does_not_reorder_sessions():
    store, now = run(ev("PreToolUse", sid="a", tool_name="Edit"), ev("PreToolUse", sid="b", tool_name="Read"))
    store, now = run(ev(STATUS_EVENT, sid="a", cost_usd=0.5), store=store, start=now)
    assert project(store, now).session_id == "b"


def test_ending_one_session_shows_the_other():
    store, now = run(ev("PreToolUse", sid="a", tool_name="Edit"), ev("UserPromptSubmit", sid="b"), ev("SessionEnd", sid="b"))
    display = project(store, now)
    assert (display.session_id, display.state, display.sessions) == ("a", State.EDITING, 1)


# --- status line ---------------------------------------------------------------------


def test_status_sets_quota_cost_context_and_labels():
    store, now = run(
        ev(
            STATUS_EVENT,
            model="Opus",
            effort="high",
            session_name="refactor",
            project="demo-project",
            context_pct=12.0,
            cost_usd=0.42,
            five_hour_pct=23.0,
            five_hour_resets_at=1761600000,
            seven_day_pct=41.0,
            seven_day_resets_at=1762000000,
        )
    )
    display = project(store, now)
    assert (display.model, display.effort, display.project) == ("Opus", "high", "demo-project")
    assert (display.context_pct, display.cost_usd) == (12.0, 0.42)
    assert (display.quota.five_hour_pct, display.quota.seven_day_pct) == (23.0, 41.0)
    assert display.quota.five_hour_resets_at == 1761600000
    assert display.quota.seen


def test_session_name_is_not_kept_anywhere():
    store, now = run(ev(STATUS_EVENT, session_name="refactor the login flow", model="Opus"))
    for cls in (SessionRecord, DisplayState):
        assert "session_name" not in {f.name for f in dataclasses.fields(cls)}
    assert "refactor" not in repr(store)
    assert "refactor" not in repr(project(store, now))


def test_status_seen_is_set_by_status_reports_only():
    store, now = run(ev("SessionStart", source="startup"), ev("UserPromptSubmit"), ev("Stop"))
    assert store.sessions["s1"].status_seen is False
    store, now = run(ev(STATUS_EVENT), store=store, start=now)
    assert store.sessions["s1"].status_seen is True
    store, now = run(ev("UserPromptSubmit"), ev("SessionStart", source="clear"), store=store, start=now)
    assert store.sessions["s1"].status_seen is True


def test_receipt_stamp_is_never_stored():
    plain, now = run(ev("PreToolUse", tool_name="Read"), ev(STATUS_EVENT, model="Opus", five_hour_pct=5.0))
    stamped, _ = run(
        ev("PreToolUse", tool_name="Read", _rx=12.5),
        ev(STATUS_EVENT, model="Opus", five_hour_pct=5.0, _rx=13.5),
    )
    assert stamped == plain
    assert "_rx" not in repr(stamped)
    assert project(stamped, now) == project(plain, now)


def test_status_without_rate_limits_keeps_quota_and_shows_cost():
    store, now = run(ev(STATUS_EVENT, five_hour_pct=10.0), ev(STATUS_EVENT, cost_usd=1.25))
    display = project(store, now)
    assert display.quota.five_hour_pct == 10.0
    assert display.cost_usd == 1.25


def test_quota_is_account_wide_across_sessions():
    store, now = run(ev(STATUS_EVENT, sid="a", five_hour_pct=30.0), ev("UserPromptSubmit", sid="b"))
    display = project(store, now)
    assert display.session_id == "b"
    assert display.quota.five_hour_pct == 30.0


def test_context_goes_null_after_compact():
    store, now = run(ev(STATUS_EVENT, context_pct=80.0), ev(STATUS_EVENT, context_pct=None))
    assert project(store, now).context_pct is None


def test_status_does_not_change_state():
    store, now = run(ev("PreToolUse", tool_name="Edit"), ev(STATUS_EVENT, model="Opus"))
    assert shown(store, now) is State.EDITING


def test_no_sessions_still_shows_last_quota():
    store, now = run(ev(STATUS_EVENT, five_hour_pct=50.0), ev("SessionEnd"))
    display = project(store, now)
    assert display.sessions == 0
    assert display.quota.five_hour_pct == 50.0


# --- quota freshness -----------------------------------------------------------------

LAPSE = T0 + 100.0


def max_report(**fields):
    """A status report from a subscription: a 5h window lapsing at LAPSE and a later 7d window."""
    quota = {
        "five_hour_pct": 23.0,
        "five_hour_resets_at": LAPSE,
        "seven_day_pct": 41.0,
        "seven_day_resets_at": LAPSE + 86400,
    }
    return ev(STATUS_EVENT, **{**quota, **fields})


def test_fresh_quota_drops_a_lapsed_window_and_keeps_the_rest():
    quota = Quota(23.0, LAPSE, 41.0, LAPSE + 86400, seen=True)
    assert fresh_quota(quota, LAPSE - 0.01) is quota
    assert fresh_quota(quota, LAPSE) == Quota(None, None, 41.0, LAPSE + 86400, seen=True)
    assert fresh_quota(quota, LAPSE + 86400) == Quota(seen=True)


def test_fresh_quota_keeps_windows_without_a_reset_time():
    quota = Quota(five_hour_pct=10.0, seen=True)
    assert fresh_quota(quota, LAPSE) is quota


def test_project_drops_a_lapsed_window_with_a_session():
    store, now = run(max_report())
    assert project(store, now).quota.five_hour_pct == 23.0
    display = project(store, LAPSE)
    assert (display.sessions, display.quota.five_hour_pct, display.quota.five_hour_resets_at) == (1, None, None)
    assert display.quota.seven_day_pct == 41.0
    assert display.quota.seen


def test_project_drops_a_lapsed_window_without_sessions():
    store, now = run(max_report(), ev("SessionEnd"))
    assert project(store, now).quota.five_hour_pct == 23.0
    display = project(store, LAPSE)
    assert display.sessions == 0
    assert (display.quota.five_hour_pct, display.quota.seven_day_pct, display.quota.seen) == (None, 41.0, True)


def test_reduce_drops_a_lapsed_window_from_the_store():
    store, now = run(max_report())
    store, _ = run(ev("UserPromptSubmit"), store=store, start=LAPSE + 1)
    assert store.quota == Quota(None, None, 41.0, LAPSE + 86400, seen=True)


def test_quota_seen_is_set_by_the_first_quota_key_and_survives_the_lapse():
    store, now = run(ev(STATUS_EVENT, cost_usd=0.42, context_pct=3.0))
    assert store.quota.seen is False
    store, now = run(ev(STATUS_EVENT, five_hour_resets_at=LAPSE), store=store, start=now)
    assert store.quota.seen is True
    store, now = run(ev(STATUS_EVENT, cost_usd=0.5), store=store, start=LAPSE + 1)
    assert store.quota == Quota(seen=True)
    assert project(store, now).quota.seen is True


def test_hook_events_never_set_quota_seen():
    store, now = run(ev("SessionStart", source="startup"), ev("UserPromptSubmit"), ev("Stop"))
    assert store.quota == Quota()


def test_new_report_after_a_lapse_brings_the_window_back():
    store, now = run(max_report())
    store, now = run(max_report(five_hour_pct=2.0, five_hour_resets_at=LAPSE + 18000), store=store, start=LAPSE + 1)
    display = project(store, now)
    assert (display.quota.five_hour_pct, display.quota.five_hour_resets_at) == (2.0, LAPSE + 18000)


# --- subagents ----------------------------------------------------------------------


def sub(name, agent="agent-1", **fields):
    """A hook that fired inside a subagent, as the server flags it."""
    return ev(name, agent_id=agent, subagent=True, **fields)


def test_subagent_tools_never_flicker_the_parent_verb():
    store, now = run(ev("UserPromptSubmit"), ev("PreToolUse", tool_name="Agent"))
    for event in (
        sub("PreToolUse", tool_name="Read"),
        sub("PostToolUse", tool_name="Read"),
        sub("PreToolUse", tool_name="Edit"),
        sub("PostToolBatch"),
        sub("PreToolUse", tool_name="Bash"),
    ):
        store, now = run(event, store=store, start=now)
        display = project(store, now)
        assert (display.state, display.tool_name) == (State.DELEGATING, "Agent")


def test_the_parent_own_work_outranks_delegating():
    store, now = run(ev("PreToolUse", tool_name="Bash"), sub("PreToolUse", tool_name="Read"))
    assert shown(store, now) is State.RUNNING
    store, now = run(ev("PostToolBatch"), store=store, start=now)
    assert shown(store, now) is State.DELEGATING, "background subagent still working"


def test_delegating_lapses_after_the_hold_without_subagent_activity():
    store, now = run(ev("UserPromptSubmit"), sub("PreToolUse", tool_name="Read"))
    last = now - 1.0
    assert shown(store, last + SUBAGENT_HOLD_S - 1) is State.DELEGATING
    assert shown(store, last + SUBAGENT_HOLD_S) is State.THINKING


def test_delegating_ends_when_the_last_subagent_stops():
    store, now = run(
        ev("SubagentStart", agent_id="agent-1", agent_type="Explore"),
        ev("SubagentStart", agent_id="agent-2", agent_type="Explore"),
        sub("PreToolUse", tool_name="Read"),
        ev("Stop"),
    )
    assert shown(store, now) is State.DELEGATING, "background work outranks the done hold"
    store, now = run(ev("SubagentStop", agent_id="agent-1"), store=store, start=now)
    assert shown(store, now) is State.DELEGATING
    store, now = run(ev("SubagentStop", agent_id="agent-2"), store=store, start=now)
    assert shown(store, now) is State.DONE, "the done hold shows once the subagents are gone"


def test_subagent_failure_does_not_flash_the_parent_error():
    store, now = run(ev("UserPromptSubmit"), sub("PostToolUseFailure", tool_name="Grep"))
    assert shown(store, now - 0.5) is State.DELEGATING
    assert State.ERROR not in store.sessions["s1"].overlays


def test_subagent_events_do_not_change_the_parent_labels():
    store, now = run(
        ev("UserPromptSubmit", permission_mode="acceptEdits", effort="xhigh", project="app"),
        sub("PreToolUse", tool_name="Read", permission_mode="plan", effort="low", project="elsewhere"),
    )
    display = project(store, now)
    assert (display.permission_mode, display.effort, display.project) == ("acceptEdits", "xhigh", "app")


def test_subagent_activity_keeps_the_session_most_recent():
    store, now = run(ev("UserPromptSubmit", sid="a"), ev("UserPromptSubmit", sid="b"), sub("PreToolUse", sid="a"))
    assert project(store, now).session_id == "a"


def test_tasks_created_by_a_subagent_still_count():
    store, now = run(sub("TaskCreated", task_id="1"), sub("TaskCreated", task_id="2"), sub("TaskCompleted", task_id="1"))
    display = project(store, now)
    assert (display.tasks_completed, display.tasks_created) == (1, 2)


def test_a_subagent_permission_prompt_waits_until_that_subagent_moves_on():
    store, now = run(
        ev("PreToolUse", tool_name="Agent"),
        sub("PreToolUse", tool_name="Bash"),
        sub("PermissionRequest", tool_name="Bash"),
    )
    display = project(store, now)
    assert (display.state, display.waiting_reason) == (State.WAITING, "permission")
    # Neither the parent nor another subagent answers this prompt.
    store, now = run(
        ev("PostToolUse", tool_name="Edit"),
        ev("PostToolBatch"),
        sub("PostToolUse", agent="agent-2", tool_name="Bash"),
        sub("PostToolBatch", agent="agent-2"),
        sub("PostToolUse", tool_name="Read"),  # a sibling tool of the same subagent
        store=store,
        start=now,
    )
    assert shown(store, now) is State.WAITING
    store, now = run(sub("PostToolUse", tool_name="Bash"), store=store, start=now)
    assert shown(store, now) is State.DELEGATING


@pytest.mark.parametrize(
    "ending",
    [sub("PostToolBatch"), sub("PermissionDenied", tool_name="Bash"), sub("PostToolUseFailure", tool_name="Bash"),
     ev("SubagentStop", agent_id="agent-1")],
    ids=["batch", "denied", "failed", "stopped"],
)  # fmt: skip
def test_other_ways_a_subagent_prompt_ends(ending):
    store, now = run(sub("PermissionRequest", tool_name="Bash"), ending)
    assert shown(store, now) is not State.WAITING


def test_a_subagent_question_ends_on_its_next_step():
    store, now = run(sub("Notification", notification_type="elicitation_dialog"))
    display = project(store, now)
    assert (display.state, display.waiting_reason) == (State.WAITING, "elicitation_dialog")
    store, now = run(sub("PreToolUse", agent="agent-2"), store=store, start=now)
    assert shown(store, now) is State.WAITING, "another subagent moving does not answer it"
    store, now = run(sub("PostToolUse", tool_name="mcp__srv__ask"), store=store, start=now)
    assert shown(store, now) is not State.WAITING


def test_a_subagent_wait_wins_across_sessions_and_counts():
    store, now = run(sub("PermissionRequest", sid="a", tool_name="Bash"), ev("PreToolUse", sid="b", tool_name="Edit"))
    display = project(store, now)
    assert (display.session_id, display.state, display.waiting_sessions) == ("a", State.WAITING, 1)


def test_a_new_session_start_clears_subagent_waits():
    store, now = run(sub("PermissionRequest", tool_name="Bash"), ev("SessionStart", source="clear"))
    assert shown(store, now) is State.IDLE


# --- compaction, end to end ----------------------------------------------------------


def test_compact_sequence_shows_compacting_then_returns_and_clears_the_ring():
    store, now = run(ev("PreToolUse", tool_name="Edit"), ev(STATUS_EVENT, context_pct=81.0))
    assert project(store, now).context_pct == 81.0
    store, now = run(ev("PreCompact", trigger="manual"), store=store, start=now)
    assert shown(store, now) is State.COMPACTING
    store, now = run(ev("PostCompact", trigger="manual"), store=store, start=now)
    assert shown(store, now) is State.EDITING
    store, now = run(ev(STATUS_EVENT, context_pct=None), store=store, start=now)
    display = project(store, now)
    assert (display.state, display.context_pct) == (State.EDITING, None)


def test_session_start_compact_clears_compacting_and_keeps_the_rest():
    store, now = run(
        ev("PreToolUse", tool_name="Bash"),
        ev("SubagentStart", agent_id="a1"),
        ev("PreCompact", trigger="auto"),
        ev("SessionStart", source="compact"),
    )
    display = project(store, now)
    assert (display.state, display.tool_name, display.subagents) == (State.RUNNING, "Bash", 1)


# --- timing and purity ---------------------------------------------------------------


def test_next_transition_is_the_earliest_overlay_or_expiry():
    store, now = run(ev("PostToolUseFailure", tool_name="Bash"))
    failed_at = now - 1.0
    assert next_transition(store, failed_at) == failed_at + ERROR_FLASH_S
    assert next_transition(store, failed_at + ERROR_FLASH_S) == failed_at + SESSION_TTL_S
    assert next_transition(Store(), now) is None


def test_next_transition_includes_a_future_quota_reset():
    store, now = run(max_report(), ev("SessionEnd"))
    assert next_transition(store, now) == LAPSE
    assert next_transition(store, LAPSE) == LAPSE + 86400
    assert next_transition(store, LAPSE + 86400) is None


def test_next_transition_ignores_a_past_quota_reset():
    past = Store(quota=Quota(five_hour_pct=9.0, five_hour_resets_at=T0 - 5))
    assert next_transition(past, T0) is None
    store, now = run(ev(STATUS_EVENT, five_hour_pct=9.0, five_hour_resets_at=T0 + 500))
    assert next_transition(store, now) == T0 + 500
    store = dataclasses.replace(store, quota=Quota(five_hour_resets_at=T0 - 5))
    assert next_transition(store, now) == T0 + SESSION_TTL_S


def test_reduce_does_not_mutate_its_input():
    store, now = run(ev("UserPromptSubmit"), ev("SubagentStart", agent_id="a1"), ev("TaskCreated", task_id="1"))
    before = store.sessions["s1"]
    snapshot = (before.state, dict(before.overlays), dict(before.subagents), set(before.tasks_created))
    run(ev("Stop"), ev("SubagentStop", agent_id="a1"), ev("TaskCreated", task_id="2"), ev("SessionEnd"), store=store, start=now)
    assert "s1" in store.sessions
    assert (before.state, before.overlays, before.subagents, before.tasks_created) == snapshot


# --- a whole turn ------------------------------------------------------------------------


def test_edit_then_pytest_turn_shows_the_expected_states_in_order():
    """An edit-then-test turn shows thinking, editing, running and done, in that order, from the real event order."""
    events = [
        ev("UserPromptSubmit"),
        ev("PreToolUse", tool_name="Read"),
        ev("PostToolUse", tool_name="Read"),
        ev("PostToolBatch"),
        ev("PreToolUse", tool_name="Edit"),
        ev("PostToolUse", tool_name="Edit"),
        ev("PostToolBatch"),
        ev("PreToolUse", tool_name="Bash"),
        ev("PostToolUse", tool_name="Bash"),
        ev("PostToolBatch"),
        ev("Stop"),
    ]
    store, now, log = Store(), T0, []
    for event in events:
        store = reduce(store, event, now)
        state = shown(store, now)
        if not log or log[-1] is not state:
            log.append(state)
        now += 1.0
    log.append(shown(store, now + DONE_HOLD_S))

    expected = iter(["thinking", "editing", "thinking", "running", "thinking", "done", "idle"])
    target = next(expected)
    for state in log:
        if state.value == target:
            target = next(expected, None)
    assert target is None, f"log {[s.value for s in log]} misses the expected states"
