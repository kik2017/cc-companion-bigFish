import pytest

from companion.bubble_text import (
    ATTENTION_STATES,
    Bar,
    BubbleText,
    bubble_text,
    compose,
    countdown,
    finite,
    level,
    next_tick,
    title_for,
    visible,
)
from companion.model import DisplayState, Quota, State

NOW = 1_761_600_000.0


def live(**fields):
    """A display with one live session unless the test says otherwise."""
    return DisplayState(**{"sessions": 1, **fields})


def text(display, *, show_subagents=True, now=NOW):
    return bubble_text(display, show_subagents=show_subagents, now=now)


def max_quota(**fields):
    """Both windows fresh, as a subscription reports them."""
    base = dict(
        five_hour_pct=23.0,
        five_hour_resets_at=NOW + 8040,
        seven_day_pct=41.0,
        seven_day_resets_at=NOW + 273600,
        seen=True,
    )
    return Quota(**{**base, **fields})


# --- titles, one test per row of the title table ----------------------------------------


def test_no_session_reads_no_live_session_whatever_the_state():
    assert title_for(DisplayState()) == "No live session"
    assert title_for(DisplayState(state=State.WAITING, waiting_reason="permission")) == "No live session"


@pytest.mark.parametrize("count", [2, 3])
def test_several_waiting_sessions_are_counted(count):
    display = live(state=State.WAITING, waiting_reason="permission", sessions=4, waiting_sessions=count)
    assert title_for(display) == f"{count} sessions need you"


def test_one_waiting_session_reads_its_reason():
    display = live(state=State.WAITING, waiting_reason="permission", sessions=2, waiting_sessions=1)
    assert title_for(display) == "Needs your permission"


@pytest.mark.parametrize(
    ("state", "title"),
    [
        (State.IDLE, "Ready"),
        (State.THINKING, "Thinking"),
        (State.RUNNING, "Running a command"),
        (State.DONE, "Done"),
        (State.COMPACTING, "Compacting context"),
    ],
)
def test_state_titles(state, title):
    assert title_for(live(state=state)) == title
    assert title_for(live(state=state, tool_name="Bash")) == title


@pytest.mark.parametrize(
    ("tool", "title"),
    [
        ("Read", "Reading files"),
        ("Grep", "Searching the code"),
        ("Glob", "Searching the code"),
        ("WebSearch", "Searching the web"),
        ("WebFetch", "Reading a web page"),
        (None, "Searching"),
    ],
)
def test_searching_titles(tool, title):
    assert title_for(live(state=State.SEARCHING, tool_name=tool)) == title


@pytest.mark.parametrize(
    ("tool", "title"),
    [
        ("Write", "Writing a file"),
        ("NotebookEdit", "Editing a notebook"),
        ("Edit", "Editing"),
        ("MultiEdit", "Editing"),
        (None, "Editing"),
    ],
)
def test_editing_titles(tool, title):
    assert title_for(live(state=State.EDITING, tool_name=tool)) == title


@pytest.mark.parametrize("tool", ["Bash", "PowerShell", None])
def test_running_title(tool):
    assert title_for(live(state=State.RUNNING, tool_name=tool)) == "Running a command"


@pytest.mark.parametrize(
    ("tool", "title"),
    [("Workflow", "Running a workflow"), ("Agent", "Delegating"), ("Task", "Delegating"), (None, "Delegating")],
)
def test_delegating_titles(tool, title):
    assert title_for(live(state=State.DELEGATING, tool_name=tool)) == title


@pytest.mark.parametrize(
    ("reason", "title"),
    [
        ("permission", "Needs your permission"),
        ("permission_prompt", "Needs your permission"),
        ("agent_needs_input", "Needs your answer"),
        ("elicitation_dialog", "Has a question for you"),
        ("elicitation_url_dialog", "Has a question for you"),
        ("something_new", "Needs you"),
        (None, "Needs you"),
    ],
)
def test_waiting_titles(reason, title):
    assert title_for(live(state=State.WAITING, waiting_reason=reason, waiting_sessions=1)) == title


@pytest.mark.parametrize(
    ("reason", "title"),
    [
        (None, "A tool failed"),
        ("error", "Stopped on an error"),
        ("rate_limit", "Stopped: rate limit"),
        ("authentication_failed", "Stopped: authentication failed"),
        ("billing_error", "Stopped: billing error"),
    ],
)
def test_error_titles(reason, title):
    assert title_for(live(state=State.ERROR, error_reason=reason)) == title


@pytest.mark.parametrize("state", list(State))
def test_every_state_has_a_title_and_its_tone(state):
    result = text(live(state=state))
    assert result.title
    assert result.tone == state.value


def test_tone_is_none_without_a_session():
    assert text(DisplayState()).tone == "none"


def test_title_never_carries_tool_input():
    # Only the tool name reaches the display, and only known names change the title.
    assert title_for(live(state=State.THINKING, tool_name="TodoWrite")) == "Thinking"
    assert title_for(live(state=State.SEARCHING, tool_name="mcp__x__read")) == "Searching"


# --- labels -------------------------------------------------------------------------------


def test_labels_join_project_model_and_effort():
    assert text(live(project="garden", model="Opus", effort="high")).labels == "garden · Opus · high effort"


@pytest.mark.parametrize(
    ("fields", "labels"),
    [
        ({"model": "Opus", "effort": "high"}, "Opus · high effort"),
        ({"project": "garden", "effort": "high"}, "garden · high effort"),
        ({"project": "garden", "model": "Opus"}, "garden · Opus"),
        ({"project": "garden"}, "garden"),
        ({}, None),
    ],
)
def test_labels_leave_out_each_missing_field(fields, labels):
    assert text(live(**fields)).labels == labels


def test_labels_count_sessions_only_when_more_than_one():
    assert text(live(project="garden", model="Opus", sessions=2)).labels == "garden · Opus · 2 sessions"
    assert text(live(sessions=3)).labels == "3 sessions"
    assert "session" not in text(live(project="garden", sessions=1)).labels


def test_no_session_has_no_labels_or_counters():
    result = text(DisplayState())
    assert result.labels is None
    assert result.counters is None


# --- counters -----------------------------------------------------------------------------


@pytest.mark.parametrize(("count", "counters"), [(0, None), (1, "1 subagent"), (2, "2 subagents")])
def test_subagent_counter(count, counters):
    assert text(live(subagents=count)).counters == counters


def test_subagent_counter_hidden_when_the_setting_is_off():
    assert text(live(subagents=2), show_subagents=False).counters is None


def test_tasks_counter_shows_done_of_created():
    assert text(live(tasks_created=5, tasks_completed=3)).counters == "3/5 tasks"
    assert text(live(tasks_created=2, tasks_completed=0)).counters == "0/2 tasks"


def test_tasks_counter_hidden_until_a_task_is_created():
    assert text(live(tasks_created=0, tasks_completed=0)).counters is None


@pytest.mark.parametrize(
    ("mode", "counters"),
    [
        ("plan", "plan mode"),
        ("acceptEdits", "auto-accept edits"),
        ("bypassPermissions", "bypassing permissions"),
        ("default", None),
        (None, None),
    ],
)
def test_permission_mode_tags(mode, counters):
    assert text(live(permission_mode=mode)).counters == counters


def test_counters_join_in_order():
    display = live(permission_mode="plan", subagents=2, tasks_created=5, tasks_completed=3)
    assert text(display).counters == "plan mode · 2 subagents · 3/5 tasks"
    assert text(display, show_subagents=False).counters == "plan mode · 3/5 tasks"


# --- context ring ---------------------------------------------------------------------------


@pytest.mark.parametrize("pct", [None, float("nan"), float("inf"), float("-inf"), True, "42"])
def test_ring_hidden_without_a_finite_number(pct):
    result = text(live(context_pct=pct))
    assert result.ring_pct is None
    assert result.ring_level is None


@pytest.mark.parametrize(
    ("pct", "shown", "band"),
    [(42, 42.0, "ok"), (75.5, 75.5, "warn"), (150, 100.0, "high"), (-5, 0.0, "ok"), (0, 0.0, "ok")],
)
def test_ring_clamps_to_0_100_and_takes_its_level(pct, shown, band):
    result = text(live(context_pct=pct))
    assert result.ring_pct == shown
    assert result.ring_level == band


@pytest.mark.parametrize(
    ("pct", "band"),
    [(0, "ok"), (69.9, "ok"), (70, "warn"), (89.9, "warn"), (90, "high"), (100, "high")],
)
def test_level_thresholds(pct, band):
    assert level(pct) == band


# --- footer: quota bars or the cost line ----------------------------------------------------------


def test_fresh_windows_give_bars_and_no_cost():
    result = text(live(quota=max_quota(), cost_usd=0.42))
    assert result.bars == (Bar("5h", 23.0, "ok", "2h 14m"), Bar("7d", 41.0, "ok", "3d 4h"))
    assert result.cost is None


def test_bars_take_the_level_colour_and_clamp():
    quota = max_quota(five_hour_pct=75, seven_day_pct=130)
    assert [(bar.pct, bar.level) for bar in text(live(quota=quota)).bars] == [(75.0, "warn"), (100.0, "high")]


def test_bar_without_a_reset_time_has_no_countdown():
    quota = Quota(five_hour_pct=23.0, seen=True)
    assert text(live(quota=quota)).bars == (Bar("5h", 23.0, "ok", None),)


def test_only_windows_with_a_finite_pct_give_bars():
    quota = max_quota(five_hour_pct=None, seven_day_pct=float("nan"))
    assert text(live(quota=quota)).bars == ()
    assert [bar.label for bar in text(live(quota=max_quota(five_hour_pct=None))).bars] == ["7d"]


def test_a_window_past_its_reset_gives_no_bar_even_if_not_yet_dropped():
    # The tick timer redraws a display projected earlier; a window may have lapsed since.
    quota = max_quota(five_hour_resets_at=NOW - 1)
    assert [bar.label for bar in text(live(quota=quota)).bars] == ["7d"]
    assert text(live(quota=max_quota(five_hour_resets_at=NOW)), now=NOW).bars[0].label == "7d"


def test_seen_quota_with_every_window_lapsed_shows_neither_bars_nor_cost():
    lapsed = max_quota(five_hour_resets_at=NOW - 60, seven_day_resets_at=NOW - 1)
    dropped = Quota(seen=True)  # as fresh_quota leaves it
    for quota in (lapsed, dropped):
        result = text(live(quota=quota, cost_usd=0.42))
        assert result.bars == ()
        assert result.cost is None


def test_cost_line_when_no_quota_was_ever_seen():
    assert text(live(cost_usd=0.42)).cost == "$0.42 this session (est.)"
    assert text(live(cost_usd=12.345)).cost == "$12.35 this session (est.)"


@pytest.mark.parametrize("cost", [0, 0.0, None, -1.0, float("nan"), float("inf"), True])
def test_no_cost_line_without_a_positive_finite_cost(cost):
    assert text(live(cost_usd=cost)).cost is None


def test_bars_without_a_session_show_under_no_live_session():
    result = text(DisplayState(quota=max_quota()))
    assert result.title == "No live session"
    assert len(result.bars) == 2


# --- countdown ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "shown"),
    [
        (-5, "<1m"),
        (0, "<1m"),
        (59, "<1m"),
        (59.99, "<1m"),
        (60, "1m"),
        (3599, "59m"),
        (3600, "1h 00m"),
        (3660, "1h 01m"),
        (8040, "2h 14m"),
        (8099.9, "2h 14m"),
        (86399, "23h 59m"),
        (86400, "1d 0h"),
        (273600, "3d 4h"),
        (604800, "7d 0h"),
    ],
)
def test_countdown(seconds, shown):
    assert countdown(seconds) == shown


# --- next_tick ------------------------------------------------------------------------------


def test_next_tick_is_none_without_bars():
    assert next_tick(live(), NOW) is None
    assert next_tick(live(quota=Quota(seen=True)), NOW) is None


def test_next_tick_is_none_when_no_bar_has_a_countdown():
    assert next_tick(live(quota=Quota(five_hour_pct=23.0, seven_day_pct=41.0, seen=True)), NOW) is None


def test_next_tick_lands_just_after_the_minute_rollover():
    display = live(quota=max_quota(five_hour_resets_at=NOW + 8040.5, seven_day_pct=None))
    tick = next_tick(display, NOW)
    assert tick == pytest.approx(NOW + 0.55)
    before = text(display, now=tick - 0.1).bars[0].note
    assert text(display).bars[0].note == before == "2h 14m"
    assert text(display, now=tick).bars[0].note == "2h 13m"


def test_next_tick_on_an_exact_minute_is_just_after_now():
    # At exactly 2h 14m left the next instant reads 2h 13m, so the tick must not wait a minute.
    display = live(quota=max_quota(five_hour_resets_at=NOW + 8040, seven_day_pct=None))
    tick = next_tick(display, NOW)
    assert tick == pytest.approx(NOW + 0.05)
    assert text(display).bars[0].note == "2h 14m"
    assert text(display, now=tick).bars[0].note == "2h 13m"


def test_next_tick_takes_the_earliest_countdown():
    quota = max_quota(five_hour_resets_at=NOW + 8040 + 40, seven_day_resets_at=NOW + 273600 + 10)
    assert next_tick(live(quota=quota), NOW) == pytest.approx(NOW + 10.05)


def test_next_tick_under_a_minute_lands_after_the_reset():
    display = live(quota=max_quota(five_hour_resets_at=NOW + 30, seven_day_pct=None))
    tick = next_tick(display, NOW)
    assert tick == pytest.approx(NOW + 30.05)
    assert text(display, now=tick).bars == ()


def test_next_tick_ignores_lapsed_windows():
    quota = max_quota(five_hour_resets_at=NOW - 5, seven_day_resets_at=NOW + 120 + 20)
    assert next_tick(live(quota=quota), NOW) == pytest.approx(NOW + 20.05)


def test_text_is_equal_between_rollovers():
    # The window repaints only when the text changes, so seconds passing must not change it.
    quota = max_quota(five_hour_resets_at=NOW + 8040.5, seven_day_resets_at=NOW + 273600 + 30)
    display = live(quota=quota, project="garden", context_pct=42.0)
    assert text(display, now=NOW) == text(display, now=NOW + 0.4)
    assert text(display, now=NOW) != text(display, now=NOW + 0.6)


# --- visible ----------------------------------------------------------------------------------


@pytest.mark.parametrize("state", list(State))
def test_visible_modes_by_state(state):
    display = live(state=state)
    shown = text(display)
    assert visible("always", display, shown) is True
    assert visible("attention", display, shown) is (state in ATTENTION_STATES)
    assert visible("hidden", display, shown) is False


def test_attention_states():
    assert ATTENTION_STATES == {State.WAITING, State.ERROR, State.DONE}


def test_always_without_a_session_needs_bars():
    empty = DisplayState()
    assert visible("always", empty, text(empty)) is False
    with_bars = DisplayState(quota=max_quota())
    assert visible("always", with_bars, text(with_bars)) is True


def test_always_without_a_session_hides_once_the_bars_lapse():
    display = DisplayState(quota=max_quota(five_hour_resets_at=NOW - 1, seven_day_resets_at=NOW - 1))
    assert visible("always", display, text(display)) is False


def test_attention_and_hidden_ignore_bars_without_a_session():
    display = DisplayState(quota=max_quota())
    assert visible("attention", display, text(display)) is False
    assert visible("hidden", display, text(display)) is False


def test_unknown_mode_is_hidden():
    display = live(state=State.WAITING)
    assert visible("sometimes", display, text(display)) is False


# --- finite and the whole value ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "result"),
    [
        (0, 0.0),
        (23.5, 23.5),
        (-1, -1.0),
        (10**400, None),
        (float("nan"), None),
        (float("inf"), None),
        (True, None),
        (False, None),
        ("1", None),
        (None, None),
    ],
)
def test_finite(value, result):
    assert finite(value) == result
    assert result is None or type(finite(value)) is float


def test_full_bubble_for_a_subscription_session():
    display = live(
        state=State.EDITING,
        tool_name="Write",
        project="garden",
        model="Opus",
        effort="high",
        permission_mode="acceptEdits",
        subagents=1,
        tasks_created=4,
        tasks_completed=1,
        context_pct=12.4,
        cost_usd=1.5,
        quota=max_quota(),
        sessions=2,
    )
    assert text(display) == BubbleText(
        tone="editing",
        title="Writing a file",
        ring_pct=12.4,
        ring_level="ok",
        labels="garden · Opus · high effort · 2 sessions",
        counters="auto-accept edits · 1 subagent · 1/4 tasks",
        bars=(Bar("5h", 23.0, "ok", "2h 14m"), Bar("7d", 41.0, "ok", "3d 4h")),
        cost=None,
    )


def test_full_bubble_for_an_api_key_session_without_a_status_line():
    # Hooks only: no ring, no cost, never 0% or $0.00.
    display = live(state=State.THINKING, project="garden", model="claude-opus")
    assert text(display) == BubbleText(
        tone="thinking",
        title="Thinking",
        ring_pct=None,
        ring_level=None,
        labels="garden · claude-opus",
        counters=None,
        bars=(),
        cost=None,
    )

# --- her line ---------------------------------------------------------------------------


def test_compose_without_a_line_is_the_status_or_nothing():
    shown = text(live(state=State.THINKING))
    assert compose("always", live(state=State.THINKING), shown, None) == shown
    assert compose("attention", live(state=State.THINKING), shown, None) is None
    assert compose("hidden", live(state=State.WAITING), text(live(state=State.WAITING)), None) is None


def test_compose_puts_her_line_over_a_shown_status():
    display = live(state=State.DONE)
    composed = compose("attention", display, text(display), "Nice.")
    assert (composed.speech, composed.status, composed.title) == ("Nice.", True, "Done")


@pytest.mark.parametrize("mode, display", [("attention", live(state=State.THINKING)), ("always", DisplayState())])
def test_compose_shows_her_line_alone_when_the_status_would_hide(mode, display):
    composed = compose(mode, display, text(display), "Morning!")
    assert (composed.speech, composed.status) == ("Morning!", False)


def test_compose_hidden_mode_hides_her_line_too():
    display = live(state=State.WAITING)
    assert compose("hidden", display, text(display), "hello") is None


def test_bubble_text_has_no_line_of_its_own():
    assert (text(live()).speech, text(live()).status) == (None, True)
