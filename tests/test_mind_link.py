"""The daemon's link to the persona's mind, with a stand-in child process: no API, no window."""

import json
import sys
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from companion import __main__ as daemon
from companion import mind_link
from companion.mind_link import MindLink

# Copies stdin to the file named in argv[1] and exits at end of input, or with argv[2] at once.
CHILD = """
import sys
if len(sys.argv) > 2:
    sys.exit(int(sys.argv[2]))
with open(sys.argv[1], "a", encoding="utf-8") as out:
    for line in sys.stdin:
        out.write(line)
        out.flush()
"""


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def make_link(app, tmp_path):
    links = []

    def build(*extra):
        sink = tmp_path / "received.jsonl"
        link = MindLink("token-123", 47999, program=sys.executable, args=["-c", CHILD, str(sink), *extra])
        link.sink = sink
        links.append(link)
        return link

    yield build
    for link in links:
        link.mode = "off"
        link.stop()


def received(link):
    return [json.loads(line) for line in link.sink.read_text(encoding="utf-8").splitlines()]


def test_on_starts_the_mind_and_tells_it_the_mode(make_link):
    link = make_link()
    link.set_mode("on")
    assert link.running()
    link.send({"type": "event", "event": {"hook_event_name": "Stop", "session_id": "s"}})
    link.stop()
    assert not link.running()
    messages = received(link)
    assert messages[0] == {"type": "persona", "mode": "on"}
    assert messages[-1] == {"type": "event", "event": {"hook_event_name": "Stop", "session_id": "s"}}


def test_quiet_switch_is_sent_and_off_stops_it(make_link):
    link = make_link()
    link.set_mode("on")
    link.set_mode("quiet")
    assert link.running()
    link.set_mode("off")
    assert not link.running()
    assert {"type": "persona", "mode": "quiet"} in received(link)


def test_nothing_is_sent_while_off(make_link):
    link = make_link()
    link.send({"type": "pat", "zone": "head"})
    assert not link.sink.exists()


def test_the_child_gets_the_say_token_and_port(make_link, monkeypatch, tmp_path):
    out = tmp_path / "env.json"
    script = f"import json, os; json.dump({{k: os.environ.get(k) for k in ('CC_COMPANION_SAY_TOKEN', 'CC_COMPANION_PORT')}}, open({str(out)!r}, 'w'))"
    link = MindLink("token-123", 47999, program=sys.executable, args=["-c", script])
    link.set_mode("on")
    link.process.waitForFinished(5000)
    link.mode = "off"
    assert json.loads(out.read_text()) == {"CC_COMPANION_SAY_TOKEN": "token-123", "CC_COMPANION_PORT": "47999"}


def test_not_configured_is_not_restarted(make_link):
    link = make_link("3")
    link.set_mode("on")
    link.process.waitForFinished(5000)
    QApplication.processEvents()
    assert not link.restart_timer.isActive()


def test_a_crash_restarts_a_few_times_then_gives_up(make_link, monkeypatch):
    link = make_link("1")
    link.set_mode("on")
    for _ in range(mind_link.MAX_RESTARTS):
        link.process.waitForFinished(5000)
        QApplication.processEvents()
        assert link.restart_timer.isActive()
        link.restart_timer.stop()
        link._start()
    link.process.waitForFinished(5000)
    QApplication.processEvents()
    assert not link.restart_timer.isActive()


# --- the controller feeds it -------------------------------------------------------------------


class FakeWindow(QObject):
    quit_requested = Signal()
    hide_requested = Signal()
    patted = Signal(str)
    persona_changed = Signal(str)
    chattiness_changed = Signal(str)
    chat_sent = Signal(str)
    private_changed = Signal(object)
    no_gap_changed = Signal(bool)
    limit_changed = Signal(object)
    search_changed = Signal(object)

    def set_display(self, display):
        return False

    def show(self):
        pass

    def hide(self):
        pass


class FakeMind:
    def __init__(self):
        self.sent, self.modes, self.live = [], [], True

    def send(self, message):
        self.sent.append(message)

    def set_mode(self, mode):
        self.modes.append(mode)

    def running(self):
        return self.live


@pytest.fixture
def controller(app):
    mind = FakeMind()
    grants, intervals = [], []
    server = SimpleNamespace(
        session_count=0, record_paint=lambda ms: None, grant_reply=lambda: grants.append(1), grants=grants,
        set_say_interval=intervals.append, intervals=intervals,
    )
    ctrl = daemon.Controller(FakeWindow(), server, exit_app=lambda: None, mind=mind)
    yield ctrl
    ctrl.timer.stop()


def test_events_pats_and_persona_reach_the_mind(controller):
    mind = controller.mind
    controller.on_event({"hook_event_name": "UserPromptSubmit", "session_id": "abc", "_rx": 1.0})
    assert {"type": "event", "event": {"hook_event_name": "UserPromptSubmit", "session_id": "abc"}} in mind.sent
    controller.window.patted.emit("tail")
    assert mind.sent[-1] == {"type": "pat", "zone": "tail"}
    controller.window.persona_changed.emit("quiet")
    assert mind.modes == ["quiet"]


def test_the_display_is_sent_once_per_change(controller):
    mind = controller.mind
    controller.on_event({"hook_event_name": "UserPromptSubmit", "session_id": "abc"})
    displays = [m for m in mind.sent if m["type"] == "display"]
    assert displays and displays[-1]["display"]["state"] == "thinking"
    count = len(displays)
    controller.refresh()
    assert len([m for m in mind.sent if m["type"] == "display"]) == count
    json.dumps(displays[-1])  # serializable as the link sends it

def test_a_chattiness_change_reaches_the_mind(app):
    mind = FakeMind()
    server = SimpleNamespace(session_count=0, record_paint=lambda ms: None)
    ctrl = daemon.Controller(FakeWindow(), server, exit_app=lambda: None, mind=mind)
    ctrl.window.chattiness_changed.emit("quiet")
    assert mind.sent[-1] == {"type": "chattiness", "level": "quiet"}
    ctrl.timer.stop()


def test_a_chat_reaches_the_mind_with_leave_for_one_reply(controller):
    controller.window.chat_sent.emit("你好呀")
    assert controller.mind.sent[-1] == {"type": "chat", "text": "你好呀"}
    assert controller.server.grants == [1]


def test_a_chat_with_no_mind_running_is_dropped(controller):
    controller.mind.live = False
    controller.window.chat_sent.emit("hello")
    assert controller.mind.sent == [] and controller.server.grants == []


def test_private_today_reaches_the_mind(controller):
    controller.window.private_changed.emit("2026-10-28")
    assert controller.mind.sent[-1] == {"type": "private", "day": "2026-10-28"}
    controller.window.private_changed.emit(None)
    assert controller.mind.sent[-1] == {"type": "private", "day": None}


def test_no_pause_lifts_the_say_interval_and_tells_the_mind(controller):
    from companion.server import SAY_INTERVAL_S

    controller.window.no_gap_changed.emit(True)
    assert controller.server.intervals == [0.0]
    assert controller.mind.sent[-1] == {"type": "no_gap", "on": True}
    controller.window.no_gap_changed.emit(False)
    assert controller.server.intervals == [0.0, SAY_INTERVAL_S]
    assert controller.mind.sent[-1] == {"type": "no_gap", "on": False}


def test_a_limit_change_reaches_the_mind(controller):
    for limit in ("unlimited", 500, "normal"):
        controller.window.limit_changed.emit(limit)
        assert controller.mind.sent[-1] == {"type": "limit", "limit": limit}


def test_a_web_search_change_reaches_the_mind(controller):
    for search in ("normal", 40, "unlimited", "off"):
        controller.window.search_changed.emit(search)
        assert controller.mind.sent[-1] == {"type": "search", "search": search}
