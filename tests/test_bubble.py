"""Bubble window checks that need a real Windows desktop session.

Every window is parked at (-32000, -32000) before it is shown, the screen area is
injected far off-screen so placement never pulls the bubble back onto a real screen,
and everything is closed at teardown.
"""

import sys

import pytest

if sys.platform != "win32":
    pytest.skip("bubble window checks are Windows-only", allow_module_level=True)

import ctypes
import logging
import math
from dataclasses import replace

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication
from PySide6.QtWidgets import QApplication, QWidget

from companion import bubble as bubbles
from companion import placement
from companion.bubble import (
    MAX_W,
    MIN_W,
    RADIUS,
    SHADOW,
    SLACK,
    TAIL_H,
    TAIL_W,
    BubbleWindow,
    screen_area,
    to_rect,
)
from companion.bubble_text import next_tick
from companion.model import DisplayState, Quota, State

GWL_EXSTYLE = -20
GW_HWNDNEXT = 2
GW_OWNER = 4
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000

AREA = QRect(-32400, -32400, 3000, 2000)
PARK = QPoint(-32000, -32000)
PET_W, PET_H = 329, 275
NOW = 1_761_600_000.0

LIVE = DisplayState(
    state=State.THINKING, session_id="s1", project="cc-companion", model="Opus", effort="high", sessions=1
)
# 8040.5 s left reads "2h 14m" and rolls over to "2h 13m" in 0.5 s.
QUOTA = Quota(
    five_hour_pct=23.0,
    five_hour_resets_at=NOW + 8040.5,
    seven_day_pct=41.0,
    seven_day_resets_at=NOW + 273_600.5,
    seen=True,
)

user32 = ctypes.windll.user32
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.GetWindow.restype = ctypes.c_void_p
user32.GetWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]


class Pet(QWidget):
    """Stands in for the pet: a transparent, input-less tool window wired the way CompanionWindow is."""

    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(PET_W, PET_H)
        self.bubble = None

    def moveEvent(self, event):
        super().moveEvent(event)
        if self.bubble is not None:
            self.bubble.follow(self.geometry())

    def hideEvent(self, event):
        super().hideEvent(event)
        if self.bubble is not None:
            self.bubble.hide()


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def paint_errors(monkeypatch, caplog):
    """Log records of the paint guard; the once-per-process flag is reset for each test."""
    monkeypatch.setattr(bubbles, "_paint_error_logged", False)
    caplog.set_level(logging.ERROR, logger="companion")
    return lambda: [r for r in caplog.records if r.getMessage() == "bubble paint failed"]


@pytest.fixture
def clock():
    return [NOW]


@pytest.fixture
def make(app, clock, paint_errors):
    created = []

    def build(at: QPoint = PARK):
        pet = Pet()
        pet.move(at)
        bubble = BubbleWindow(pet, area_for=lambda rect: AREA, wall=lambda: clock[0])
        pet.bubble = bubble
        created.append(pet)
        pet.show()
        app.processEvents()
        return pet, bubble

    yield build
    for pet in created:
        pet.bubble.close()
        pet.close()
    app.processEvents()


def sync(bubble, display, *, mode="always", subagents=True, pet_visible=True):
    return bubble.sync(display, mode=mode, show_subagents=subagents, pet_visible=pet_visible)


def shown(app, bubble, display=LIVE, **kwargs):
    """Sync and let the first expose arrive, as the running event loop would right after."""
    sync(bubble, display, **kwargs)
    app.processEvents()
    assert bubble.isVisible()


def hwnd(widget) -> int:
    return int(widget.winId())


# --- native window -----------------------------------------------------------------------


def test_bubble_is_a_click_through_topmost_tool_window(app, make):
    pet, bubble = make()
    shown(app, bubble)
    style = user32.GetWindowLongPtrW(hwnd(bubble), GWL_EXSTYLE)
    assert style & WS_EX_TOPMOST, "not always on top"
    assert style & WS_EX_TOOLWINDOW, "would appear in alt-tab"
    assert style & WS_EX_NOACTIVATE, "would steal focus"
    assert style & WS_EX_TRANSPARENT, "would catch clicks"
    assert style & WS_EX_LAYERED, "no per-pixel transparency"
    assert not style & WS_EX_APPWINDOW, "would appear on the taskbar"


def test_bubble_is_owned_by_the_pet(app, make):
    pet, bubble = make()
    shown(app, bubble)
    assert user32.GetWindow(hwnd(bubble), GW_OWNER) == hwnd(pet)


def test_bubble_stays_above_the_pet_after_the_pet_is_raised(app, make):
    pet, bubble = make()
    shown(app, bubble)
    pet.raise_()
    app.processEvents()
    below, steps = user32.GetWindow(hwnd(bubble), GW_HWNDNEXT), 0
    while below and below != hwnd(pet) and steps < 100_000:
        below, steps = user32.GetWindow(below, GW_HWNDNEXT), steps + 1
    assert below == hwnd(pet), "the pet is not below the bubble in z-order"


def test_warm_up_creates_the_native_window_off_screen_without_showing_or_painting(app, make):
    pet, bubble = make()
    assert not bubble.testAttribute(Qt.WidgetAttribute.WA_WState_Created)
    bubble.warm_up()
    assert bubble.testAttribute(Qt.WidgetAttribute.WA_WState_Created)
    assert not bubble.isVisible()
    assert bubble.paint_count == 0
    assert bubble.text is None
    shown(app, bubble)  # and it still shows and paints normally afterwards
    assert bubble.paint_count >= 1


# --- sync ----------------------------------------------------------------------------------


def test_a_text_change_repaints_once_within_the_call(app, make):
    pet, bubble = make()
    shown(app, bubble)
    painted = bubble.paint_count
    assert sync(bubble, replace(LIVE, state=State.EDITING, tool_name="Edit")) is True
    assert bubble.paint_count == painted + 1  # no processEvents: synchronous


def test_the_same_display_again_does_not_repaint(app, make):
    pet, bubble = make()
    shown(app, bubble)
    painted = bubble.paint_count
    assert sync(bubble, LIVE) is False
    assert bubble.paint_count == painted


def test_first_show_paints_on_its_expose(app, make):
    pet, bubble = make()
    painted = bubble.paint_count
    repainted = sync(bubble, LIVE)
    assert bubble.isVisible()
    # repaint() is a no-op before the first expose, so an appearance is not timed (§5.7 step 3).
    assert repainted is False
    assert bubble.paint_count == painted
    app.processEvents()
    assert bubble.paint_count > painted
    assert bubble.text.title == "Thinking"


def test_showing_again_after_a_hide_paints_on_its_expose(app, make):
    pet, bubble = make()
    shown(app, bubble)
    assert sync(bubble, DisplayState()) is False
    assert not bubble.isVisible()
    painted = bubble.paint_count
    assert sync(bubble, LIVE) is False
    assert bubble.isVisible() and bubble.paint_count == painted
    app.processEvents()
    assert bubble.paint_count > painted


def test_a_text_change_moves_neither_window(app, make):
    pet, bubble = make()
    shown(app, bubble)
    pet_rect, bubble_rect = pet.geometry(), bubble.geometry()
    sync(bubble, replace(LIVE, state=State.WAITING, waiting_reason="permission", subagents=3))
    sync(bubble, replace(LIVE, quota=QUOTA, context_pct=88.0))
    assert (pet.geometry(), bubble.geometry()) == (pet_rect, bubble_rect)


# --- following -----------------------------------------------------------------------------


def test_bubble_follows_a_pet_move_by_the_same_delta(app, make):
    pet, bubble = make()
    shown(app, bubble)
    start = bubble.pos()
    pet.move(pet.pos() + QPoint(30, -20))
    assert bubble.pos() == start + QPoint(30, -20)
    assert to_rect(AREA).contains(to_rect(bubble.geometry()))


def test_bubble_sits_above_the_pet_with_the_tail_down(app, make):
    pet, bubble = make()
    shown(app, bubble)
    assert bubble.placement.below is False
    assert bubble.geometry().y() + bubble.height() == pet.y() - placement.GAP
    layout = bubble.body_layout(bubble.text)
    assert layout.body.y() + layout.body.height() == bubble.height() - TAIL_H - SHADOW


def test_bubble_goes_below_a_pet_at_the_top_of_the_area(app, make):
    pet, bubble = make(QPoint(-32000, AREA.y() + 10))
    shown(app, bubble)
    assert bubble.placement.below is True
    assert bubble.y() == pet.y() + pet.height() + placement.GAP
    layout = bubble.body_layout(bubble.text)
    assert layout.body.y() == TAIL_H
    assert layout.body.x() + RADIUS + TAIL_W // 2 <= layout.tip_x <= layout.body.right() + 1 - RADIUS - TAIL_W // 2


def test_tail_bound_matches_placement():
    assert placement.TAIL_MIN == RADIUS + TAIL_W // 2


def test_to_rect_uses_width_and_height():
    rect = to_rect(QRect(10, 20, 30, 40))
    assert (rect.x, rect.y, rect.w, rect.h, rect.right, rect.bottom) == (10, 20, 30, 40, 40, 60)


def test_screen_area_falls_back_to_the_primary_screen(app):
    primary = QGuiApplication.primaryScreen()
    assert screen_area(QRect(PARK, PARK + QPoint(10, 10))) == primary.availableGeometry()
    centre = primary.geometry().center()
    assert screen_area(QRect(centre, centre + QPoint(10, 10))) == primary.availableGeometry()


# --- visibility ----------------------------------------------------------------------------


def test_hidden_pet_hides_the_bubble(app, make):
    pet, bubble = make()
    shown(app, bubble)
    assert sync(bubble, LIVE, pet_visible=False) is False
    assert not bubble.isVisible()
    shown(app, bubble)
    pet.hide()
    assert not bubble.isVisible()


def test_always_mode_shows_a_live_session(app, make):
    pet, bubble = make()
    sync(bubble, LIVE)
    assert bubble.isVisible()


def test_always_mode_with_no_session_shows_only_quota(app, make):
    pet, bubble = make()
    sync(bubble, DisplayState())
    assert not bubble.isVisible()
    sync(bubble, None)
    assert not bubble.isVisible()
    sync(bubble, DisplayState(quota=QUOTA))
    assert bubble.isVisible()
    assert bubble.text.title == "No live session"


@pytest.mark.parametrize(
    "state, shows", [(State.WAITING, True), (State.ERROR, True), (State.DONE, True), (State.THINKING, False)]
)
def test_attention_mode(app, make, state, shows):
    pet, bubble = make()
    sync(bubble, replace(LIVE, state=state), mode="attention")
    assert bubble.isVisible() is shows


def test_hidden_mode_never_shows(app, make):
    pet, bubble = make()
    for display in (LIVE, replace(LIVE, state=State.WAITING), DisplayState(quota=QUOTA)):
        assert sync(bubble, display, mode="hidden") is False
        assert not bubble.isVisible()


# --- robustness ----------------------------------------------------------------------------


def test_fonts_are_set_in_pixels(app, make):
    pet, bubble = make()
    family = "Segoe UI" if "Segoe UI" in QFontDatabase.families() else bubble.title_font.family()
    assert bubble.title_font.family() == bubble.body_font.family() == family
    assert (bubble.title_font.pixelSize(), bubble.body_font.pixelSize()) == (14, 12)
    assert bubble.title_font.weight() == QFont.Weight.DemiBold


def test_canvas_is_recomputed_on_a_screen_change(app, make, monkeypatch):
    pet, bubble = make()
    shown(app, bubble)
    assert bubble._watching_screen  # screenChanged is connected on the first show
    bottom = bubble.y() + bubble.height()
    monkeypatch.setattr(bubble, "canvas_size", lambda: QSize(MAX_W + SLACK, 140))
    bubble._recompute_canvas()
    assert bubble.size() == QSize(MAX_W + SLACK, 140)
    assert bubble.y() + bubble.height() == bottom  # still GAP above the pet's frame top
    assert bubble.placement.rect.h == 140


def test_canvas_outside_the_body_is_transparent(app, make):
    pet, bubble = make()
    shown(app, bubble)
    image = bubble.grab().toImage()
    layout = bubble.body_layout(bubble.text)
    assert image.pixelColor(0, 0).alpha() == 0
    assert image.pixelColor(bubble.width() - 1, 0).alpha() == 0
    assert image.pixelColor(layout.body.center()).alpha() > 200


def test_canvas_size_is_fixed_across_texts(app, make):
    pet, bubble = make()
    size = bubble.size()
    assert size == bubble.canvas_size()
    assert size.width() == MAX_W + SLACK
    busy = replace(
        LIVE, context_pct=55.0, permission_mode="plan", subagents=2, tasks_created=5, tasks_completed=3, quota=QUOTA
    )
    for display in (LIVE, busy, replace(LIVE, cost_usd=0.42), DisplayState(quota=QUOTA)):
        sync(bubble, display)
        assert bubble.size() == size
        assert bubble.canvas_size() == size
    # The fullest bubble still fits the canvas with its tail and shadow.
    sync(bubble, busy)
    layout = bubble.body_layout(bubble.text)
    assert len(layout.rows) == 4
    assert layout.body.height() + TAIL_H + SHADOW <= size.height()


def test_body_width_follows_the_longest_row(app, make):
    pet, bubble = make()
    shown(app, bubble, replace(LIVE, project=None, model=None, effort=None))
    narrow = bubble.body_layout(bubble.text).body.width()
    assert narrow == MIN_W
    sync(bubble, replace(LIVE, project="a-rather-long-project-name", model="Opus 4.7 (1M context)"))
    assert MIN_W < bubble.body_layout(bubble.text).body.width() <= MAX_W


def test_a_400_character_title_elides(app, make, paint_errors):
    pet, bubble = make()
    shown(app, bubble)
    bubble.text = replace(bubble.text, title="x" * 400, labels="y" * 400)
    layout = bubble.body_layout(bubble.text)
    assert layout.title.endswith("…") and len(layout.title) < 400
    assert layout.rows[0].text.endswith("…")
    assert MAX_W - 2 * bubbles.PAD_X < layout.body.width() <= MAX_W  # capped, give or take one glyph
    painted = bubble.paint_count
    bubble.repaint()
    assert bubble.paint_count == painted + 1
    assert paint_errors() == []


def test_a_nan_ring_paints_without_raising(app, make, paint_errors):
    pet, bubble = make()
    shown(app, bubble)
    bubble.text = replace(bubble.text, ring_pct=float("nan"), ring_level="ok")
    painted = bubble.paint_count
    bubble.repaint()
    assert bubble.paint_count == painted + 1
    assert paint_errors() == []
    assert bubble.body_layout(bubble.text).ring_text is None


def test_ring_is_clamped_and_follows_the_level(app, make):
    pet, bubble = make()
    shown(app, bubble, replace(LIVE, context_pct=150.0))
    layout = bubble.body_layout(bubble.text)
    assert (layout.ring_pct, layout.ring_text) == (100.0, "100%")
    assert layout.ring_color == bubbles.LEVELS["high"]


def test_a_failing_paint_is_logged_once_and_never_raises(app, make, monkeypatch, paint_errors):
    pet, bubble = make()
    shown(app, bubble)

    def broken(text):
        raise ValueError("bad value")

    monkeypatch.setattr(bubble, "body_layout", broken)
    painted = bubble.paint_count
    bubble.repaint()
    bubble.repaint()
    assert bubble.paint_count == painted + 2
    assert len(paint_errors()) == 1


# --- tick timer ----------------------------------------------------------------------------


def test_tick_runs_only_for_shown_countdowns(app, make):
    pet, bubble = make()
    sync(bubble, LIVE)
    assert not bubble.tick.isActive(), "no bars"
    sync(bubble, replace(LIVE, quota=QUOTA))
    assert bubble.tick.isActive()
    assert bubble.tick.interval() == math.ceil((next_tick(replace(LIVE, quota=QUOTA), NOW) - NOW) * 1000)
    no_countdown = Quota(five_hour_pct=23.0, seven_day_pct=41.0, seen=True)
    sync(bubble, replace(LIVE, quota=no_countdown))
    assert bubble.text.bars and not bubble.tick.isActive(), "bars without a countdown"
    sync(bubble, replace(LIVE, quota=QUOTA), pet_visible=False)
    assert not bubble.tick.isActive(), "hidden with the pet"
    sync(bubble, replace(LIVE, quota=QUOTA), mode="attention")
    assert not bubble.tick.isActive(), "hidden by the mode"


def test_hiding_stops_the_tick(app, make):
    pet, bubble = make()
    sync(bubble, replace(LIVE, quota=QUOTA))
    assert bubble.tick.isActive()
    pet.hide()
    assert not bubble.tick.isActive()


def test_tick_redraws_the_countdown_on_the_rollover(app, make, clock):
    pet, bubble = make()
    shown(app, bubble, replace(LIVE, quota=QUOTA))
    assert bubble.text.bars[0].note == "2h 14m"
    clock[0] = NOW + 0.55
    painted = bubble.paint_count
    bubble._tick()
    assert bubble.text.bars[0].note == "2h 13m"
    assert bubble.paint_count == painted + 1
    assert bubble.tick.isActive()


def test_tick_drops_a_lapsed_window(app, make, clock):
    pet, bubble = make()
    quota = replace(QUOTA, five_hour_resets_at=NOW + 30.0)
    shown(app, bubble, replace(LIVE, quota=quota))
    assert [bar.label for bar in bubble.text.bars] == ["5h", "7d"]
    clock[0] = NOW + 31.0
    bubble._tick()
    assert [bar.label for bar in bubble.text.bars] == ["7d"]

# --- her line ------------------------------------------------------------------------------


def test_her_line_shows_alone_when_the_status_would_hide(app, make):
    pet, bubble = make()
    sync(bubble, DisplayState())
    assert not bubble.isVisible()
    bubble.say("Morning!", 8)
    app.processEvents()
    assert bubble.isVisible()
    layout = bubble.body_layout(bubble.text)
    assert (layout.status, layout.speech_lines, layout.rows) == (False, ("Morning!",), ())
    assert layout.body.height() == 2 * bubbles.PAD_Y + layout.speech_line_h


def test_her_line_sits_above_a_shown_status_and_repaints_within_the_call(app, make):
    pet, bubble = make()
    shown(app, bubble)
    plain = bubble.body_layout(bubble.text).body.height()
    assert bubble.say("跑了好久，终于好了！", 8) is True
    layout = bubble.body_layout(bubble.text)
    assert layout.status and layout.speech_lines == ("跑了好久，终于好了！",)
    assert layout.body.height() == plain + layout.speech_line_h + bubbles.DIVIDER_GAP
    assert bubble.text.title == "Thinking"


def test_her_line_clears_after_its_lifetime(app, make):
    pet, bubble = make()
    sync(bubble, DisplayState())
    bubble.say("hi", 5)
    assert bubble.speech_timer.isActive() and bubble.speech_timer.interval() == 5000
    bubble.speech_timer.timeout.emit()
    assert bubble.speech is None
    assert not bubble.isVisible()


def test_clearing_her_line_keeps_the_status(app, make):
    pet, bubble = make()
    shown(app, bubble)
    bubble.say("hi", 5)
    bubble.say(None)
    assert not bubble.speech_timer.isActive()
    assert bubble.isVisible() and bubble.text.speech is None


def test_hidden_mode_hides_her_line(app, make):
    pet, bubble = make()
    sync(bubble, LIVE, mode="hidden")
    assert bubble.say("hi", 5) is False
    assert not bubble.isVisible()


def test_a_long_line_wraps_onto_at_most_three_lines_and_elides(app, make):
    pet, bubble = make()
    shown(app, bubble)
    inner = MAX_W - 2 * bubbles.PAD_X
    metrics = bubble._metrics()[0].__class__(bubble.speech_font, bubble)
    for line in ("一起加油" * 30, "word " * 60):
        lines = bubble._wrap(line.strip(), inner)
        assert len(lines) == bubbles.SPEECH_LINES
        assert lines[-1].endswith("…")
        assert all(metrics.horizontalAdvance(part) <= inner for part in lines)
    english = bubble._wrap("That one took a while. Nice work on the tail clip today!", inner)
    assert 1 <= len(english) <= 2 and all(not part.startswith(" ") for part in english)
    assert " ".join(english) == "That one took a while. Nice work on the tail clip today!"


def test_an_astral_character_wraps_without_splitting(app, make):
    pet, bubble = make()
    text = "\U0001F433" * 80
    lines = bubble._wrap(text, MAX_W - 2 * bubbles.PAD_X)
    assert all(ch in ("\U0001F433", "…") for line in lines for ch in line)


def test_the_canvas_fits_the_fullest_status_and_three_lines(app, make):
    pet, bubble = make()
    busy = replace(
        LIVE, context_pct=55.0, permission_mode="plan", subagents=2, tasks_created=5, tasks_completed=3, quota=QUOTA
    )
    sync(bubble, busy)
    bubble.say("一起加油" * 30, 8)
    layout = bubble.body_layout(bubble.text)
    assert len(layout.rows) == 4 and len(layout.speech_lines) == bubbles.SPEECH_LINES
    assert layout.body.height() + TAIL_H + SHADOW <= bubble.height()
    assert bubble.size() == bubble.canvas_size()


def test_her_line_paints_without_raising(app, make, paint_errors):
    pet, bubble = make()
    shown(app, bubble)
    bubble.say("早呀～今天也一起加油 (｡•̀ᴗ-)✧", 8)
    painted = bubble.paint_count
    bubble.repaint()
    assert bubble.paint_count == painted + 1
    assert paint_errors() == []
