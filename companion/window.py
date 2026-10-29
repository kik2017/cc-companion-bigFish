"""The companion window: frameless, always on top, off the taskbar and alt-tab, draggable.

It paints the frame the animator picks and turns the mouse into reactions: a press that
moves more than DRAG_THRESHOLD_PX is a drag, otherwise the release is a click on the
skin zone under the pointer (head, tail, else body), and a double-click is a head pat.
It owns the speech bubble, which follows it on every move and resize. say() takes the
persona's line and mood from POST /say. It also owns the chat box (chat.py), opened from
Persona > Talk to her, which follows it the same way. The right-click menu changes
settings live and saves them.
"""

from __future__ import annotations

import logging
import sys
import time
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QDir, QPoint, QProcess, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QGuiApplication, QImageReader, QPainter, QPixmap
from PySide6.QtWidgets import QInputDialog, QMenu, QWidget

from . import apikey
from . import settings as prefs
from . import skin as skins
from .animator import FADE_S, Animator, Frame
from .bubble import BubbleWindow, to_rect
from .chat import ChatBox
from .knows import KnowsWindow
from .model import DisplayState
from .placement import Rect, clamp_into, resize_keep_feet
from .sounds import SoundPlayer, cue_for

log = logging.getLogger("companion")

EDGE_MARGIN = 40
DRAG_THRESHOLD_PX = 5
# Holds one 241-frame clip at M and 1x (87.2 MB at 329x275), so the idle loop stays cached.
# At L and 1x (136.6 MB per clip) or on a 1.5x screen, frames decode on the fly, which
# measured ~1.5 ms each, under 4% of a core at 24 fps.
FRAME_CACHE_BYTES = 96 * 1024 * 1024
FADE_STEP_S = 0.016

# Radio submenus: (title, setting, ((label, value), ...)).
MENU_CHOICES = (
    ("Size", "size", (("Small", "S"), ("Medium", "M"), ("Large", "L"))),
    (
        "Bubble",
        "bubble_mode",
        (("Always", "always"), ("Only for waiting, errors and done", "attention"), ("Never", "hidden")),
    ),
    ("Idle moves", "activity", (("Rarely", "quiet"), ("Sometimes", "normal"), ("Often", "lively"))),
    ("Persona", "persona", (("Off", "off"), ("On", "on"), ("Quiet: moods only, no words", "quiet"))),
)
MENU_TOGGLES = (("Reduce motion", "reduced_motion"), ("Show subagent count", "subagents"), ("Sounds", "sounds"))
CHATTINESS_LABELS = (("Quiet", "quiet"), ("Normal", "normal"), ("Chatty", "chatty"))
NO_GAP_LABEL = "No pause"
NO_GAP_TIP = (
    "Warning: this might incur high API cost.\n"
    "Every line she says is an API call. With no pause she can speak each time a moment comes:\n"
    "no 60-second gap between lines, no 2-minute hush after a chat, no cooldown on pats,\n"
    "no 20-second spacing on screen. Your limit (Persona > Limit) still applies."
)
LIMIT_TIP = (
    "Warning: this might incur high API cost.\n"
    "Every line and chat reply is one API call, about $0.0001-0.0003 each with Haiku."
)
DEFAULT_CUSTOM_LINES = 300
DAILY_LINES_PROMPT = (
    "Lines and chat replies a day (counted until midnight).\n"
    "Each is one API call, about $0.0001-0.0003 with Haiku,\n"
    "so 1,000 a day is roughly $0.10-0.30."
)
# Searches an answer, as mind/budget.py's SEARCHES_PER_ANSWER.
SEARCH_TIP = (
    "She searches only to answer what you type in Talk to her, at most 2 searches an answer.\n"
    "What you typed goes to Anthropic's web search as the query.\n"
    "Each search costs about $0.01, plus about $0.001-0.002 for the pages she reads."
)
SEARCH_WARNING = "Warning: this might incur high API cost.\n" + SEARCH_TIP
DEFAULT_CUSTOM_SEARCHES = 30
DAILY_SEARCHES_PROMPT = (
    "Web searches a day (counted until midnight), for her chat answers only.\n"
    "Each is about $0.01 plus the pages she reads,\n"
    "so 100 a day is roughly $1.10-1.20."
)

# "first paint" is logged once per process; a relaunch is timed from spawn to this line.
_first_paint_logged = False


def reveal(folder: Path) -> bool:
    """Show `folder` in the file manager, in front.

    On Windows, Explorer is started on it with /select on her identity.md: handing a
    folder URL to the shell can just raise an Explorer window that is already open on
    some other folder. Elsewhere, or if that fails, the desktop's URL handler opens it.
    """
    if sys.platform == "win32":
        target = folder / "identity.md"
        native = QDir.toNativeSeparators(str(target if target.exists() else folder))
        process = QProcess()
        process.setProgram("explorer.exe")
        process.setNativeArguments(f'/select,"{native}"' if target.exists() else f'"{native}"')
        started = process.startDetached()
        if (started[0] if isinstance(started, tuple) else started):
            return True
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def spend_text() -> str:
    """The menu's readout of today's lines, searches (once search is on or used) and dollars, against her limits."""
    lines, usd = prefs.read_spend_today()
    limit = prefs.read_limit()
    text = f"Today: {lines:,} lines" + (f" of {limit:,}" if isinstance(limit, int) else "")
    searches, search = prefs.read_searches_today(), prefs.read_search()
    if searches or search != "off":
        allowed = prefs.read_normal_searches() if search == "normal" else search
        text += f" · {searches:,} searches" + (f" of {allowed:,}" if isinstance(allowed, int) else "")
    text += f" · ${usd:.3f}"
    if limit == "normal":
        text += f" of ${prefs.read_normal_limit()[1]:.2f}"
    return text


def ask_daily_lines(start: int) -> int | None:
    """Ask for a daily number of lines and replies, kept on top; None if cancelled."""
    return _ask_count("Her daily limit", DAILY_LINES_PROMPT, start, prefs.MAX_DAILY_LINES, 50)


def ask_daily_searches(start: int) -> int | None:
    """Ask for a daily number of web searches, kept on top; None if cancelled."""
    return _ask_count("Her web searches", DAILY_SEARCHES_PROMPT, start, prefs.MAX_DAILY_SEARCHES, 10)


def _ask_count(title: str, label: str, start: int, maximum: int, step: int) -> int | None:
    dialog = QInputDialog()
    dialog.setWindowTitle(title)
    dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
    dialog.setInputMode(QInputDialog.InputMode.IntInput)
    dialog.setLabelText(label)
    dialog.setIntRange(1, maximum)
    dialog.setIntStep(step)
    dialog.setIntValue(start)
    return dialog.intValue() if dialog.exec() else None


def head_anchor_x(skin: skins.Skin) -> float:
    """Centre of the skin's head zone as a fraction of the frame width, or 0.5 without one."""
    head = next((zone for zone in skin.zones if zone.name == "head"), None)
    return 0.5 if head is None else (head.left + head.right) / 2


class FrameCache:
    """Frames decoded at display size, least recently used dropped first.

    A skin can hold thousands of frames, so nothing is preloaded: a looping clip is
    decoded once on its first pass and then served from here.
    """

    def __init__(self, limit_bytes: int = FRAME_CACHE_BYTES) -> None:
        self.limit = limit_bytes
        self.used = 0
        self.pixmaps: OrderedDict[tuple[Path, int, int], QPixmap] = OrderedDict()

    def get(self, path: Path, size: QSize, ratio: float) -> QPixmap:
        width, height = round(size.width() * ratio), round(size.height() * ratio)
        key = (path, width, height)
        pixmap = self.pixmaps.get(key)
        if pixmap is not None:
            self.pixmaps.move_to_end(key)
            return pixmap
        reader = QImageReader(str(path))
        reader.setScaledSize(QSize(width, height))
        pixmap = QPixmap.fromImage(reader.read())
        pixmap.setDevicePixelRatio(ratio)
        self.pixmaps[key] = pixmap
        self.used += width * height * 4
        while self.used > self.limit and len(self.pixmaps) > 1:
            (_, w, h), _ = self.pixmaps.popitem(last=False)
            self.used -= w * h * 4
        return pixmap

    def clear(self) -> None:
        """Drop every frame; after a size change none of them is the right size."""
        self.pixmaps.clear()
        self.used = 0


class CompanionWindow(QWidget):
    quit_requested = Signal()
    hide_requested = Signal()
    patted = Signal(str)  # a click reaction started on this zone: head, body or tail
    persona_changed = Signal(str)  # the Persona setting's new value
    chattiness_changed = Signal(str)  # quiet, normal or chatty, saved in her config.json
    chat_sent = Signal(str)  # a line typed to her in the chat box
    private_changed = Signal(object)  # the day of hers now private (YYYY-MM-DD), or None: off
    no_gap_changed = Signal(bool)  # No pause, saved in her config.json
    api_key_needed = Signal()  # the persona was switched on with no API key to be found
    limit_changed = Signal(object)  # "normal", "unlimited" or N lines a day, saved in her config.json
    search_changed = Signal(object)  # web search: "off", "normal", "unlimited" or N a day, saved in her config.json

    def __init__(
        self,
        settings: prefs.Settings,
        skin: skins.Skin | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # Tool keeps the window off the taskbar and out of alt-tab; WindowDoesNotAcceptFocus
        # stops a drag or click from pulling focus away from the app you are typing in.
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.settings = settings
        self.skin = skin or skins.resolve(settings.skin)
        self.clock = clock
        # Before the first resize or move, whose events make the bubble follow.
        self.bubble = BubbleWindow(self, anchor_x=head_anchor_x(self.skin))
        # Through the bubble's area lookup, so a test's injected area applies to both.
        self.chat = ChatBox(self, lambda rect: self.bubble.area_for(rect))
        self.chat.submitted.connect(self.chat_sent)
        self.knows = KnowsWindow(self)
        self.sounds = SoundPlayer()
        if settings.sounds:
            self.sounds.preload()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("cc-companion")
        self.setFixedSize(self._pet_size())
        self.setCursor(Qt.CursorShape.OpenHandCursor)

        self.animator = Animator(
            self.skin, clock(), activity=settings.activity, reduced_motion=settings.reduced_motion
        )
        self.frames = FrameCache()
        self.display: DisplayState | None = None
        self.shown: Frame | None = None
        self.shown_pixmap: QPixmap | None = None
        self.fade_from: QPixmap | None = None
        self.fade_started = 0.0
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.refresh)

        self._press_global: QPoint | None = None
        self._drag_offset = QPoint()
        self._dragging = False
        self._release_is_click = True
        # Shows the persona folder; tests swap it so no Explorer window opens.
        self.reveal: Callable[[Path], bool] = reveal
        # Ask for a custom daily limit or number of searches; tests swap them so no dialog opens.
        self.ask_daily_lines: Callable[[int], int | None] = ask_daily_lines
        self.ask_daily_searches: Callable[[int], int | None] = ask_daily_searches
        # Whether her mind will find an API key; tests swap it so the registry is never read.
        self.has_api_key: Callable[[], bool] = apikey.has_api_key
        self.move(self._initial_position())

    def set_display(self, display: DisplayState) -> bool:
        """Show `display`; True if the bubble was repainted within this call, False if nothing changed."""
        if display == self.display:
            return False
        previous, self.display = self.display, display
        cue = cue_for(previous, display) if self.settings.sounds else None
        if cue:
            self.sounds.play(cue)
        now = self.clock()
        self.animator.set_state(display.state, now)
        self.animator.set_usage(display.quota.five_hour_pct, now)
        self.refresh()
        return self._sync_bubble()

    def say(self, text: str | None, mood: str, ttl_s: float, reply: bool = False, cue: str | None = None) -> None:
        """Her line, mood and cue from /say; the line (if any) shows unless she is quiet.

        Her body: the cue's clip (her greeting, her sleep), else a talking clip while a line
        shows, else the mood clip if nothing else is playing. A reply answers the chat box,
        which stops waiting.
        """
        persona = self.settings.persona
        if persona == "off":
            return
        now = self.clock()
        shown = persona == "on" and bool(text)
        gestures = (self.skin.cue_clips(cue) if cue else ()) or (self.skin.talk_clips() if shown else ())
        if gestures and self.animator.gesture(gestures, now):
            self.refresh()
        elif (clip := self.skin.mood_clip(mood)) is not None and self.animator.play_mood(clip, now):
            self.refresh()
        if persona == "on" and text:
            self.bubble.say(text, ttl_s)
        if reply:
            self.chat.answered(text if persona == "on" else None)

    def open_persona_folder(self) -> None:
        folder = prefs.persona_dir()
        folder.mkdir(parents=True, exist_ok=True)
        self.reveal(folder)

    def open_knows(self) -> None:
        self.knows.open()

    def open_chat(self) -> None:
        if self.settings.persona == "on":
            self.chat.open_beside(self.geometry())

    def set_private(self, private: bool) -> bool:
        """Mark her current day private (nothing of it is remembered) or not; False if it could not be saved."""
        day = prefs.her_day() if private else None
        if not prefs.write_private_day(day):
            return False
        self.private_changed.emit(day)
        return True

    def set_no_gap(self, on: bool) -> bool:
        """Lift or restore the pauses between her lines; False if it could not be saved."""
        if not prefs.write_no_gap(on):
            return False
        self.no_gap_changed.emit(bool(on))
        return True

    def set_limit(self, limit: str | int) -> bool:
        """Save her limit ("normal", "unlimited" or N lines a day) and tell the mind; False if rejected."""
        if not prefs.write_limit(limit):
            return False
        self.limit_changed.emit(limit)
        return True

    def choose_daily_limit(self) -> bool:
        """Ask for N lines a day and set it; False if cancelled."""
        current = prefs.read_limit()
        chosen = self.ask_daily_lines(current if isinstance(current, int) else DEFAULT_CUSTOM_LINES)
        return chosen is not None and self.set_limit(chosen)

    def set_search(self, search: str | int) -> bool:
        """Save her web search ("off", "normal", "unlimited" or N a day) and tell the mind; False if rejected."""
        if not prefs.write_search(search):
            return False
        self.search_changed.emit(search)
        return True

    def choose_daily_searches(self) -> bool:
        """Ask for N searches a day and set it; False if cancelled."""
        current = prefs.read_search()
        chosen = self.ask_daily_searches(current if isinstance(current, int) else DEFAULT_CUSTOM_SEARCHES)
        return chosen is not None and self.set_search(chosen)

    def set_chattiness(self, level: str) -> bool:
        """Save her chattiness and tell the mind; False, with nothing changed, if `level` is not a choice."""
        if level == prefs.read_chattiness():
            return True
        if not prefs.write_chattiness(level):
            return False
        self.chattiness_changed.emit(level)
        return True

    def refresh(self) -> None:
        """Repaint now and wake again when the animator's frame or a fade next changes."""
        self.update()
        if not self.isVisible():
            self.timer.stop()
            return
        now = self.clock()
        upcoming = self.animator.next_change(now)
        if self.fade_from is not None:
            upcoming = min(upcoming or now + FADE_STEP_S, now + FADE_STEP_S)
        if upcoming is None:
            self.timer.stop()
        else:
            self.timer.start(max(1, int((upcoming - now) * 1000) + 1))

    def apply_setting(self, name: str, value: object) -> bool:
        """Validate one setting, apply it live and save it; False, with nothing saved, if it is rejected."""
        previous = getattr(self.settings, name, None)
        if not prefs.update(self.settings, name, value):
            return False
        if value == previous:
            return True  # the current choice picked again: nothing to apply or save
        now = self.clock()
        if name == "size":
            self._apply_size()
        elif name == "reduced_motion":
            self.animator.set_reduced_motion(value, now)
            self.refresh()
        elif name == "activity":
            self.animator.set_activity(value, now)
            self.refresh()
        elif name in ("bubble_mode", "subagents"):
            self._sync_bubble()
        elif name == "sounds" and value:
            self.sounds.preload()
        elif name == "persona":
            if value != "on":
                self.bubble.say(None)  # off or quiet: her current line goes too
                self.chat.hide()
            self.persona_changed.emit(value)
            if value != "off" and not self.has_api_key():
                self.api_key_needed.emit()
        prefs.save(self.settings)
        return True

    def _sync_bubble(self) -> bool:
        return self.bubble.sync(
            self.display,
            mode=self.settings.bubble_mode,
            show_subagents=self.settings.subagents,
            pet_visible=self.isVisible(),
        )

    def _pet_size(self) -> QSize:
        """The size setting's height, with the width following the skin's frame aspect."""
        frame_w, frame_h = self.skin.frame_size
        height = self.settings.size_px
        return QSize(round(height * frame_w / frame_h), height)

    def _apply_size(self) -> None:
        """Resize around the same feet (bottom centre), kept fully inside the screen area."""
        size = self._pet_size()
        # The bubble's area lookup is the pet's too: the screen holding the pet's centre.
        area = to_rect(self.bubble.area_for(self.geometry()))
        rect = resize_keep_feet(to_rect(self.geometry()), size.width(), size.height(), area)
        self.fade_from = None
        self.frames.clear()
        self.setFixedSize(size)
        self.move(rect.x, rect.y)
        self.remember_position()
        self.refresh()

    def _initial_position(self) -> QPoint:
        """Saved position, kept fully inside its screen, if it still lands on one; else the primary screen's corner."""
        if self.settings.x is not None and self.settings.y is not None:
            saved = QPoint(self.settings.x, self.settings.y)
            screen = QGuiApplication.screenAt(saved + self.rect().center())
            if screen is not None:
                # A spot saved at a smaller size can overhang the edge, or onto the next screen.
                rect = Rect(saved.x(), saved.y(), self.width(), self.height())
                rect = clamp_into(rect, to_rect(screen.availableGeometry()))
                return QPoint(rect.x, rect.y)
        area = QGuiApplication.primaryScreen().availableGeometry()
        return QPoint(area.right() - self.width() - EDGE_MARGIN, area.bottom() - self.height() - EDGE_MARGIN)

    # --- painting -----------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        global _first_paint_logged
        now = self.clock()
        frame = self.animator.frame(now)
        pixmap = self.frames.get(frame.path, self.size(), self.devicePixelRatioF())
        if self.shown is not None and self.shown_pixmap is not None and self.animator.fades(self.shown.clip, frame.clip):
            self.fade_from, self.fade_started = self.shown_pixmap, now
        self.shown, self.shown_pixmap = frame, pixmap

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        target = QRectF(self.rect())
        alpha = 1.0
        if self.fade_from is not None:
            alpha = (now - self.fade_started) / FADE_S
            if alpha < 1.0:
                # The old frame stays opaque underneath so the pet never flashes see-through.
                painter.drawPixmap(target, self.fade_from, QRectF(self.fade_from.rect()))
            else:
                self.fade_from, alpha = None, 1.0
        painter.setOpacity(max(0.0, min(1.0, alpha)))
        painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))
        step_ms = int(FADE_STEP_S * 1000)
        if self.fade_from is not None and self.isVisible() and not 0 < self.timer.remainingTime() <= step_ms:
            self.timer.start(step_ms)
        if not _first_paint_logged and self.isVisible():
            _first_paint_logged = True
            log.info("first paint")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()
        self._sync_bubble()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.timer.stop()
        self.bubble.hide()  # an owned window stays up when its owner hides
        self.chat.hide()

    def closeEvent(self, event) -> None:
        self.bubble.close()
        self.chat.close()
        self.knows.close()
        super().closeEvent(event)

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self.bubble.follow(self.geometry())
        self.chat.follow(self.geometry())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.bubble.follow(self.geometry())
        self.chat.follow(self.geometry())

    # --- mouse --------------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._drag_offset = self._press_global - self.pos()
            self._dragging = False
            self._release_is_click = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._press_global is None or not event.buttons() & Qt.MouseButton.LeftButton:
            return
        position = event.globalPosition().toPoint()
        if not self._dragging and (position - self._press_global).manhattanLength() > DRAG_THRESHOLD_PX:
            self._dragging = True
            self.animator.drag_start(self.clock())
            self.refresh()
        if self._dragging:
            self.move(position - self._drag_offset)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._press_global is None:
            return
        if self._dragging:
            self.animator.drag_end(self.clock())
            self.remember_position()
        elif self._release_is_click:
            point = event.position()
            zone = self.skin.zone_at(point.x() / self.width(), point.y() / self.height())
            if self.animator.click(zone, self.clock()):
                self.patted.emit(zone)
        self._press_global = None
        self._dragging = False
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.refresh()
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.mousePressEvent(event)
            self._release_is_click = False  # this press is the pat; its release is not a click
            if self.animator.double_click(self.clock()):
                self.patted.emit("head")
            self.refresh()

    def remember_position(self) -> None:
        self.settings.x, self.settings.y = self.x(), self.y()
        prefs.save(self.settings)

    # --- menu ---------------------------------------------------------------------------

    def build_menu(self, menu: QMenu | None = None) -> QMenu:
        """The right-click menu, built on every open so each check mark reads the current settings.

        Every settings action is named "<setting>:<value>" ("<setting>:toggle" for a check
        item) and carries (setting, value) as its data. The tray passes its own menu to fill.
        """
        menu = menu if menu is not None else QMenu(self)
        for title, name, choices in MENU_CHOICES:
            submenu = menu.addMenu(title)
            group = QActionGroup(submenu)
            group.setExclusive(True)
            for label, value in choices:
                action = self._setting_action(submenu, label, name, value, value)
                action.setChecked(getattr(self.settings, name) == value)
                group.addAction(action)
            if name == "persona":
                submenu.addSeparator()
                spend = submenu.addAction(spend_text())
                spend.setObjectName("persona:spend")
                spend.setEnabled(False)  # a readout, not a command
                talk = submenu.addAction("Talk to her…", self.open_chat)
                talk.setObjectName("persona:talk")
                talk.setEnabled(self.settings.persona == "on")
                submenu.addAction("What she knows…", self.open_knows).setObjectName("persona:knows")
                private = submenu.addAction("Private today: remember nothing")
                private.setObjectName("persona:private")
                private.setCheckable(True)
                private.setChecked(prefs.read_private_day() == prefs.her_day())
                private.triggered.connect(self.set_private)
                no_gap = submenu.addAction(NO_GAP_LABEL)
                no_gap.setObjectName("persona:no_gap")
                no_gap.setCheckable(True)
                no_gap.setChecked(prefs.read_no_gap())
                no_gap.setToolTip(NO_GAP_TIP)
                no_gap.triggered.connect(self.set_no_gap)
                submenu.setToolTipsVisible(True)  # menus hide action tooltips unless asked
                self._limit_menu(submenu.addMenu("Limit"))
                self._search_menu(submenu.addMenu("Web search"))
                chatty = submenu.addMenu("Chattiness")
                chatty_group = QActionGroup(chatty)
                chatty_group.setExclusive(True)
                current = prefs.read_chattiness()
                for label, level in CHATTINESS_LABELS:
                    item = chatty.addAction(label)
                    item.setObjectName(f"chattiness:{level}")
                    item.setCheckable(True)
                    item.setChecked(level == current)
                    item.triggered.connect(lambda _checked=False, level=level: self.set_chattiness(level))
                    chatty_group.addAction(item)
                submenu.addAction("Open her folder", self.open_persona_folder).setObjectName("persona:folder")
        for label, name in MENU_TOGGLES:
            current = getattr(self.settings, name)
            self._setting_action(menu, label, name, not current, "toggle").setChecked(current)
        menu.addSeparator()
        menu.addAction("Hide until all sessions end", self.hide_requested.emit).setObjectName("hide")
        menu.addAction("Quit", self.quit_requested.emit).setObjectName("quit")
        return menu

    def _limit_menu(self, menu: QMenu) -> None:
        """Persona > Limit: Normal, Unlimited, or a custom number of lines a day; the last two warn of cost."""
        menu.setToolTipsVisible(True)
        current = prefs.read_limit()
        lines, usd = prefs.read_normal_limit()
        custom = f"Custom: {current:,} lines a day…" if isinstance(current, int) else "Custom: lines a day…"
        items = (
            ("normal", f"Normal: {lines} lines an hour, ${usd:.2f} a day", None, lambda: self.set_limit("normal")),
            ("unlimited", "Unlimited", LIMIT_TIP, lambda: self.set_limit("unlimited")),
            ("custom", custom, LIMIT_TIP, self.choose_daily_limit),
        )
        self._choice_items(menu, "limit", current, items)

    def _search_menu(self, menu: QMenu) -> None:
        """Persona > Web search: Off, Normal, Unlimited, or a custom number of searches a day; each says what it costs."""
        menu.setToolTipsVisible(True)
        current = prefs.read_search()
        per_day = prefs.read_normal_searches()
        custom = f"Custom: {current:,} searches a day…" if isinstance(current, int) else "Custom: searches a day…"
        items = (
            ("off", "Off", None, lambda: self.set_search("off")),
            ("normal", f"Normal: {per_day} searches a day", SEARCH_TIP, lambda: self.set_search("normal")),
            ("unlimited", "Unlimited", SEARCH_WARNING, lambda: self.set_search("unlimited")),
            ("custom", custom, SEARCH_WARNING, self.choose_daily_searches),
        )
        self._choice_items(menu, "search", current, items)

    def _choice_items(self, menu: QMenu, name: str, current: str | int, items: tuple) -> None:
        """One exclusive choice per (key, label, tooltip, run); "custom" is checked when `current` is a number."""
        group = QActionGroup(menu)
        group.setExclusive(True)
        for key, label, tip, run in items:
            item = menu.addAction(label)
            item.setObjectName(f"{name}:{key}")
            item.setCheckable(True)
            item.setChecked(key == current or (key == "custom" and isinstance(current, int)))
            if tip:
                item.setToolTip(tip)
            item.triggered.connect(lambda _checked=False, run=run: run())
            group.addAction(item)

    def _setting_action(self, menu: QMenu, label: str, name: str, value: object, key: str) -> QAction:
        action = menu.addAction(label)
        action.setObjectName(f"{name}:{key}")
        action.setData((name, value))
        action.setCheckable(True)
        action.triggered.connect(lambda: self.apply_setting(name, value))
        return action

    def contextMenuEvent(self, event) -> None:
        menu = self.build_menu()
        menu.exec(event.globalPos())
        menu.deleteLater()  # a fresh one is built on the next open
