"""Window checks that need a real Windows desktop session.

Every window that is shown is parked at (-32000, -32000) first, and the screen area the
pet and the bubble clamp into is injected far off-screen, so no test ever shows a window
on the desktop. Everything is closed at teardown.
"""

import sys

import pytest

if sys.platform != "win32":
    pytest.skip("window style checks are Windows-only", allow_module_level=True)

import ctypes
import json
import logging
import shutil
from pathlib import Path
from dataclasses import replace

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QAction, QGuiApplication, QMouseEvent
from PySide6.QtWidgets import QApplication

from companion import settings as prefs
from companion import skin as skins
from companion import window as windows
from companion.bubble import to_rect
from companion.model import DisplayState, State
from companion.window import FRAME_CACHE_BYTES, CompanionWindow, FrameCache, head_anchor_x

# Window tests run on the small square placeholder pack, not the bundled character.
PLACEHOLDER = Path(__file__).parent / "fixtures" / "placeholder-skin"

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_NOACTIVATE = 0x08000000

AREA = QRect(-32400, -32400, 3000, 2000)
PARK = QPoint(-32000, -32000)

LIVE = DisplayState(state=State.THINKING, session_id="s1", project="cc-companion", model="Opus", sessions=1)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app):
    created = []

    def make(settings=None, skin=None, park=True):
        w = CompanionWindow(settings or prefs.Settings(), skin=skin or skins.load_skin(PLACEHOLDER))
        w.bubble.area_for = lambda rect: AREA
        if park:
            w.move(PARK)
        created.append(w)
        return w

    yield make
    for w in created:
        w.close()
    app.processEvents()


def ex_style(widget) -> int:
    user32 = ctypes.windll.user32
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    return user32.GetWindowLongPtrW(int(widget.winId()), GWL_EXSTYLE)


def send_mouse(widget, kind, local: QPoint, buttons):
    button = Qt.MouseButton.LeftButton
    global_pos = QPointF(widget.mapToGlobal(local))
    event = QMouseEvent(kind, QPointF(local), global_pos, button, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, event)


def show(app, w):
    w.show()
    app.processEvents()


def wide_skin(tmp_path):
    root = tmp_path / "wide"
    shutil.copytree(PLACEHOLDER, root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["frameSize"] = [412, 344]
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return skins.load_skin(root)


def test_topmost_tool_window_off_taskbar_and_alt_tab(app, window):
    w = window()
    show(app, w)
    style = ex_style(w)
    assert style & WS_EX_TOPMOST, "not always on top"
    assert style & WS_EX_TOOLWINDOW, "would appear in alt-tab"
    assert not style & WS_EX_APPWINDOW, "would appear on the taskbar"
    assert style & WS_EX_NOACTIVATE, "clicking would steal focus"


def test_default_size_is_medium(window):
    assert window().size() == QSize(275, 275)  # the placeholder frame is square


def test_saved_position_is_restored(app, window):
    area = QGuiApplication.primaryScreen().availableGeometry()
    target = area.topLeft() + QPoint(50, 60)
    w = window(prefs.Settings(x=target.x(), y=target.y()), park=False)
    assert w.pos() == target


def test_offscreen_position_falls_back_to_primary_screen(window):
    w = window(prefs.Settings(x=-100_000, y=-100_000), park=False)
    assert QGuiApplication.primaryScreen().availableGeometry().contains(w.geometry())


def test_saved_spot_overhanging_the_screen_edge_is_clamped_inside(window):
    area = QGuiApplication.primaryScreen().availableGeometry()
    # The centre stays on the primary screen, but the right edge overhangs it.
    x = area.x() + area.width() - 275 // 2 - 10
    w = window(prefs.Settings(x=x, y=area.y() + 50), park=False)
    assert to_rect(area).contains(to_rect(w.geometry()))
    assert (w.x(), w.y()) == (area.x() + area.width() - 275, area.y() + 50)


def test_drag_moves_window_and_saves_position(app, window):
    w = window()
    show(app, w)
    start = w.pos()
    grab = QPoint(100, 100)
    send_mouse(w, QEvent.Type.MouseButtonPress, grab, Qt.MouseButton.LeftButton)
    send_mouse(w, QEvent.Type.MouseMove, grab + QPoint(-30, -20), Qt.MouseButton.LeftButton)
    assert w.animator.reaction == "drag"
    send_mouse(w, QEvent.Type.MouseButtonRelease, grab + QPoint(-30, -20), Qt.MouseButton.NoButton)
    assert w.animator.reaction == "land"
    assert w.pos() == start + QPoint(-30, -20)
    saved = prefs.load()
    assert (saved.x, saved.y) == (w.x(), w.y())


def click(w, local: QPoint, wiggle: QPoint = QPoint(0, 0)):
    send_mouse(w, QEvent.Type.MouseButtonPress, local, Qt.MouseButton.LeftButton)
    if wiggle != QPoint(0, 0):
        send_mouse(w, QEvent.Type.MouseMove, local + wiggle, Qt.MouseButton.LeftButton)
    send_mouse(w, QEvent.Type.MouseButtonRelease, local + wiggle, Qt.MouseButton.NoButton)


@pytest.mark.parametrize("fx, fy, reaction", [(0.5, 0.2, "head"), (0.4, 0.8, "body"), (0.9, 0.8, "tail")])
def test_click_on_a_zone_plays_its_reaction(app, window, fx, fy, reaction):
    w = window()
    show(app, w)
    click(w, QPoint(int(w.width() * fx), int(w.height() * fy)))
    assert w.animator.reaction == reaction


def test_small_wiggle_is_still_a_click_and_does_not_move(app, window):
    w = window()
    show(app, w)
    start = w.pos()
    click(w, QPoint(80, 160), wiggle=QPoint(2, 2))
    assert w.animator.reaction == "body"
    assert w.pos() == start


def test_double_click_is_a_head_pat(app, window):
    w = window()
    show(app, w)
    body = QPoint(80, 160)
    click(w, body)
    assert w.animator.reaction == "body"
    send_mouse(w, QEvent.Type.MouseButtonDblClick, body, Qt.MouseButton.LeftButton)
    send_mouse(w, QEvent.Type.MouseButtonRelease, body, Qt.MouseButton.NoButton)
    assert w.animator.reaction == "head"


def test_window_follows_the_skin_aspect(window, tmp_path):
    w = window(skin=wide_skin(tmp_path))
    assert (w.width(), w.height()) == (329, 275)


# --- bubble ------------------------------------------------------------------------------


def test_set_display_repaints_the_bubble_within_the_call(app, window):
    w = window()
    show(app, w)
    w.set_display(LIVE)
    app.processEvents()  # the bubble's first expose
    assert w.bubble.isVisible()
    painted = w.bubble.paint_count
    assert w.set_display(replace(LIVE, state=State.EDITING, tool_name="Write")) is True
    assert w.bubble.paint_count == painted + 1  # the one-frame unit gate: no event-loop turn
    assert w.set_display(replace(LIVE, state=State.EDITING, tool_name="Write")) is False
    assert w.bubble.paint_count == painted + 1


def test_bubble_follows_the_pet(app, window):
    w = window()
    show(app, w)
    w.set_display(LIVE)
    start = w.bubble.pos()
    w.move(w.pos() + QPoint(30, -20))
    assert w.bubble.pos() == start + QPoint(30, -20)
    assert w.bubble.geometry().bottom() < w.y()  # above the pet's frame top


def test_hiding_the_pet_hides_the_bubble_and_showing_brings_it_back(app, window):
    w = window()
    show(app, w)
    w.set_display(LIVE)
    assert w.bubble.isVisible()
    w.hide()
    assert not w.bubble.isVisible()
    assert w.set_display(replace(LIVE, state=State.RUNNING)) is False
    assert not w.bubble.isVisible()
    show(app, w)
    assert w.bubble.isVisible()


def test_bubble_shown_with_the_pet_stays_above_it(app, window):
    user32 = ctypes.windll.user32
    user32.GetWindow.restype = ctypes.c_void_p
    user32.GetWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    w = window()
    w.set_display(LIVE)  # hidden: the bubble waits for the pet's showEvent
    assert not w.bubble.isVisible()
    show(app, w)
    assert w.bubble.isVisible()
    pet, below, steps = int(w.winId()), user32.GetWindow(int(w.bubble.winId()), 2), 0  # GW_HWNDNEXT
    while below and below != pet and steps < 100_000:
        below, steps = user32.GetWindow(below, 2), steps + 1
    assert below == pet


def test_closing_the_pet_closes_the_bubble(app, window):
    w = window()
    show(app, w)
    w.set_display(LIVE)
    w.close()
    assert not w.bubble.isVisible()


def test_bubble_anchor_is_the_head_zone_centre(tmp_path):
    assert head_anchor_x(skins.load_skin(PLACEHOLDER)) == 0.5
    skin = skins.load_skin(PLACEHOLDER)
    zones = tuple(replace(z, left=0.2, right=0.6) if z.name == "head" else z for z in skin.zones)
    assert head_anchor_x(replace(skin, zones=zones)) == pytest.approx(0.4)
    assert head_anchor_x(replace(skin, zones=())) == 0.5


# --- settings ------------------------------------------------------------------------------


def test_size_change_keeps_the_feet_and_is_saved(window):
    w = window(park=False)
    w.move(-31000, -31000)  # inside the injected area; the window is never shown
    before = to_rect(w.geometry())
    assert w.apply_setting("size", "L") is True
    after = to_rect(w.geometry())
    assert (after.w, after.h) == (344, 344)
    assert after.bottom == before.bottom
    assert abs(after.cx - before.cx) <= 1
    saved = prefs.load()
    assert saved.size == "L"
    assert (saved.x, saved.y) == (after.x, after.y)
    assert w.frames.pixmaps == {} and w.fade_from is None


def test_size_change_is_clamped_into_the_area(window):
    w = window(park=False)
    w.move(AREA.x() + AREA.width() - 275, AREA.y() + AREA.height() - 275)  # the area's bottom right corner
    assert w.apply_setting("size", "L") is True
    assert to_rect(AREA).contains(to_rect(w.geometry()))


def test_invalid_setting_is_rejected_and_nothing_is_written(window):
    w = window(park=False)
    assert w.apply_setting("size", "XL") is False
    assert w.apply_setting("reduced_motion", "yes") is False
    assert w.apply_setting("no_such_setting", 1) is False
    assert w.settings.size == "M" and w.size() == QSize(275, 275)
    assert not prefs.settings_path().exists()


def test_reduced_motion_and_activity_reach_the_animator(window):
    w = window(park=False)
    assert w.apply_setting("reduced_motion", True) is True
    assert w.animator.reduced_motion is True
    assert w.animator.next_change(w.clock()) is None
    assert w.apply_setting("activity", "lively") is True
    assert w.animator.activity == "lively"
    saved = prefs.load()
    assert (saved.reduced_motion, saved.activity) == (True, "lively")


def test_bubble_mode_and_subagents_apply_live(app, window):
    w = window()
    show(app, w)
    w.set_display(replace(LIVE, subagents=2))
    assert w.bubble.isVisible()
    assert "2 subagents" in (w.bubble.text.counters or "")
    assert w.apply_setting("subagents", False) is True
    assert w.bubble.text.counters is None
    assert w.apply_setting("bubble_mode", "hidden") is True
    assert not w.bubble.isVisible()
    assert prefs.load().bubble_mode == "hidden"


# --- menu ----------------------------------------------------------------------------------


def action(menu, name) -> QAction:
    found = menu.findChild(QAction, name)
    assert found is not None, name
    return found


def test_menu_checks_match_the_settings(window):
    settings = prefs.Settings(
        size="L", bubble_mode="attention", activity="quiet", reduced_motion=True, subagents=False, persona="quiet"
    )
    w = window(settings, park=False)
    menu = w.build_menu()
    checked = {
        a.objectName() for a in menu.findChildren(QAction) if a.isCheckable() and a.isChecked()
    }
    assert checked == {"size:L", "bubble_mode:attention", "activity:quiet", "persona:quiet", "chattiness:normal", "limit:normal", "reduced_motion:toggle"}
    assert action(menu, "size:S").data() == ("size", "S")
    assert action(menu, "subagents:toggle").data() == ("subagents", True)
    menu.deleteLater()


def test_menu_lists_every_choice(window):
    menu = window(park=False).build_menu()
    names = {a.objectName() for a in menu.findChildren(QAction) if a.objectName()}
    assert {
        "size:S", "size:M", "size:L",
        "bubble_mode:always", "bubble_mode:attention", "bubble_mode:hidden",
        "activity:quiet", "activity:normal", "activity:lively",
        "persona:off", "persona:on", "persona:quiet", "persona:folder", "persona:talk", "persona:private", "persona:no_gap", "persona:spend", "persona:knows",
        "limit:normal", "limit:unlimited", "limit:custom",
        "chattiness:quiet", "chattiness:normal", "chattiness:chatty",
        "reduced_motion:toggle", "subagents:toggle", "hide", "quit",
    } <= names
    menu.deleteLater()


def test_menu_choice_applies_and_saves_the_setting(window):
    w = window(park=False)
    w.move(-31000, -31000)
    menu = w.build_menu()
    action(menu, "size:S").trigger()
    action(menu, "reduced_motion:toggle").trigger()
    assert w.size() == QSize(206, 206)
    assert w.animator.reduced_motion is True
    saved = prefs.load()
    assert (saved.size, saved.reduced_motion) == ("S", True)
    menu.deleteLater()


class FakePlayer:
    def __init__(self):
        self.played, self.preloads = [], 0

    def play(self, cue):
        self.played.append(cue)

    def preload(self):
        self.preloads += 1


def test_sounds_play_on_done_and_error_only_when_switched_on(window):
    live = DisplayState(state=State.RUNNING, session_id="s1", sessions=1)
    for enabled, expected in ((False, []), (True, ["done", "error"])):
        w = window(prefs.Settings(sounds=enabled))
        w.sounds = FakePlayer()
        w.set_display(live)
        w.set_display(replace(live, state=State.DONE))
        w.set_display(replace(live, state=State.THINKING))
        w.set_display(replace(live, state=State.ERROR, error_reason=None))  # a tool flash: silent
        w.set_display(replace(live, state=State.ERROR, error_reason="rate_limit"))
        assert w.sounds.played == expected, enabled


def test_sounds_toggle_is_in_the_menu_and_switching_on_preloads(window):
    w = window(park=False)
    w.sounds = FakePlayer()
    menu = w.build_menu()
    toggle = action(menu, "sounds:toggle")
    assert (toggle.data(), toggle.isChecked()) == (("sounds", True), False)
    toggle.trigger()
    assert w.settings.sounds is True and prefs.load().sounds is True
    assert w.sounds.preloads == 1
    menu.deleteLater()


def test_menu_hide_and_quit_emit_their_signals(window):
    w = window(park=False)
    hits = []
    w.hide_requested.connect(lambda: hits.append("hide"))
    w.quit_requested.connect(lambda: hits.append("quit"))
    menu = w.build_menu()
    action(menu, "hide").trigger()
    action(menu, "quit").trigger()
    assert hits == ["hide", "quit"]
    menu.deleteLater()


# --- frames and logging ---------------------------------------------------------------------


def test_frame_cache_holds_a_medium_idle_loop_and_clears():
    assert FRAME_CACHE_BYTES >= 241 * 329 * 275 * 4
    cache = FrameCache()
    path = skins.load_skin(PLACEHOLDER).state_clip(State.IDLE).frames[0]
    cache.get(path, QSize(100, 100), 1.0)
    assert cache.used == 100 * 100 * 4
    cache.clear()
    assert (cache.used, len(cache.pixmaps)) == (0, 0)


def test_first_paint_is_logged_once(app, window, monkeypatch, caplog):
    monkeypatch.setattr(windows, "_first_paint_logged", False)
    caplog.set_level(logging.INFO, logger="companion")
    first = window()
    first.grab()  # paints while hidden, which does not count
    assert "first paint" not in caplog.messages
    show(app, first)
    first.repaint()
    show(app, window())
    assert [r.getMessage() for r in caplog.records].count("first paint") == 1

# --- persona -------------------------------------------------------------------------------


def idle_window(app, window, persona):
    w = window(prefs.Settings(persona=persona))
    show(app, w)
    w.set_display(DisplayState(sessions=1, session_id="s1"))
    return w


def test_say_with_the_persona_off_does_nothing(app, window):
    w = idle_window(app, window, "off")
    w.say("hi", "happy", 8)
    assert w.bubble.speech is None and w.animator.idle_clip is None


def test_say_shows_her_line_and_plays_the_mood(app, window):
    w = idle_window(app, window, "on")
    w.say("hi", "happy", 8)
    assert w.bubble.speech == "hi" and w.bubble.text.speech == "hi"
    assert w.animator.idle_clip == w.skin.reactions["tail"]


def test_quiet_plays_the_mood_without_words(app, window):
    w = idle_window(app, window, "quiet")
    w.say("hi", "proud", 8)
    assert w.bubble.speech is None
    assert w.animator.idle_clip == w.skin.reactions["head"]


def test_a_mood_with_no_clip_still_shows_the_line(app, window):
    w = idle_window(app, window, "on")
    w.say("hi", "calm", 8)
    assert w.bubble.speech == "hi" and w.animator.idle_clip is None


def test_turning_the_persona_quiet_or_off_clears_her_line(app, window):
    for mode in ("quiet", "off"):
        w = idle_window(app, window, "on")
        w.say("hi", "calm", 8)
        assert w.apply_setting("persona", mode) is True
        assert w.bubble.speech is None
        assert prefs.load().persona == mode


def test_open_her_folder_creates_it_and_opens_it(window):
    w = window(park=False)
    opened = []
    w.reveal = opened.append
    menu = w.build_menu()
    action(menu, "persona:folder").trigger()
    assert prefs.persona_dir().is_dir()
    assert opened == [prefs.persona_dir()]
    menu.deleteLater()


def test_a_mood_with_no_words_plays_only_the_clip(app, window):
    w = idle_window(app, window, "on")
    w.say(None, "happy", 8)
    assert w.bubble.speech is None
    assert w.animator.idle_clip == w.skin.reactions["tail"]


def test_a_click_reaction_reports_the_pat(app, window):
    w = window()
    show(app, w)
    pats = []
    w.patted.connect(pats.append)
    click(w, QPoint(int(w.width() * 0.9), int(w.height() * 0.8)))
    assert pats == ["tail"]
    click(w, QPoint(int(w.width() * 0.9), int(w.height() * 0.8)))
    assert pats == ["tail"], "a click the animator ignores is no pat"


def test_changing_the_persona_is_announced(window):
    w = window(park=False)
    modes = []
    w.persona_changed.connect(modes.append)
    w.apply_setting("persona", "quiet")
    w.apply_setting("persona", "quiet")
    assert modes == ["quiet"]

def test_chattiness_menu_reads_writes_her_config_and_announces_it(window):
    prefs.persona_dir().mkdir(parents=True)
    prefs.persona_config_path().write_text('{"model": "claude-haiku-5-5", "chattiness": "quiet"}', encoding="utf-8")
    w = window(park=False)
    changes = []
    w.chattiness_changed.connect(changes.append)
    menu = w.build_menu()
    assert action(menu, "chattiness:quiet").isChecked()
    action(menu, "chattiness:chatty").trigger()
    assert changes == ["chatty"]
    saved = json.loads(prefs.persona_config_path().read_text(encoding="utf-8"))
    assert saved == {"model": "claude-haiku-5-5", "chattiness": "chatty"}
    assert w.set_chattiness("chatty") is True and changes == ["chatty"], "the same level again changes nothing"
    assert w.set_chattiness("loud") is False
    menu.deleteLater()


class FakeProcess:
    started = []

    def setProgram(self, program):
        self.program = program

    def setNativeArguments(self, args):
        self.args = args

    def startDetached(self):
        FakeProcess.started.append((self.program, self.args))
        return True, 123


def test_reveal_starts_explorer_with_her_identity_selected(monkeypatch, tmp_path):
    monkeypatch.setattr(windows, "QProcess", FakeProcess)
    FakeProcess.started = []
    assert windows.reveal(tmp_path) is True
    (tmp_path / "identity.md").write_text("me", encoding="utf-8")
    assert windows.reveal(tmp_path) is True
    native = str(tmp_path)
    assert FakeProcess.started == [("explorer.exe", f'"{native}"'), ("explorer.exe", '/select,"' + str(tmp_path / "identity.md") + '"')]
