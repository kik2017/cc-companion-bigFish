"""Controller wiring: Quit and hide, paint timing and the status line probe.

Menu Quit hides the pet and exits only once no session is live; "Hide until all sessions
end" does the same but no new session brings the pet back.
"""

import dataclasses
import logging
import os
import sys
from types import SimpleNamespace

import pytest

if sys.platform != "win32" and not os.environ.get("DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from companion import __main__ as daemon
from companion.model import SessionRecord
from companion.reducer import SESSION_TTL_S, STATUS_EVENT


class FakeWindow(QObject):
    quit_requested = Signal()
    hide_requested = Signal()

    def __init__(self):
        super().__init__()
        self.visible = True
        self.display = None
        self.displays = []
        self.repaint_result = False
        self.said = []

    def set_display(self, display):
        self.display = display
        self.displays.append(display)
        return self.repaint_result

    def say(self, text, mood, ttl_s, reply=False):
        self.said.append((text, mood, ttl_s) + (("reply",) if reply else ()))

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def clock(monkeypatch):
    now = [1_000_000.0]
    monkeypatch.setattr(daemon, "time", SimpleNamespace(time=lambda: now[0]))
    return now


@pytest.fixture
def controller(app, clock):
    exits = []
    window = FakeWindow()
    paints = []
    server = SimpleNamespace(session_count=0, paints=paints, record_paint=paints.append)
    ctrl = daemon.Controller(window, server, exit_app=lambda: exits.append(clock[0]))
    ctrl.exits = exits
    yield ctrl
    ctrl.timer.stop()


@pytest.fixture
def companion_log(caplog):
    caplog.set_level(logging.INFO, logger="companion")
    return caplog


def messages(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "companion"]


def start(ctrl, session_id, source="startup"):
    ctrl.on_event({"hook_event_name": "SessionStart", "session_id": session_id, "source": source})


def end(ctrl, session_id):
    ctrl.on_event({"hook_event_name": "SessionEnd", "session_id": session_id})


def stop(ctrl, session_id):
    ctrl.on_event({"hook_event_name": "Stop", "session_id": session_id})


def status(ctrl, session_id):
    ctrl.on_event({"hook_event_name": STATUS_EVENT, "session_id": session_id, "context_pct": 12.0})


# --- Quit ---------------------------------------------------------------------------


def test_quit_with_no_sessions_exits_at_once(controller):
    controller.window.quit_requested.emit()
    assert controller.exits
    assert not controller.window.visible


def test_quit_hides_and_keeps_listening_until_last_session_ends(controller):
    start(controller, "a")
    start(controller, "b")
    controller.window.quit_requested.emit()
    assert not controller.window.visible
    assert not controller.exits
    end(controller, "a")
    assert not controller.exits
    assert controller.server.session_count == 1
    end(controller, "b")
    assert len(controller.exits) == 1


def test_showing_her_from_the_tray_calls_off_a_pending_quit_or_hide(controller):
    start(controller, "a")
    controller.window.hide_requested.emit()
    controller.cancel_leaving()
    assert not controller.leaving and not controller.hidden_for_run
    end(controller, "a")
    assert not controller.exits, "no longer leaving once the last session ends"


def test_quit_exits_when_the_last_session_expires(controller, clock):
    start(controller, "a")
    controller.window.quit_requested.emit()
    clock[0] += SESSION_TTL_S + 1
    controller.refresh()  # what the expiry timer calls
    assert controller.exits


@pytest.mark.parametrize("source", ["startup", "resume", "fork"])
def test_a_new_session_brings_a_quit_pet_back(controller, source):
    start(controller, "a")
    controller.window.quit_requested.emit()
    start(controller, "b", source=source)
    assert controller.window.visible
    end(controller, "a")
    end(controller, "b")
    assert not controller.exits, "quit was cancelled, so the pet stays up"


@pytest.mark.parametrize("source", ["clear", "compact"])
def test_clear_or_compact_does_not_bring_it_back(controller, source):
    start(controller, "a")
    controller.window.quit_requested.emit()
    start(controller, "a", source=source)
    assert not controller.window.visible
    end(controller, "a")
    assert controller.exits


def test_display_reaches_the_window(controller):
    start(controller, "a")
    controller.on_event({"hook_event_name": "UserPromptSubmit", "session_id": "a"})
    assert controller.window.display.state.value == "thinking"
    assert controller.server.session_count == 1


def test_a_quota_reset_wakes_the_timer_with_no_session(controller, clock):
    resets_at = clock[0] + 120
    quota = {"five_hour_pct": 23.0, "five_hour_resets_at": resets_at}
    controller.on_event({"hook_event_name": STATUS_EVENT, "session_id": "a", **quota})
    end(controller, "a")
    assert controller.server.session_count == 0
    assert controller.window.display.quota.five_hour_pct == 23.0
    assert controller.timer.isActive()
    assert 120_000 <= controller.timer.interval() <= 120_010


def test_a_far_off_quota_reset_is_clamped_to_the_timer_range(controller, clock):
    # The server accepts resets_at up to 1e12; QTimer.start() would raise past 2**31 - 1 ms.
    controller.on_event({"hook_event_name": STATUS_EVENT, "session_id": "a", "seven_day_resets_at": 1e12})
    end(controller, "a")
    assert controller.timer.isActive()
    assert controller.timer.interval() == daemon.MAX_TIMER_MS


# --- Hide until all sessions end ----------------------------------------------------


def test_hide_stays_hidden_through_new_sessions_and_exits_when_all_end(controller, companion_log):
    start(controller, "a")
    controller.window.hide_requested.emit()
    assert not controller.window.visible
    assert not controller.exits
    assert "hide requested; hidden until live sessions end" in messages(companion_log)
    start(controller, "b")
    assert not controller.window.visible, "a new session does not bring a hidden pet back"
    assert "new session; quit cancelled" not in messages(companion_log)
    end(controller, "a")
    assert not controller.exits
    assert controller.server.session_count == 1
    end(controller, "b")
    assert len(controller.exits) == 1


@pytest.mark.parametrize("source", ["resume", "fork", "clear", "compact"])
def test_hide_ignores_every_session_start_source(controller, source):
    start(controller, "a")
    controller.window.hide_requested.emit()
    start(controller, "b", source=source)
    assert not controller.window.visible
    end(controller, "a")
    end(controller, "b")
    assert len(controller.exits) == 1


def test_hide_with_no_sessions_exits_at_once(controller):
    controller.window.hide_requested.emit()
    assert controller.exits
    assert not controller.window.visible


def test_hide_exits_when_the_last_session_expires(controller, clock):
    start(controller, "a")
    controller.window.hide_requested.emit()
    clock[0] += SESSION_TTL_S + 1
    controller.refresh()
    assert controller.exits


# --- Paint recording ----------------------------------------------------------------


def test_rx_is_popped_before_reduce(controller, monkeypatch):
    seen = []
    real_reduce = daemon.reduce

    def spy(store, event, now):
        seen.append(dict(event))
        return real_reduce(store, event, now)

    monkeypatch.setattr(daemon, "reduce", spy)
    event = {"hook_event_name": "SessionStart", "session_id": "a", "source": "startup", "_rx": 5.0}
    controller.on_event(event)
    assert "_rx" not in seen[0]
    assert "_rx" not in event
    assert set(vars(controller.store.sessions["a"])) == {f.name for f in dataclasses.fields(SessionRecord)}


def test_paint_is_recorded_once_when_the_bubble_repaints(controller, monkeypatch):
    monkeypatch.setattr(daemon, "perf_counter", lambda: 10.0025)
    controller.window.repaint_result = True
    controller.on_event({"hook_event_name": "SessionStart", "session_id": "a", "source": "startup", "_rx": 10.0})
    assert controller.server.paints == [pytest.approx(2.5)]


def test_no_paint_is_recorded_when_the_bubble_did_not_repaint(controller):
    controller.window.repaint_result = False
    controller.on_event({"hook_event_name": "SessionStart", "session_id": "a", "source": "startup", "_rx": 10.0})
    assert controller.server.paints == []


def test_no_paint_is_recorded_without_a_receipt_stamp(controller):
    controller.window.repaint_result = True
    start(controller, "a")  # no _rx, as from an older server
    controller.refresh()  # timer and menu renders carry no event
    controller.window.quit_requested.emit()
    assert controller.server.paints == []


# --- Status line probe --------------------------------------------------------------


def probe_lines(caplog):
    return [m for m in messages(caplog) if m.startswith("no status line report")]


def test_probe_logs_once_after_two_stops_without_a_status_report(controller, companion_log):
    start(controller, "abcdef123456")
    stop(controller, "abcdef123456")
    assert probe_lines(companion_log) == [], "one Stop can come before the first status line run"
    stop(controller, "abcdef123456")
    assert probe_lines(companion_log) == ["no status line report for session=abcdef12"]
    stop(controller, "abcdef123456")
    assert len(probe_lines(companion_log)) == 1


def test_probe_is_silent_for_a_session_with_a_status_report(controller, companion_log):
    start(controller, "a")
    status(controller, "a")
    for _ in range(3):
        stop(controller, "a")
    assert probe_lines(companion_log) == []


def test_probe_counts_each_session_on_its_own(controller, companion_log):
    start(controller, "a")
    start(controller, "b")
    status(controller, "b")
    stop(controller, "a")
    stop(controller, "b")
    stop(controller, "b")
    assert probe_lines(companion_log) == []
    stop(controller, "a")
    assert probe_lines(companion_log) == ["no status line report for session=a"]


def test_probe_forgets_a_session_that_ends(controller):
    start(controller, "a")
    stop(controller, "a")
    stop(controller, "a")
    start(controller, "b")
    end(controller, "a")
    assert controller._stops == {} and controller._probed == set()


# --- Compact ------------------------------------------------------------------------


def test_compact_shows_compacting_then_the_previous_state(controller, companion_log):
    start(controller, "a")
    controller.on_event({"hook_event_name": "UserPromptSubmit", "session_id": "a"})
    controller.on_event({"hook_event_name": "PreCompact", "session_id": "a", "trigger": "auto"})
    assert controller.window.display.state.value == "compacting"
    controller.on_event({"hook_event_name": "PostCompact", "session_id": "a", "trigger": "auto"})
    assert controller.window.display.state.value == "thinking"
    states = [d.state.value for d in controller.window.displays]
    assert states[-3:] == ["thinking", "compacting", "thinking"]
    lines = [m for m in messages(companion_log) if m.startswith("state ")]
    assert lines[-2:] == ["state compacting session=a", "state thinking session=a"]


def test_manual_compact_returns_to_idle(controller, companion_log):
    start(controller, "a")
    controller.on_event({"hook_event_name": "PreCompact", "session_id": "a", "trigger": "manual"})
    controller.on_event({"hook_event_name": "PostCompact", "session_id": "a", "trigger": "manual"})
    start(controller, "a", source="compact")
    assert controller.window.display.state.value == "idle"
    lines = [m for m in messages(companion_log) if m.startswith("state ")]
    assert lines == ["state idle session=a", "state compacting session=a", "state idle session=a"]


# --- events feed ----------------------------------------------------------------------


class FakeFeed:
    def __init__(self):
        self.events, self.displays = [], []

    def publish_event(self, event):
        self.events.append(dict(event))

    def publish_display(self, display):
        self.displays.append(display)


def test_the_feed_gets_each_event_without_its_receipt_stamp_and_each_display(app, clock):
    feed = FakeFeed()
    server = SimpleNamespace(session_count=0, record_paint=lambda ms: None)
    ctrl = daemon.Controller(FakeWindow(), server, exit_app=lambda: None, feed=feed)
    ctrl.on_event({"hook_event_name": "SessionStart", "session_id": "a", "source": "startup", "_rx": 1.0})
    ctrl.on_event({"hook_event_name": "UserPromptSubmit", "session_id": "a"})
    assert feed.events == [
        {"hook_event_name": "SessionStart", "session_id": "a", "source": "startup"},
        {"hook_event_name": "UserPromptSubmit", "session_id": "a"},
    ]
    assert [d.state.value for d in feed.displays] == ["idle", "thinking"]
    ctrl.timer.stop()


def test_no_feed_is_the_default(controller):
    assert controller.feed is None
    start(controller, "a")  # and nothing tries to publish

def test_a_line_from_say_reaches_the_window(controller):
    controller.say_received.emit({"text": "hi", "mood": "happy", "ttl_s": 6.0})
    assert controller.window.said == [("hi", "happy", 6.0)]


def test_a_reply_from_say_reaches_the_window_as_a_reply(controller):
    controller.say_received.emit({"text": "hi", "mood": "happy", "ttl_s": 15.0, "reply": True})
    assert controller.window.said == [("hi", "happy", 15.0, "reply")]
