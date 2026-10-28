"""Done and error sounds: which display change earns one, and a small player.

cue_for() is pure. SoundPlayer wraps QSoundEffect, loads each WAV once and plays it
without blocking. Sounds are off unless `sounds` is set in settings.json or the menu.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QSoundEffect

from .model import DisplayState, State

SOUNDS_DIR = Path(__file__).parent / "assets" / "sounds"
CUES = ("done", "error")
VOLUME = 0.5

log = logging.getLogger("companion")


def cue_for(previous: DisplayState | None, current: DisplayState) -> str | None:
    """"done" when the shown state turns to done, "error" when a turn stops on an error.

    A tool failing mid-turn only flashes error (no error_reason) and stays silent, and the
    first display after a start plays nothing.
    """
    if previous is None:
        return None
    if current.state is State.DONE and previous.state is not State.DONE:
        return "done"
    # A turn can stop on an error during a tool failure's flash, which is error already.
    if current.state is State.ERROR and current.error_reason and not (
        previous.state is State.ERROR and previous.error_reason
    ):
        return "error"
    return None


class SoundPlayer:
    def __init__(self, directory: Path = SOUNDS_DIR, volume: float = VOLUME) -> None:
        self.directory = directory
        self.volume = volume
        self.effects: dict[str, QSoundEffect] = {}

    def preload(self) -> None:
        """Load every cue now, so the first one plays at once instead of after its load."""
        for cue in CUES:
            self._effect(cue)

    def play(self, cue: str) -> None:
        effect = self._effect(cue)
        if effect is not None:
            effect.play()

    def _effect(self, cue: str) -> QSoundEffect | None:
        effect = self.effects.get(cue)
        if effect is None:
            path = self.directory / f"{cue}.wav"
            if not path.is_file():
                log.warning("sound %s missing at %s", cue, path)
                return None
            effect = QSoundEffect()
            effect.setSource(QUrl.fromLocalFile(str(path)))
            effect.setVolume(self.volume)
            self.effects[cue] = effect
        return effect
