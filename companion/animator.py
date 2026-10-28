"""Which frame the pet shows: state clips, reactions and idle clips. Pure, no Qt.

Three tiers, highest first:
  reaction  a click on the head, body or tail, a drag (hover loop) or the landing after it
  idle      a random clip after a rest window, or a persona mood clip, only while the state is idle
  state     the clip mapped to the display state

A click reaction plays to completion, then the state clip resumes; a state change
interrupts it, and an idle clip, at once. A drag and its landing hold state changes
until the landing ends. A click is ignored while another reaction plays, while a
non-looping state clip (done, error) is still playing, and for CLICK_COOLDOWN_S after
the previous click. A mood clip plays only while idle with no reaction or idle clip
playing, so it never cuts into anything. A non-looping state clip holds its last frame. Reduced motion
holds the first frame of the state clip and plays nothing else. Reduced motion and
activity can change live from the menu.

Times are seconds from a monotonic clock, passed in by the caller.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from .model import State
from .skin import Clip, Skin

CLICK_COOLDOWN_S = 1.2
DOUBLE_CLICK_S = 0.5
FADE_S = 0.1
REST_WINDOWS_S = {"quiet": (45.0, 90.0), "normal": (25.0, 45.0), "lively": (12.0, 22.0)}
CLICK_REACTIONS = ("head", "body", "tail")


@dataclass(frozen=True)
class Frame:
    clip: str
    index: int
    path: Path


class Animator:
    def __init__(
        self,
        skin: Skin,
        now: float,
        *,
        activity: str = "normal",
        reduced_motion: bool = False,
        rng: random.Random | None = None,
    ) -> None:
        self.skin = skin
        self.activity = activity
        self.reduced_motion = reduced_motion
        self.rng = rng or random.Random()
        self.state = State.IDLE
        self.reaction: str | None = None  # a key of skin.reactions
        self.idle_clip: str | None = None
        self.last_click: float | None = None
        self.last_idle: str | None = None
        self.idle_due: float | None = None
        self.clip = skin.state_clip(self.state)
        self.started = now
        self._schedule_idle(now)

    # --- inputs -------------------------------------------------------------------------

    def set_state(self, state: State, now: float) -> None:
        if state is self.state:
            return  # a repeated state never restarts its clip
        self.state = state
        self.idle_clip = None
        if self.reaction in CLICK_REACTIONS:
            self.reaction = None
        if self.reaction is None:
            self._play(self.skin.state_clip(state), now)
        self._schedule_idle(now)

    def click(self, zone: str, now: float) -> bool:
        """A click (press and release without a drag) on a zone; True if a reaction starts."""
        self.update(now)
        if self.reduced_motion or zone not in CLICK_REACTIONS or self.reaction is not None:
            return False
        if self.last_click is not None and now - self.last_click < CLICK_COOLDOWN_S:
            return False
        if self.idle_clip is None and not self.clip.loop and not self._finished(now):
            return False  # a done or error clip is still playing
        self._react(zone, now)
        self.last_click = now
        return True

    def double_click(self, now: float) -> bool:
        """A head pat; replaces the reaction its own first click started a moment ago."""
        self.update(now)
        if self.reduced_motion:
            return False
        own_first_click = (
            self.reaction in CLICK_REACTIONS and self.last_click is not None and now - self.last_click <= DOUBLE_CLICK_S
        )
        if own_first_click:
            self._react("head", now)
            self.last_click = now
            return True
        return self.click("head", now)

    def drag_start(self, now: float) -> None:
        if self.reduced_motion:
            return
        self.idle_clip = None
        self._react("drag", now)

    def drag_end(self, now: float) -> None:
        if self.reaction == "drag":
            self._react("land", now)

    def play_mood(self, clip: Clip, now: float) -> bool:
        """A persona mood clip on the idle tier; True if it starts.

        Only while idle with no reaction or idle clip playing. The rest window starts
        again once it ends, as after an idle clip.
        """
        self.update(now)
        if self.reduced_motion or self.state is not State.IDLE or self.reaction is not None or self.idle_clip is not None:
            return False
        self.idle_clip, self.idle_due = clip.name, None
        self._play(clip, now)
        return True

    def set_reduced_motion(self, on: bool, now: float) -> None:
        """Switch reduced motion live: drop any reaction or idle clip and restart the state clip."""
        if on == self.reduced_motion:
            return
        self.reduced_motion = on
        self.reaction = self.idle_clip = None
        # Forced, not _play: a shared looping clip must restart from frame 0 too.
        self.clip, self.started = self.skin.state_clip(self.state), now
        self._schedule_idle(now)  # clears idle_due when on

    def set_activity(self, level: str, now: float) -> None:
        """Change the rest window; a playing idle clip finishes first and the next rest uses the new one."""
        if level == self.activity:
            return
        self.activity = level
        if self.idle_clip is None:
            self._schedule_idle(now)

    # --- output -------------------------------------------------------------------------

    def update(self, now: float) -> None:
        """Apply transitions that are due: a finished reaction or idle clip, a due idle clip."""
        if self.reduced_motion:
            return
        if (self.reaction is not None or self.idle_clip is not None) and self._finished(now):
            ended_at = self.started + self.clip.duration_s
            self.reaction = self.idle_clip = None
            self._play(self.skin.state_clip(self.state), ended_at)
            self._schedule_idle(ended_at)
        if (
            self.idle_due is not None
            and now >= self.idle_due
            and self.reaction is None
            and self.idle_clip is None
            and self.state is State.IDLE
        ):
            choices = [c for c in self.skin.idle if c != self.last_idle] or list(self.skin.idle)
            self.idle_clip = self.last_idle = self.rng.choice(choices)
            self.idle_due = None
            self._play(self.skin.clips[self.idle_clip], now)

    def frame(self, now: float) -> Frame:
        self.update(now)
        if self.reduced_motion:
            clip = self.skin.state_clip(self.state)
            return Frame(clip.name, 0, clip.frames[0])
        index = self._index(now)
        return Frame(self.clip.name, index, self.clip.frames[index])

    def next_change(self, now: float) -> float | None:
        """When the shown frame can next change with no new input, or None if it is still."""
        self.update(now)
        if self.reduced_motion:
            return None
        clip = self.clip
        upcoming = []
        # The next frame boundary; for a non-looping clip the last one is where it ends.
        if (clip.loop and len(clip.frames) > 1) or (not clip.loop and not self._finished(now)):
            step = clip.frame_ms / 1000
            upcoming.append(self.started + (int(max(0.0, now - self.started) / step) + 1) * step)
        if self.idle_due is not None:
            upcoming.append(self.idle_due)
        return min(upcoming) if upcoming else None

    def fades(self, previous_clip: str, current_clip: str) -> bool:
        """Cross-fade between different clips, but never into or out of the drag clips."""
        drag = {self.skin.reactions["drag"], self.skin.reactions["land"]}
        return previous_clip != current_clip and previous_clip not in drag and current_clip not in drag

    # --- internals ----------------------------------------------------------------------

    def _react(self, reaction: str, now: float) -> None:
        self.reaction = reaction
        self.idle_clip = None
        self._play(self.skin.reaction_clip(reaction), now)

    def _play(self, clip: Clip, now: float) -> None:
        if clip is self.clip and clip.loop:
            return  # two states sharing a looping clip: it carries on without a restart
        self.clip, self.started = clip, now

    def _index(self, now: float) -> int:
        count = len(self.clip.frames)
        step = int(max(0.0, now - self.started) * 1000 // self.clip.frame_ms)
        return step % count if self.clip.loop else min(step, count - 1)

    def _finished(self, now: float) -> bool:
        return not self.clip.loop and now - self.started >= self.clip.duration_s

    def _schedule_idle(self, now: float) -> None:
        if self.state is State.IDLE and self.skin.idle and not self.reduced_motion and self.idle_clip is None:
            low, high = REST_WINDOWS_S.get(self.activity, REST_WINDOWS_S["normal"])
            self.idle_due = now + self.rng.uniform(low, high)
        else:
            self.idle_due = None
