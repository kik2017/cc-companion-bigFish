"""What she knows: a small window listing her facts about you, where you can archive wrong ones.

Opened from Persona > What she knows. It reads her facts.json each time it opens:
facts you told her in the chat first, then the ones she has seen on KNOWN_AFTER_DAYS
days (both reach her prompts), then the ones she is not sure of yet. Archive marks a fact
"archived by you": it stays in her files, as nothing of hers is ever deleted, but she
stops using it at once, since her mind reads facts.json for every line. Undo brings back
the last one you archived.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QRect, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from . import settings as prefs
from .bubble import to_rect
from .placement import place_beside

KNOWN_AFTER_DAYS = 2  # as mind/facts.py
SIZE = (420, 380)
HEADER = (
    "What she has learned about you. Facts you told her, or that she has seen on two days, "
    "reach her lines; the rest stay unsure. Archive any that are wrong: she keeps them in "
    "her files but stops using them."
)
EMPTY = "Nothing yet. She learns about you while she sleeps, from her day and from what you tell her."


def status(fact: dict) -> str:
    days = len(fact["days"])
    if fact["told"]:
        return "you told her · known"
    if days >= KNOWN_AFTER_DAYS:
        return f"known · seen on {days} days"
    return f"not sure yet · seen on {days} day" + ("s" if days != 1 else "")


def ordered(facts: list[dict]) -> list[dict]:
    """Told first, then known, then unsure; the most reinforced first within each."""
    return sorted(facts, key=lambda f: (not f["told"], len(f["days"]) < KNOWN_AFTER_DAYS, -f["seen"], f["id"]))


def _plain(text: str, parent: QWidget, *, muted: bool = False) -> QLabel:
    label = QLabel(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)  # her facts are text, never markup
    label.setWordWrap(True)
    if muted:
        label.setStyleSheet("color: #8C8C8C; font-size: 11px;")
    return label


class KnowsWindow(QWidget):
    def __init__(self, pet: QWidget, area_for: Callable[[QRect], QRect] | None = None) -> None:
        # Owned by the pet, so it stays above it and off the taskbar.
        super().__init__(pet, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("What she knows")
        self.pet = pet
        self.area_for = area_for or (lambda rect: pet.bubble.area_for(rect))
        self.last_archived: tuple[str, str] | None = None  # (id, text) for Undo

        layout = QVBoxLayout(self)
        layout.addWidget(_plain(HEADER, self, muted=True))
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(self.scroll, 1)
        footer = QHBoxLayout()
        self.undo_label = _plain("", self)
        self.undo_label.setStyleSheet("color: #8C8C8C;")  # full size: CJK falls back unevenly at 11 px
        self.undo_button = QPushButton("Undo", self)
        self.undo_button.setObjectName("undo")
        self.undo_button.clicked.connect(self.undo)
        footer.addWidget(self.undo_label, 1)
        footer.addWidget(self.undo_button)
        layout.addLayout(footer)
        self._show_undo(False)
        self.resize(*SIZE)
        self.refresh()

    def open(self) -> None:
        """Read her facts again and show the window beside the pet."""
        self.refresh()
        rect = place_beside(to_rect(self.pet.geometry()), (self.width(), self.height()), to_rect(self.area_for(self.pet.geometry())))
        self.move(rect.x, rect.y)
        self.show()
        self.raise_()
        self.activateWindow()

    def refresh(self) -> None:
        facts = ordered(prefs.read_facts())
        rows = QWidget()
        column = QVBoxLayout(rows)
        column.setContentsMargins(0, 4, 0, 4)
        column.setSpacing(8)
        if not facts:
            column.addWidget(_plain(EMPTY, rows))
        for fact in facts:
            row = QWidget(rows)
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            words = QVBoxLayout()
            words.setSpacing(1)
            words.addWidget(_plain(fact["text"], row))
            words.addWidget(_plain(status(fact), row, muted=True))
            line.addLayout(words, 1)
            button = QPushButton("Archive", row)
            button.setObjectName(f"archive:{fact['id']}")
            button.clicked.connect(lambda _checked=False, fact=fact: self.archive(fact["id"], fact["text"]))
            line.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)
            column.addWidget(row)
        column.addStretch(1)
        self.scroll.setWidget(rows)
        self.facts = facts

    def archive(self, fact_id: str, text: str) -> bool:
        if not prefs.set_fact_archived(fact_id, True):
            return False
        self.last_archived = (fact_id, text)
        self.undo_label.setText(f"Archived: {text}")
        self._show_undo(True)
        self.refresh()
        return True

    def undo(self) -> bool:
        if self.last_archived is None or not prefs.set_fact_archived(self.last_archived[0], False):
            return False
        self.last_archived = None
        self._show_undo(False)
        self.refresh()
        return True

    def _show_undo(self, shown: bool) -> None:
        self.undo_label.setVisible(shown)
        self.undo_button.setVisible(shown)
        if not shown:
            self.undo_label.setText("")
