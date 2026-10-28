"""The speech bubble: a click-through window owned by the pet that says what Claude Code is doing.

The canvas has a fixed size, so new text only repaints it and the window moves only when
the pet moves; placement.place() picks the side (above or below the pet) from where the
pet is. Inside the canvas the body takes the width of its longest row and sits against
the tail side. The rest of the canvas stays transparent and the whole window ignores
input, so it never blocks a click on whatever is under it.

Her line (say()) sits above the header in a larger font, wrapped onto at most
SPEECH_LINES lines, and clears itself after its lifetime. When the bubble mode would hide
the status, the bubble shows her line alone.

sync() repaints synchronously, so an update to a bubble already on screen is drawn when
the call returns. A window that was hidden paints on its first expose, which the next
turn of the event loop delivers. A single-shot timer redraws the quota countdowns on
each minute rollover while they are shown.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QGuiApplication,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QTextLayout,
)
from PySide6.QtWidgets import QWidget

from .bubble_text import SEPARATOR, Bar, BubbleText, bubble_text, compose, finite, level, next_tick
from .model import DisplayState
from .placement import Placement, Rect, place

log = logging.getLogger("companion")

# Metrics in logical px. Fonts are set in pixels too, so nothing depends on the pet's size.
MAX_W = 320
MIN_W = 150
PAD_X = 12
PAD_Y = 9
ROW_GAP = 3
RADIUS = 12
TAIL_W = 16
TAIL_H = 9
SHADOW = 3  # room under the body for the shadow copy, drawn SHADOW_DY lower
SHADOW_DY = 2
SLACK = 2  # font metrics can round a pixel differently on another screen
DOT = 8
DOT_GAP = 6
RING_D = 18
RING_STROKE = 3
BAR_W = 72
BAR_H = 6
BAR_RADIUS = 3
TITLE_PX = 14
BODY_PX = 12
MAX_ROWS = 4  # under the header: labels, counters and two quota bars
SPEECH_PX = 14
SPEECH_LINES = 3
DIVIDER_GAP = 7  # between her line and the header, with a 1 px rule in the middle

FILL = QColor(251, 250, 247, 240)
BORDER = QColor(214, 209, 199, 230)
SHADOW_COLOR = QColor(0, 0, 0, 26)
TITLE_COLOR = QColor("#1F1F1F")
TEXT_COLOR = QColor("#555555")
MUTED_COLOR = QColor("#8C8C8C")
TRACK_COLOR = QColor("#E5E1D8")
TONES = {
    tone: QColor(hex_code)
    for tone, hex_code in {
        "idle": "#8E8E93",
        "none": "#B0B0B5",
        "thinking": "#7A6FF0",
        "compacting": "#7A6FF0",
        "searching": "#2E9BD6",
        "editing": "#2FA36B",
        "running": "#E07B24",
        "delegating": "#9B5FD6",
        "waiting": "#E3A008",
        "done": "#23A55A",
        "error": "#D64545",
    }.items()
}
LEVELS = {"ok": QColor("#3D7DD8"), "warn": QColor("#E3A008"), "high": QColor("#D64545")}

ELIDE = Qt.TextElideMode.ElideRight  # Qt elides with "…"
_SINGLE_LINE = Qt.TextFlag.TextSingleLine.value | Qt.TextFlag.TextDontClip.value
LEFT = (Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter).value | _SINGLE_LINE
RIGHT = (Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter).value | _SINGLE_LINE

# A paint failure is logged once per process, so a bad value cannot flood the log at 60 Hz.
_paint_error_logged = False


def to_rect(q: QRect) -> Rect:
    """A Qt rect as a placement Rect; width and height, never Qt's inclusive right() and bottom()."""
    return Rect(q.x(), q.y(), q.width(), q.height())


def screen_area(rect: QRect, fallback: QWidget | None = None) -> QRect:
    """Available geometry of the screen holding `rect`'s centre, else `fallback`'s screen, else the primary."""
    screen = QGuiApplication.screenAt(rect.center())
    if screen is None and fallback is not None:
        screen = fallback.screen()
    if screen is None:
        screen = QGuiApplication.primaryScreen()
    return screen.availableGeometry()


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(value, high))


def _percent(value: object) -> float:
    return min(max(finite(value) or 0.0, 0.0), 100.0)


def _font_family() -> str:
    if "Segoe UI" in QFontDatabase.families():
        return "Segoe UI"
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()


@dataclass(frozen=True)
class Row:
    """One row under the header: text, or a quota bar (label, track and fill) followed by its text."""

    text: str
    color: QColor
    bar_label: str | None = None
    bar_pct: float = 0.0
    bar_color: QColor | None = None


@dataclass(frozen=True)
class BodyLayout:
    """Where everything goes on the canvas for one BubbleText; all of it measured before painting."""

    body: QRect  # the rounded body, in canvas coordinates
    tip_x: int  # the tail tip, from the canvas's left edge
    below: bool  # True: the body is at the top and the tail points up at the pet
    header_h: int
    line_h: int
    tone: QColor
    title: str  # elided
    ring_pct: float | None  # 0-100, or None to leave the ring out
    ring_text: str | None
    ring_color: QColor | None
    rows: tuple[Row, ...]
    bar_label_w: int  # label column of the bar rows, so both tracks line up
    speech_lines: tuple[str, ...] = ()  # her line, wrapped and elided
    speech_line_h: int = 0
    status: bool = True  # False: her line alone, no header or rows


class BubbleWindow(QWidget):
    def __init__(
        self,
        pet: QWidget,
        *,
        anchor_x: float = 0.5,
        area_for: Callable[[QRect], QRect] | None = None,
        wall: Callable[[], float] = time.time,
    ) -> None:
        # Owned by the pet (Qt parent), so Windows keeps it above the pet and off the taskbar;
        # WindowTransparentForInput makes every pixel click-through.
        super().__init__(
            pet,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.pet = pet
        self.anchor_x = anchor_x
        # The available area for a pet geometry; tests inject one far off-screen.
        self.area_for: Callable[[QRect], QRect] = area_for or (lambda rect: screen_area(rect, pet))
        self.wall = wall
        self.text: BubbleText | None = None
        self.placement: Placement | None = None
        self.paint_count = 0
        self._last: tuple[DisplayState | None, str, bool, bool] | None = None
        self._watching_screen = False
        self.speech: str | None = None

        family = _font_family()
        self.title_font = QFont(family)
        self.title_font.setPixelSize(TITLE_PX)
        self.title_font.setWeight(QFont.Weight.DemiBold)
        self.body_font = QFont(family)
        self.body_font.setPixelSize(BODY_PX)
        self.speech_font = QFont(family)
        self.speech_font.setPixelSize(SPEECH_PX)

        self.tick = QTimer(self)
        self.tick.setSingleShot(True)
        self.tick.setTimerType(Qt.TimerType.CoarseTimer)
        self.tick.timeout.connect(self._tick)
        self.speech_timer = QTimer(self)
        self.speech_timer.setSingleShot(True)
        self.speech_timer.timeout.connect(self._end_speech)
        self.setFixedSize(self.canvas_size())

    def warm_up(self) -> None:
        """Create the native window and rasterize the fonts once, off screen and hidden.

        Otherwise the first live event pays for both on the main thread while its hook
        request waits on the GIL (measured 4.7-6 ms for the first event after a start).
        """
        self.winId()
        sample = BubbleText(
            tone="thinking",
            title="Running a command 0123456789",
            ring_pct=50.0,
            ring_level="ok",
            labels=SEPARATOR.join(("project", "model", "high effort", "2 sessions")),
            counters=SEPARATOR.join(("plan mode", "1 subagent", "3/5 tasks")),
            bars=(Bar("5h", 23.0, "ok", "2h 14m"), Bar("7d", 41.0, "ok", "3d 4h")),
            cost=None,
            speech="早呀～今天也一起加油 Morning (｡•̀ᴗ-)✧",
        )
        image = QImage(self.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        try:
            self._draw(painter, self.body_layout(sample))
        finally:
            painter.end()

    def canvas_size(self) -> QSize:
        """Room for the widest body, her longest line and the most rows, with the tail and shadow; the same for every text."""
        title_fm, body_fm = self._metrics()
        header_h = max(title_fm.height(), RING_D + 2)
        speech_h = SPEECH_LINES * QFontMetrics(self.speech_font, self).height() + DIVIDER_GAP
        height = TAIL_H + 2 * PAD_Y + speech_h + header_h + MAX_ROWS * (ROW_GAP + body_fm.height()) + SHADOW + SLACK
        return QSize(MAX_W + SLACK, height)

    def sync(self, display: DisplayState | None, *, mode: str, show_subagents: bool, pet_visible: bool) -> bool:
        """Show, hide or redraw the bubble for `display`; True if it was repainted within this call."""
        self._last = (display, mode, show_subagents, pet_visible)
        display = display if display is not None else DisplayState()
        now = self.wall()
        text = compose(mode, display, bubble_text(display, show_subagents=show_subagents, now=now), self.speech)
        if not pet_visible or text is None:
            self.hide()
            self.tick.stop()
            return False
        painted = self.paint_count
        if not self.isVisible():
            # Set first: if the show paints, it paints the new text.
            self.text = text
            self.follow(self.pet.geometry())
            self.show()
            if self.paint_count == painted:
                self.repaint()  # a no-op until the window's first expose, which then paints it
        elif text != self.text:
            self.text = text
            self.repaint()
        self._arm_tick(display, now)
        return self.paint_count > painted

    def say(self, text: str | None, ttl_s: float = 0.0) -> bool:
        """Show her line for `ttl_s` seconds, or clear it with None; True if repainted within this call."""
        self.speech = text or None
        if self.speech is None:
            self.speech_timer.stop()
        else:
            self.speech_timer.start(max(1, round(ttl_s * 1000)))
        return self._resync()

    def follow(self, pet_geometry: QRect) -> None:
        """Move next to the pet; repaint only when the side or the tail moved."""
        previous = self.placement
        area = to_rect(self.area_for(pet_geometry))
        current = place(to_rect(pet_geometry), (self.width(), self.height()), area, self.anchor_x)
        self.placement = current
        self.move(current.rect.x, current.rect.y)
        moved = previous is None or (previous.below, previous.tail_x) != (current.below, current.tail_x)
        if moved and self.isVisible():
            self.repaint()

    def body_layout(self, text: BubbleText) -> BodyLayout:
        """Measure every row of `text`, elide what does not fit and place the body against the tail."""
        title_fm, body_fm = self._metrics()
        speech_fm = QFontMetrics(self.speech_font, self)
        inner = MAX_W - 2 * PAD_X
        header_h = max(title_fm.height(), RING_D + 2)
        line_h = body_fm.height()
        speech_lines = self._wrap(text.speech, inner) if text.speech else ()
        speech_line_h = speech_fm.height()
        speech_h = len(speech_lines) * speech_line_h

        ring_pct = finite(text.ring_pct)
        ring_text = ring_color = None
        ring_w = 0
        if ring_pct is not None:
            ring_pct = _percent(ring_pct)
            ring_text = f"{round(ring_pct)}%"
            ring_color = LEVELS.get(text.ring_level, LEVELS[level(ring_pct)])
            ring_w = PAD_X + RING_D + DOT_GAP + body_fm.horizontalAdvance(ring_text)
        title = title_fm.elidedText(text.title, ELIDE, inner - DOT - DOT_GAP - ring_w)
        widths = [DOT + DOT_GAP + title_fm.horizontalAdvance(title) + ring_w]

        lines = (text.labels, text.counters)
        rows = [Row(body_fm.elidedText(line, ELIDE, inner), TEXT_COLOR) for line in lines if line]
        bars = text.bars[:2]
        bar_label_w = max((body_fm.horizontalAdvance(bar.label) for bar in bars), default=0) + DOT_GAP
        lead = bar_label_w + BAR_W + DOT_GAP
        for bar in bars:
            pct = _percent(bar.pct)
            note = f"{round(pct)}%" + (SEPARATOR + bar.note if bar.note else "")
            color = LEVELS.get(bar.level, LEVELS[level(pct)])
            rows.append(Row(body_fm.elidedText(note, ELIDE, inner - lead), TEXT_COLOR, bar.label, pct, color))
            widths.append(lead + body_fm.horizontalAdvance(rows[-1].text))
        if not bars and text.cost:
            rows.append(Row(body_fm.elidedText(text.cost, ELIDE, inner), MUTED_COLOR))
        widths.extend(body_fm.horizontalAdvance(row.text) for row in rows if row.bar_label is None)

        speech_widths = [speech_fm.horizontalAdvance(line) for line in speech_lines]
        if text.status:
            body_w = _clamp(max(widths + speech_widths) + 2 * PAD_X, MIN_W, MAX_W)
            body_h = 2 * PAD_Y + header_h + len(rows) * (ROW_GAP + line_h)
            if speech_lines:
                body_h += speech_h + DIVIDER_GAP
        else:
            rows = []
            body_w = _clamp(max(speech_widths, default=0) + 2 * PAD_X, MIN_W, MAX_W)
            body_h = 2 * PAD_Y + speech_h
        # Before the first follow() there is no placement: draw above, with the tail centred.
        below = self.placement.below if self.placement is not None else False
        tail_x = self.placement.tail_x if self.placement is not None else self.width() // 2
        top = TAIL_H if below else self.height() - TAIL_H - SHADOW - body_h
        left = _clamp(tail_x - body_w // 2, 0, self.width() - body_w)
        tip_x = _clamp(tail_x, left + RADIUS + TAIL_W // 2, left + body_w - RADIUS - TAIL_W // 2)
        return BodyLayout(
            body=QRect(left, top, body_w, body_h),
            tip_x=tip_x,
            below=below,
            header_h=header_h,
            line_h=line_h,
            tone=TONES.get(text.tone, TONES["none"]),
            title=title,
            ring_pct=ring_pct,
            ring_text=ring_text,
            ring_color=ring_color,
            rows=tuple(rows),
            bar_label_w=bar_label_w,
            speech_lines=speech_lines,
            speech_line_h=speech_line_h,
            status=text.status,
        )

    def _wrap(self, text: str, width: int) -> tuple[str, ...]:
        """`text` broken into lines of at most `width` px, the last of SPEECH_LINES elided if it runs on.

        Breaks at spaces where there are any and between CJK characters anywhere, as Qt lays out text.
        """
        layout = QTextLayout(text, self.speech_font, self)
        layout.beginLayout()
        starts = []
        while (line := layout.createLine()).isValid():
            line.setLineWidth(width)
            starts.append(line.textStart())
        layout.endLayout()
        # Qt counts UTF-16 code units; map them to str indices so astral characters split cleanly.
        index_of, unit = {}, 0
        for i, ch in enumerate(text):
            index_of[unit] = i
            unit += 2 if ord(ch) > 0xFFFF else 1
        index_of[unit] = len(text)
        cuts = [index_of.get(start, len(text)) for start in starts] + [len(text)]
        lines = [text[a:b].strip() for a, b in zip(cuts, cuts[1:])]
        if len(lines) > SPEECH_LINES:
            rest = text[cuts[SPEECH_LINES - 1]:].strip()
            lines = lines[: SPEECH_LINES - 1] + [QFontMetrics(self.speech_font, self).elidedText(rest, ELIDE, width)]
        return tuple(line for line in lines if line)

    # --- painting -----------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        global _paint_error_logged
        self.paint_count += 1
        if self.text is None:
            return
        try:
            layout = self.body_layout(self.text)
            painter = QPainter(self)
            try:
                self._draw(painter, layout)
            finally:
                painter.end()
        except Exception:
            # An exception escaping a paint handler can take the event loop down; draw nothing instead.
            if not _paint_error_logged:
                _paint_error_logged = True
                log.exception("bubble paint failed")

    def _draw(self, painter: QPainter, layout: BodyLayout) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        shape = self._shape(layout)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(SHADOW_COLOR)
        painter.drawPath(shape.translated(0, SHADOW_DY))
        painter.setPen(QPen(BORDER, 1))
        painter.setBrush(FILL)
        painter.drawPath(shape)

        body = layout.body
        x, y = body.x() + PAD_X, body.y() + PAD_Y
        if layout.speech_lines:
            painter.setFont(self.speech_font)
            painter.setPen(TITLE_COLOR)
            for line in layout.speech_lines:
                painter.drawText(QRectF(x, y, MAX_W, layout.speech_line_h), LEFT, line)
                y += layout.speech_line_h
            if not layout.status:
                return
            rule_y = y + DIVIDER_GAP // 2 + 0.5
            painter.setPen(QPen(BORDER, 1))
            painter.drawLine(QPointF(x, rule_y), QPointF(body.x() + body.width() - PAD_X, rule_y))
            y += DIVIDER_GAP
        mid = y + layout.header_h / 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(layout.tone)
        painter.drawEllipse(QRectF(x, mid - DOT / 2, DOT, DOT))
        painter.setFont(self.title_font)
        painter.setPen(TITLE_COLOR)
        painter.drawText(QRectF(x + DOT + DOT_GAP, y, MAX_W, layout.header_h), LEFT, layout.title)
        painter.setFont(self.body_font)
        if layout.ring_text is not None and layout.ring_pct is not None:
            right = body.x() + body.width() - PAD_X
            text_w = painter.fontMetrics().horizontalAdvance(layout.ring_text)
            painter.setPen(TEXT_COLOR)
            painter.drawText(QRectF(right - text_w, y, text_w, layout.header_h), RIGHT, layout.ring_text)
            inset = RING_STROKE / 2
            ring = QRectF(right - text_w - DOT_GAP - RING_D, mid - RING_D / 2, RING_D, RING_D)
            ring = ring.adjusted(inset, inset, -inset, -inset)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(TRACK_COLOR, RING_STROKE))
            painter.drawEllipse(ring)
            if layout.ring_pct > 0:
                pen = QPen(layout.ring_color, RING_STROKE)
                pen.setCapStyle(Qt.PenCapStyle.FlatCap)
                painter.setPen(pen)
                # Qt angles are 1/16 degree counter-clockwise from 3 o'clock: start at 12, sweep clockwise.
                painter.drawArc(ring, 90 * 16, -round(layout.ring_pct * 3.6 * 16))

        y += layout.header_h
        for row in layout.rows:
            y += ROW_GAP
            text_x = x
            if row.bar_label is not None:
                painter.setPen(TEXT_COLOR)
                painter.drawText(QRectF(x, y, layout.bar_label_w, layout.line_h), LEFT, row.bar_label)
                track = QRectF(x + layout.bar_label_w, y + (layout.line_h - BAR_H) / 2, BAR_W, BAR_H)
                track_path = QPainterPath()
                track_path.addRoundedRect(track, BAR_RADIUS, BAR_RADIUS)
                painter.fillPath(track_path, TRACK_COLOR)
                painter.save()
                painter.setClipPath(track_path)  # the fill keeps the track's rounded ends
                painter.fillRect(QRectF(track.x(), track.y(), BAR_W * row.bar_pct / 100, BAR_H), row.bar_color)
                painter.restore()
                text_x = track.right() + DOT_GAP
            painter.setPen(row.color)
            painter.drawText(QRectF(text_x, y, MAX_W, layout.line_h), LEFT, row.text)
            y += layout.line_h

    @staticmethod
    def _shape(layout: BodyLayout) -> QPainterPath:
        """The body and its tail as one outline, so the border runs around the tail too."""
        body = QRectF(layout.body).adjusted(0.5, 0.5, -0.5, -0.5)  # a 1 px line on the pixel grid
        outline = QPainterPath()
        outline.addRoundedRect(body, RADIUS, RADIUS)
        tip = layout.tip_x + 0.5
        if layout.below:
            base, apex = body.top() + 1, body.top() - TAIL_H
        else:
            base, apex = body.bottom() - 1, body.bottom() + TAIL_H
        tail = QPainterPath()
        tail.moveTo(tip - TAIL_W / 2, base)
        tail.lineTo(tip, apex)
        tail.lineTo(tip + TAIL_W / 2, base)
        tail.closeSubpath()
        return outline.united(tail)

    # --- events and timers --------------------------------------------------------------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        handle = self.windowHandle()
        if not self._watching_screen and handle is not None:
            # Metrics can round differently at another scale, so re-measure the canvas there.
            handle.screenChanged.connect(lambda _screen: self._recompute_canvas())
            self._watching_screen = True

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.tick.stop()

    def _recompute_canvas(self) -> None:
        size = self.canvas_size()
        if size != self.size():
            self.setFixedSize(size)
        self.follow(self.pet.geometry())

    def _arm_tick(self, display: DisplayState, now: float) -> None:
        """Wake on the next minute rollover of a shown countdown; stop when none is shown."""
        due = next_tick(display, now)
        if due is None or not self.isVisible():
            self.tick.stop()
        else:
            self.tick.start(max(1, math.ceil((due - now) * 1000)))

    def _tick(self) -> None:
        self._resync()

    def _end_speech(self) -> None:
        self.speech = None
        self._resync()

    def _resync(self) -> bool:
        """Sync again with the last inputs, after a change only the bubble knows about."""
        if self._last is None:
            return False
        display, mode, show_subagents, pet_visible = self._last
        return self.sync(display, mode=mode, show_subagents=show_subagents, pet_visible=pet_visible)

    def _metrics(self) -> tuple[QFontMetrics, QFontMetrics]:
        return QFontMetrics(self.title_font, self), QFontMetrics(self.body_font, self)
