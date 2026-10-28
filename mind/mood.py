"""Her mood, owned by code: moments nudge it, time eases it back. Pure, no Qt, no network.

Two numbers:
  valence   -100..100, pushed by moments and halving toward 0 every VALENCE_HALF_LIFE
  energy    0..100, from the hour of day, minus a little for a long work streak

They name one of model.MOODS, which picks her clip in Quiet mode and is handed to the
model so her words match her body. Affinity grows with pats, at most AFFINITY_DAILY a day.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from companion.model import MOODS

VALENCE_HALF_LIFE = timedelta(hours=2)
AFFINITY_DAILY = 5
NUDGES = {
    "greeting": 5,
    "new_session": 2,
    "long_done": 15,
    "milestone": 20,
    "error_streak": -20,
    "compacting": -2,
    "quota_high": -8,
    "working_long": 3,
    "pat": 10,
    "turn_done": 3,
    "back": 3,
    "musing": 0,
    "goodbye": 0,
    "chat": 4,
}
PROUD_AFTER = {"long_done", "milestone"}


def energy_at(when: datetime) -> int:
    hour = when.hour
    if 7 <= hour < 12:
        return 85
    if 12 <= hour < 18:
        return 70
    if 18 <= hour < 23:
        return 55
    if hour == 23 or 5 <= hour < 7:
        return 35
    return 20  # 00:00-05:00


class Mood:
    def __init__(self) -> None:
        self.valence = 0.0
        self.affinity = 0
        self.at: datetime | None = None
        self.last_kind: str | None = None
        self.affinity_day: str | None = None
        self.affinity_today = 0

    def apply(self, kind: str, now: datetime) -> str:
        """Nudge by a moment of `kind`; the mood it leaves her in."""
        self._ease(now)
        self.valence = max(-100.0, min(100.0, self.valence + NUDGES.get(kind, 0)))
        self.last_kind = kind
        if kind == "pat":
            day = now.date().isoformat()
            if self.affinity_day != day:
                self.affinity_day, self.affinity_today = day, 0
            if self.affinity_today < AFFINITY_DAILY:
                self.affinity_today += 1
                self.affinity = min(100, self.affinity + 1)
        return self.name(now)

    def name(self, now: datetime) -> str:
        self._ease(now)
        if energy_at(now) <= 20 and self.valence < 25:
            return "sleepy"
        if self.valence >= 25:
            return "proud" if self.last_kind in PROUD_AFTER else "happy"
        if self.valence <= -15:
            return "worried"
        if self.last_kind == "pat":
            return "curious"
        return MOODS[0]  # calm

    def _ease(self, now: datetime) -> None:
        if self.at is not None and now > self.at:
            halves = (now - self.at) / VALENCE_HALF_LIFE
            self.valence *= 0.5**halves
        self.at = now

    def to_state(self) -> dict:
        return {
            "valence": round(self.valence, 2),
            "affinity": self.affinity,
            "at": None if self.at is None else self.at.isoformat(),
            "last_kind": self.last_kind,
            "affinity_day": self.affinity_day,
            "affinity_today": self.affinity_today,
        }

    @classmethod
    def from_state(cls, state: dict) -> Mood:
        mood = cls()
        if not isinstance(state, dict):
            return mood
        if isinstance(state.get("valence"), (int, float)):
            mood.valence = max(-100.0, min(100.0, float(state["valence"])))
        if isinstance(state.get("affinity"), int):
            mood.affinity = max(0, min(100, state["affinity"]))
        try:
            mood.at = datetime.fromisoformat(state["at"]) if isinstance(state.get("at"), str) else None
        except ValueError:
            mood.at = None
        for key in ("last_kind", "affinity_day"):
            if isinstance(state.get(key), str):
                setattr(mood, key, state[key])
        if isinstance(state.get("affinity_today"), int):
            mood.affinity_today = state["affinity_today"]
        return mood
