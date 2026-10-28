"""The chat box: a small text field beside the pet for talking to her.

Opened from Persona > Talk to her while the persona is On. Enter sends the line (at most
CHAT_MAX_CHARS) through `submitted`, and the field waits for her answer, which comes back
as a /say reply and shows in her bubble, for at most REPLY_TIMEOUT_S. Esc closes it, and
so does IDLE_CLOSE_S with nothing typed.

Above the field, today's conversation (her day, 05:00 to 05:00) scrolls: your lines on
the right, hers on the left, so an answer missed in the bubble can be read back. It is
kept in this process's memory only, for this run of the daemon; what you type goes only
to the persona's mind, never to the log or a file.
"""

from __future__ import annotations

import html
from collections.abc import Callable

from PySide6.QtCore import QRect, Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QLabel, QLineEdit, QTextEdit, QVBoxLayout, QWidget

from . import settings as prefs
from .bubble import to_rect
from .placement import place_beside

CHAT_MAX_CHARS = 300  # mind/core.py cuts to the same
REPLY_TIMEOUT_S = 45
IDLE_CLOSE_S = 180
WIDTH = 280
INNER_W = WIDTH - 24  # the frame's side margins
HISTORY_KEPT = 40  # lines of today's conversation kept for the box
HISTORY_MAX_H = 180  # past this the history scrolls
PLACEHOLDER = "Say something to her…"
HINT = "Enter sends · Esc closes"
THINKING = "She's thinking…"
WORDLESS = "She answered without words."
NO_ANSWER = "No answer this time. Try again in a moment."
YOURS = '<p align="right" style="margin: 3px 0 3px 36px; color: #6B6B6B;">{}</p>'
HERS = '<p style="margin: 3px 36px 3px 0; color: #1F1F1F;">{}</p>'

STYLE = """
#chatFrame { background: rgba(251, 250, 247, 245); border: 1px solid rgba(214, 209, 199, 230); border-radius: 12px; }
#chatFrame QLineEdit { background: transparent; border: none; color: #1F1F1F; font-size: 14px; padding: 2px 0; }
#chatFrame QTextEdit { background: transparent; border: none; border-bottom: 1px solid rgba(214, 209, 199, 200); font-size: 13px; }
#chatFrame QLabel { color: #8C8C8C; font-size: 12px; }
"""


class ChatBox(QWidget):
    submitted = Signal(str)  # one line you typed, already stripped

    def __init__(
        self, pet: QWidget, area_for: Callable[[QRect], QRect], today: Callable[[], str] = prefs.her_day
    ) -> None:
        # Owned by the pet, so it stays above it and off the taskbar; unlike the pet and the
        # bubble it takes focus, since you type into it.
        super().__init__(pet, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Talk to her")
        self.setStyleSheet(STYLE)
        self.pet = pet
        self.area_for = area_for
        self.today = today
        self.waiting = False
        self.history: list[tuple[str, str, str]] = []  # (her day, "you" or "her", text)

        self.frame = frame = QFrame(self)
        frame.setObjectName("chatFrame")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)
        inner = QVBoxLayout(frame)
        inner.setContentsMargins(12, 8, 12, 8)
        inner.setSpacing(2)
        self.log = QTextEdit(frame)
        self.log.setReadOnly(True)
        self.log.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.log.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.log.setFixedWidth(INNER_W)
        self.log.hide()
        self.edit = QLineEdit(frame)
        self.edit.setMaxLength(CHAT_MAX_CHARS)
        self.edit.setPlaceholderText(PLACEHOLDER)
        self.edit.returnPressed.connect(self._send)
        self.edit.textEdited.connect(self._arm_idle)
        self.status = QLabel(HINT, frame)
        inner.addWidget(self.log)
        inner.addWidget(self.edit)
        inner.addWidget(self.status)
        self.setFixedWidth(WIDTH)
        self.adjustSize()

        self.reply_timer = QTimer(self)
        self.reply_timer.setSingleShot(True)
        self.reply_timer.timeout.connect(self._no_answer)
        self.idle_timer = QTimer(self)
        self.idle_timer.setSingleShot(True)
        self.idle_timer.timeout.connect(self.hide)

    def open_beside(self, pet_geometry: QRect) -> None:
        """Show beside the pet and take the keyboard."""
        self._show_history()  # yesterday's conversation goes once her day has turned
        self.follow(pet_geometry, force=True)
        self.show()
        self.raise_()
        self.activateWindow()
        self.edit.setFocus()
        if not self.waiting:
            self._arm_idle()

    def follow(self, pet_geometry: QRect, force: bool = False) -> None:
        if not (force or self.isVisible()):
            return
        rect = place_beside(to_rect(pet_geometry), (self.width(), self.height()), to_rect(self.area_for(pet_geometry)))
        self.move(rect.x, rect.y)

    def answered(self, text: str | None) -> None:
        """Her reply arrived: her words, or None for a mood alone. Kept in the history even if the box was closed."""
        if text:
            self._add("her", text)
        if not self.waiting:
            return
        self._set_waiting(False)
        self.status.setText(HINT if text else WORDLESS)

    def shown_history(self) -> list[tuple[str, str]]:
        """Today's conversation as the box shows it: (who, text), oldest first."""
        day = self.today()
        return [(who, text) for d, who, text in self.history if d == day]

    def _add(self, who: str, text: str) -> None:
        day = self.today()
        self.history = [entry for entry in self.history if entry[0] == day][-(HISTORY_KEPT - 1):] + [(day, who, text)]
        self._show_history()

    def _show_history(self) -> None:
        """Draw today's conversation, at most HISTORY_MAX_H tall and scrolled to the end; hidden when empty."""
        lines = self.shown_history()
        if not lines:
            self.history = []
            self.log.hide()
        else:
            self.log.setHtml("".join((YOURS if who == "you" else HERS).format(html.escape(text)) for who, text in lines))
            document = self.log.document()
            document.setTextWidth(INNER_W)
            self.log.setFixedHeight(min(HISTORY_MAX_H, int(document.size().height()) + 4))
            self.log.show()
            bar = self.log.verticalScrollBar()
            bar.setValue(bar.maximum())
        # A hidden window's layouts update lazily, from the event loop: settle them inside out
        # now, or the size (and with it the placement) is stale.
        self.frame.layout().activate()
        self.frame.updateGeometry()
        self.layout().activate()
        self.adjustSize()
        self.follow(self.pet.geometry())

    def _send(self) -> None:
        text = " ".join(self.edit.text().split())
        if not text or self.waiting:
            return
        self.edit.clear()
        self._add("you", text)
        self._set_waiting(True)
        self.submitted.emit(text)

    def _set_waiting(self, waiting: bool) -> None:
        self.waiting = waiting
        self.edit.setReadOnly(waiting)
        self.status.setText(THINKING if waiting else HINT)
        if waiting:
            self.idle_timer.stop()
            self.reply_timer.start(REPLY_TIMEOUT_S * 1000)
        else:
            self.reply_timer.stop()
            self._arm_idle()

    def _no_answer(self) -> None:
        self._set_waiting(False)
        self.status.setText(NO_ANSWER)

    def _arm_idle(self, *_args) -> None:
        self.idle_timer.start(IDLE_CLOSE_S * 1000)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.idle_timer.stop()
        self.reply_timer.stop()
        self.waiting = False
        self.edit.setReadOnly(False)
        self.status.setText(HINT)
