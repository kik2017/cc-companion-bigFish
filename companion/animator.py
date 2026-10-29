"""Which frame the pet shows: state clips, reactions and idle clips. Pure, no Qt.

Three tiers, highest first:
  reaction  a click on the head, body or tail, a drag (hover loop) or the landing after it,
            or a gesture: her talking clip or a cue (greet, sleep) from the persona
  idle      after a rest window: a usage clip that is due, else a time-of-day clip not yet
            played in its window today, else a random idle clip; or a persona mood clip,
            only while the state is idle
  state     a clip mapped to the display state; with several, one is picked when the
            state starts and a looping one may change at the end of each pass

A click while she plays one of the skin's wake clips (a nap) wakes her instead of the
zone's reaction. A gesture follows the rules of a click reaction, except the cooldown.

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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .model import State
from .skin import Clip, Skin, Timed

CLICK_COOLDOWN_S = 1.2
DOUBLE_CLICK_S = 0.5
FADE_S = 0.1
REST_WINDOWS_S = {"quiet": (45.0, 90.0), "normal": (25.0, 45.0), "lively": (12.0, 22.0)}
CLICK_REACTIONS = ("head", "body", "tail")
INTERRUPTIBLE = (*CLICK_REACTIONS, "wake", "gesture")  # a state change cuts these short
VARIANT_SWITCH = 0.5  # chance a looping state clip with variants changes at the end of a pass


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
        local_now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.skin = skin
        self.activity = activity
        self.reduced_motion = reduced_motion
        self.rng = rng or random.Random()
        self.local_now = local_now  # the wall clock, for time-of-day clips
        self.state = State.IDLE
        self.reaction: str | None = None  # a key of skin.reactions, "wake" or "gesture"
        self.idle_clip: str | None = None
        self.last_click: float | None = None
        self.last_idle: str | None = None
        self.idle_due: float | None = None
        self.timed_played: set[tuple[str, int, int]] = set()  # (clip, window start, window day)
        self.usage_tier: int | None = None
        self.usage_due: str | None = None
        self.base = self._pick(skin.state_clips(self.state))  # this state's clip, among its variants
        self.clip = self.base
        self.started = now
        self._schedule_idle(now)

    # --- inputs -------------------------------------------------------------------------

    def set_state(self, state: State, now: float) -> None:
        if state is self.state:
            return  # a repeated state never restarts its clip
        self.state = state
        self.idle_clip = None
        self.base = self._pick(self.skin.state_clips(state))
        if self.reaction in INTERRUPTIBLE:
            self.reaction = None
        if self.reaction is None:
            self._play(self.base, now)
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
        if self.skin.wake is not None and self.idle_clip in self.skin.wake_during:
            self.reaction, self.idle_clip = "wake", None  # clicked awake from her nap
            self._play(self.skin.clips[self.skin.wake], now)
        else:
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

    def gesture(self, clips: tuple[Clip, ...], now: float) -> bool:
        """One of `clips` (her talking, or a cue) on the reaction tier; True if it starts.

        Refused like a click: under reduced motion, during a reaction, and while a done or
        error clip is still playing. A state change cuts it short.
        """
        self.update(now)
        if self.reduced_motion or not clips or self.reaction is not None:
            return False
        if self.idle_clip is None and not self.clip.loop and not self._finished(now):
            return False
        self.reaction, self.idle_clip = "gesture", None
        self._play(self._pick(clips), now)
        return True

    def set_usage(self, pct: float | None, now: float) -> None:
        """The 5-hour quota's percent used: climbing into a new tier makes its clip due at her next idle moment."""
        tier = None if pct is None else self.skin.usage_tier(pct)
        if tier is None:
            return
        if self.usage_tier is not None and tier > self.usage_tier:
            self.usage_due = self.skin.usage[tier]
            if self.state is State.IDLE and self.reaction is None and self.idle_clip is None and not self.reduced_motion:
                self.idle_due = now
        elif self.usage_tier is not None and tier < self.usage_tier:
            self.usage_due = None  # the window reset
        self.usage_tier = tier

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
        self.base = self.skin.state_clip(self.state)
        self.clip, self.started = self.base, now
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
            self._play(self.base, ended_at)
            self._schedule_idle(ended_at)
        self._next_pass(now)
        if (
            self.idle_due is not None
            and now >= self.idle_due
            and self.reaction is None
            and self.idle_clip is None
            and self.state is State.IDLE
        ):
            self.idle_due = None
            name = self._next_idle()
            if name is None:
                self._schedule_idle(now)
            else:
                self.idle_clip = self.last_idle = name
                self._play(self.skin.clips[name], now)

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
        drag = {*self.skin.reactions["drag"], *self.skin.reactions["land"]}
        return previous_clip != current_clip and previous_clip not in drag and current_clip not in drag

    # --- internals ----------------------------------------------------------------------

    def _react(self, reaction: str, now: float) -> None:
        self.reaction = reaction
        self.idle_clip = None
        self._play(self._pick(self.skin.reaction_clips(reaction)), now)

    def _pick(self, clips: tuple[Clip, ...]) -> Clip:
        """One of `clips`; a single clip draws nothing from the random source."""
        return clips[0] if len(clips) == 1 else self.rng.choice(clips)

    def _next_pass(self, now: float) -> None:
        """At the end of a pass of a looping state clip with variants, maybe move on to another."""
        clip = self.clip
        if self.reaction is not None or self.idle_clip is not None or clip is not self.base or not clip.loop:
            return
        variants = self.skin.state_clips(self.state)
        if len(variants) < 2 or now - self.started < clip.duration_s:
            return
        ended = self.started + int((now - self.started) // clip.duration_s) * clip.duration_s
        if self.rng.random() < VARIANT_SWITCH:
            self.base = self.rng.choice([c for c in variants if c is not clip])
            self.clip = self.base
        self.started = ended

    def _next_idle(self) -> str | None:
        """A usage clip that is due, else a time-of-day clip not yet played in its window, else a random idle clip."""
        if self.usage_due is not None:
            name, self.usage_due = self.usage_due, None
            return name
        local = self.local_now()
        minute = local.hour * 60 + local.minute
        for entry in self.skin.timed:
            if entry.contains(minute):
                key = (entry.clip, entry.start, self._window_day(entry, local, minute))
                if key not in self.timed_played:
                    today = local.date().toordinal()
                    self.timed_played = {k for k in self.timed_played if k[2] >= today - 1} | {key}
                    return entry.clip
        choices = [c for c in self.skin.idle if c != self.last_idle] or list(self.skin.idle)
        return self.rng.choice(choices) if choices else None

    @staticmethod
    def _window_day(entry: Timed, local: datetime, minute: int) -> int:
        """The day a time window started on: after midnight, a window that began the evening before."""
        wraps = entry.start > entry.end
        return (local.date() - timedelta(days=1 if wraps and minute < entry.end else 0)).toordinal()

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
        has_idle = self.skin.idle or self.skin.timed or self.usage_due is not None
        if self.state is State.IDLE and has_idle and not self.reduced_motion and self.idle_clip is None:
            low, high = REST_WINDOWS_S.get(self.activity, REST_WINDOWS_S["normal"])
            self.idle_due = now + self.rng.uniform(low, high)
        else:
            self.idle_due = None
