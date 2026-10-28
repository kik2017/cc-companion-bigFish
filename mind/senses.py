"""Events in, moments out: what is worth her reacting to. Pure, no Qt, no network.

Code decides *when* she might speak; the model only decides *what*. Every input is an
allowlisted event or display state from the daemon, a pat on the pet, or the time since
the user last touched the keyboard or mouse (just a time, never what was typed), stamped
with the local time it arrived. Moments, each with its own cooldown:

  greeting        the user's first prompt or session of the day, or back after GREETING_GAP away
  new_session     a session starts or resumes (not on the day's first activity)
  long_done       a main-session turn of the profile's long_turn or more finishes
  error_streak    ERROR_STREAK errors inside ERROR_WINDOW
  late_night      the user active between 01:00 and 05:00, once a night
  compacting      a main session starts compacting
  quota_high      the 5-hour quota passes QUOTA_HIGH, once per window
  working_long    the profile's work_streak of steady activity, then every work_repeat
  milestone       turns done or days together cross a round number
  pat             a click on her head, body or tail
  day_part        a new part of the day (afternoon, evening, night) with the user around,
                  once each, after the day's greeting (not in the quiet profile)
  back            back at the desk with news: turns finished or errors while the user was
                  away (not in the quiet profile; chatty also has the plain short break below)
  and, only in the chatty profile:
  turn_done       a shorter turn finishes (by chance, turn_done_chance)
  musing          a while of steady work with no other moment: a passing thought
  back            back after a short break (back_after, under GREETING_GAP)
  goodbye         a main session ends (not on /clear)

How often moments come is set by the chattiness profile (config.json "chattiness":
quiet, normal or chatty). It never raises her budget's ceiling; it only gives her more
or fewer chances to speak, and she may still choose silence.

Any event is activity (work streaks, today's tallies), but only the user's own actions
are presence: a prompt, a session they start or resume, a pat, a chat, and keyboard or
mouse input. Greetings, short breaks, late nights and days together go by presence alone,
so Claude compacting or a subagent finishing while they are away is not them coming back.

The desk (on_idle, fed from core's tick): input with no AWAY_AFTER gap keeps the user seen,
so a prompt after an hour of reading is not a return; input after a longer gap is them
coming back, a presence like a prompt, which can greet them before they type anything.
While nobody is at the desk, core keeps her lines (budget.py), and the turns and errors
that happened meanwhile ride along with her greeting or "back" when they return. Without
the idle sense (None: not Windows) the desk plays no part and only actions are presence.

Her day runs from DAY_START_HOUR to DAY_START_HOUR (05:00), not midnight to midnight: a
person's day ends when they sleep, so work at 02:45 still belongs to the day before, and
the first activity after 05:00 is a morning greeting. Today's tallies (turns, errors,
tools, projects) ride along for the prompt and reset when her day turns over.
to_state()/from_state() carry everything across restarts.
"""

from __future__ import annotations

import random
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

DAY_START_HOUR = 5
GREETING_GAP = timedelta(hours=2)
LONG_TURN = timedelta(minutes=5)
ERROR_STREAK = 3
ERROR_WINDOW = timedelta(minutes=10)
QUOTA_HIGH = 80.0
WORK_STREAK = timedelta(minutes=20)
WORK_GAP = timedelta(minutes=10)  # a pause this long ends a work streak
COOLDOWNS = {
    "new_session": timedelta(minutes=10),
    "error_streak": timedelta(minutes=30),
    "compacting": timedelta(minutes=30),
    "working_long": timedelta(minutes=20),
    "pat": timedelta(minutes=3),
}
DONE_MILESTONES = (10, 25, 50, 100, 200, 300, 500, 1000)
DAY_MILESTONES = (2, 3, 7, 14, 30, 50, 100, 200, 365)
ERROR_EVENTS = {"StopFailure", "PostToolUseFailure"}
ZONES = ("head", "body", "tail")
PART_NAMES = ("morning", "afternoon", "evening", "night")
MUSING_AFTER_OTHER = timedelta(minutes=3)  # a musing never lands right after another moment
AWAY_AFTER = timedelta(minutes=10)  # no keyboard or mouse input this long: the user is away from the desk
# Her parts of the day, by the hour they start; 01:00-05:00 is late_night's, not a part.
DAY_PARTS = ((5, "morning"), (12, "afternoon"), (18, "evening"), (23, "night"))


@dataclass(frozen=True)
class Profile:
    long_turn: timedelta
    work_streak: timedelta
    work_repeat: timedelta
    new_session: timedelta  # cooldown
    pat: timedelta  # cooldown
    turn_done_chance: float = 0.0
    musing: timedelta | None = None  # None: no musings
    back_after: timedelta | None = None  # None: no short-break moment
    goodbye: bool = False
    day_parts: bool = False  # a moment for each new part of the day
    news: bool = False  # back at the desk with news is a moment


# normal started as how she behaved before chattiness existed.
PROFILES = {
    "quiet": Profile(timedelta(minutes=10), timedelta(minutes=40), timedelta(minutes=40), timedelta(minutes=30), timedelta(minutes=5)),
    "normal": Profile(LONG_TURN, WORK_STREAK, timedelta(minutes=20), timedelta(minutes=10), timedelta(minutes=3), day_parts=True, news=True),
    "chatty": Profile(
        timedelta(minutes=2), timedelta(minutes=10), timedelta(minutes=10), timedelta(minutes=5), timedelta(minutes=1),
        turn_done_chance=0.35, musing=timedelta(minutes=12), back_after=timedelta(minutes=10), goodbye=True,
        day_parts=True, news=True,
    ),
}
CHATTY_COOLDOWNS = {"turn_done": timedelta(minutes=3), "goodbye": timedelta(minutes=10)}

# MCP servers that are the host app's own plumbing (task lists, wrap-ups, panes): never named to her.
APP_SERVERS = ("ccd_", "visualize", "terminal", "mcp-registry", "scheduled-tasks")
BROWSER_SERVERS = ("Claude_Browser", "claude-in-chrome")
UUID_LIKE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-")


def tool_label(name: object) -> str | None:
    """How a tool is named to her: core tools by name, MCP tools grouped, the app's plumbing not at all.

    mcp__<server>__<tool> becomes "browser" for the browser servers, nothing for the host
    app's own servers, "a connector" for a server known only by an id, and otherwise the
    server's name without the tool's. Her episode log keeps the exact names.
    """
    if not isinstance(name, str) or not name:
        return None
    if not name.startswith("mcp__"):
        return name
    server = name[len("mcp__"):].split("__", 1)[0]
    if not server or server.startswith(APP_SERVERS):
        return None
    if server in BROWSER_SERVERS:
        return "browser"
    if UUID_LIKE.match(server):
        return "a connector"
    return server


def day_of(when: datetime) -> date:
    """Her day for a local time: the calendar date, except that 00:00-05:00 belongs to the day before."""
    return (when - timedelta(hours=DAY_START_HOUR)).date()


def part_of_day(when: datetime) -> str | None:
    """morning, afternoon, evening or night (23:00-01:00); None from 01:00 to 05:00, the late night."""
    if 1 <= when.hour < DAY_START_HOUR:
        return None
    hour = when.hour if when.hour >= DAY_START_HOUR else when.hour + 24
    return [name for start, name in DAY_PARTS if hour >= start][-1]


@dataclass(frozen=True)
class Moment:
    kind: str
    detail: dict = field(default_factory=dict)


class Senses:
    def __init__(self, chattiness: str = "normal", rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()
        self.no_gap = False  # No pause: pats have no cooldown
        self.set_chattiness(chattiness)
        self.last_any: datetime | None = None  # when any moment last fired
        self.last_activity: datetime | None = None  # any event
        self.last_seen: datetime | None = None  # the user themself: a prompt, a session they open, a pat, input
        self.input_idle: timedelta | None = None  # since the last keyboard or mouse input; None: no idle sense
        self.while_away = {"turns": 0, "errors": 0}  # since the user was last seen
        self.greeted: str | None = None  # the day (date) already greeted
        self.streak_start: datetime | None = None
        self.last_moment: dict[str, datetime] = {}
        self.turn_started: dict[str, datetime] = {}
        self.errors: list[datetime] = []
        self.quota_window: float | None = None  # resets_at of the window already warned about
        self.late_night: str | None = None  # the night (date) already noted
        self.done_total = 0
        self.days: list[str] = []  # distinct local dates with activity
        self.display: dict = {}
        self.today = self._blank_day(None)

    def set_chattiness(self, chattiness: str) -> None:
        """Switch profile; everything she has seen so far is kept."""
        self.profile = p = PROFILES.get(chattiness, PROFILES["normal"])
        pat = timedelta(0) if self.no_gap else p.pat
        self.cooldowns = {**COOLDOWNS, **CHATTY_COOLDOWNS, "new_session": p.new_session, "working_long": p.work_repeat, "pat": pat}

    def set_no_gap(self, on: bool) -> None:
        """No pause: every pat is a moment, with no cooldown; off restores the profile's."""
        self.no_gap = on
        self.cooldowns["pat"] = timedelta(0) if on else self.profile.pat

    # --- inputs --------------------------------------------------------------------------

    def on_display(self, display: dict) -> None:
        self.display = display if isinstance(display, dict) else {}

    def on_event(self, event: dict, now: datetime) -> list[Moment]:
        name = event.get("hook_event_name")
        if not isinstance(name, str):
            return []
        if name == "StatusLine":
            return self._quota(event, now)
        self._activity(now)
        subagent = bool(event.get("subagent"))
        session = str(event.get("session_id", ""))
        tool, project = event.get("tool_name"), event.get("project")
        opened = name == "SessionStart" and event.get("source") in ("startup", "resume")
        moments = self._seen(now) if not subagent and (name == "UserPromptSubmit" or opened) else []
        if isinstance(project, str):
            self.today["projects"][project] += 1
        if name == "PreToolUse" and (label := tool_label(tool)):
            self.today["tools"][label] += 1
        if opened and not subagent:
            self.today["sessions"] += 1
            if not any(m.kind == "greeting" for m in moments):
                moments += self._once("new_session", now, {"project": project})
        elif name == "UserPromptSubmit" and not subagent:
            self.turn_started[session] = now
        elif name == "Stop" and not subagent:
            moments += self._turn_done(session, project, now)
        elif name in ERROR_EVENTS and not event.get("is_interrupt"):
            moments += self._error(now, project)
        elif name == "PreCompact" and not subagent:
            moments += self._once("compacting", now, {"project": project})
        elif name == "SessionEnd":
            self.turn_started.pop(session, None)
            if self.profile.goodbye and not subagent and event.get("reason") != "clear":
                moments += self._once("goodbye", now, {"project": project})
        return self._noted(moments, now)

    def on_pat(self, zone: str, now: datetime) -> list[Moment]:
        if zone not in ZONES:
            return []
        self._present(now)  # she answers the pat itself; it only keeps a later prompt from reading as a return
        return self._noted(self._once("pat", now, {"zone": zone}), now)

    def on_chat(self, now: datetime) -> None:
        """The user typed to her: presence, answered by the chat itself, like a pat."""
        self._present(now)
        self.last_any = now

    def on_idle(self, idle: timedelta | None, now: datetime) -> list[Moment]:
        """The time since the user last touched the keyboard or mouse (None: unknown); see the desk above."""
        self.input_idle = idle
        if idle is None or idle >= AWAY_AFTER:
            return []
        last_input = now - idle
        self._roll_day(now)
        if self.last_seen is None or last_input - self.last_seen >= AWAY_AFTER:
            return self._noted(self._seen(now), now)
        self._present(last_input)
        return self._noted(self._clock(now), now)

    def tick(self, now: datetime) -> list[Moment]:
        """Moments that come from time passing: a long work streak, and in the chatty profile a musing."""
        if self.last_activity is None or self.streak_start is None or now - self.last_activity > timedelta(minutes=2):
            return []
        minutes = int((now - self.streak_start).total_seconds() // 60)
        if now - self.streak_start >= self.profile.work_streak:
            if moments := self._once("working_long", now, {"minutes": minutes}):
                return self._noted(moments, now)
        musing = self.profile.musing
        if musing is None or (self.last_any is not None and now - self.last_any < MUSING_AFTER_OTHER):
            return []
        if now - self.last_moment.get("musing", self.streak_start) < musing:
            return []
        self.last_moment["musing"] = now
        return self._noted([Moment("musing", {"minutes_working": minutes})], now)

    # --- outputs -------------------------------------------------------------------------

    def summary(self) -> dict:
        """Today's tallies for the prompt: plain counts and names, nothing else."""
        tools: Counter = Counter()
        for name, count in self.today["tools"].items():  # older state may hold raw names
            if label := tool_label(name):
                tools[label] += count
        return {
            "first": self.today["first"],
            "sessions": self.today["sessions"],
            "turns": self.today["turns"],
            "errors": self.today["errors"],
            "tools": [name for name, _ in tools.most_common(3)],
            "projects": [name for name, _ in self.today["projects"].most_common(3)],
            "done_total": self.done_total,
            "days_together": len(self.days),
        }

    def to_state(self) -> dict:
        return {
            "last_activity": _iso(self.last_activity),
            "last_seen": _iso(self.last_seen),
            "while_away": dict(self.while_away),
            "greeted": self.greeted,
            "streak_start": _iso(self.streak_start),
            "last_moment": {k: v.isoformat() for k, v in self.last_moment.items()},
            "quota_window": self.quota_window,
            "late_night": self.late_night,
            "done_total": self.done_total,
            "days": self.days[-400:],
            "today": {**self.today, "tools": dict(self.today["tools"]), "projects": dict(self.today["projects"]), "parts": list(self.today["parts"])},
        }

    @classmethod
    def from_state(cls, state: dict, chattiness: str = "normal", rng: random.Random | None = None) -> Senses:
        senses = cls(chattiness, rng)
        if not isinstance(state, dict):
            return senses
        senses.last_activity = _parse(state.get("last_activity"))
        senses.streak_start = _parse(state.get("streak_start"))
        moments = state.get("last_moment")
        if isinstance(moments, dict):
            senses.last_moment = {k: t for k, v in moments.items() if (t := _parse(v)) is not None}
        if isinstance(state.get("quota_window"), (int, float)):
            senses.quota_window = state["quota_window"]
        if isinstance(state.get("late_night"), str):
            senses.late_night = state["late_night"]
        if isinstance(state.get("done_total"), int):
            senses.done_total = state["done_total"]
        if isinstance(state.get("days"), list):
            senses.days = [d for d in state["days"] if isinstance(d, str)]
        today = state.get("today")
        if isinstance(today, dict) and isinstance(today.get("date"), str):
            senses.today = senses._blank_day(today["date"])
            for key in ("first",):
                if isinstance(today.get(key), str):
                    senses.today[key] = today[key]
            for key in ("sessions", "turns", "errors"):
                if isinstance(today.get(key), int):
                    senses.today[key] = today[key]
            for key in ("tools", "projects"):
                if isinstance(today.get(key), dict):
                    senses.today[key] = Counter({k: v for k, v in today[key].items() if isinstance(v, int)})
            if isinstance(today.get("parts"), list):
                senses.today["parts"] = [p for p in today["parts"] if p in PART_NAMES]
        away = state.get("while_away")
        if isinstance(away, dict):
            senses.while_away = {k: v if isinstance(v := away.get(k), int) else 0 for k in senses.while_away}
        # State from before presence was kept: the last event stands in for the last sighting,
        # and a day with a first activity was already greeted.
        senses.last_seen = _parse(state.get("last_seen")) if "last_seen" in state else senses.last_activity
        if isinstance(state.get("greeted"), str):
            senses.greeted = state["greeted"]
        elif "greeted" not in state and senses.today["first"] is not None:
            senses.greeted = senses.today["date"]
        return senses

    # --- internals -----------------------------------------------------------------------

    @staticmethod
    def _blank_day(date: str | None) -> dict:
        return {"date": date, "first": None, "sessions": 0, "turns": 0, "errors": 0, "tools": Counter(), "projects": Counter(), "parts": []}

    def _roll_day(self, now: datetime) -> None:
        """Today's tallies turn over with her day."""
        date = day_of(now).isoformat()
        if self.today["date"] != date:
            self.today = self._blank_day(date)

    def _activity(self, now: datetime) -> None:
        """Note any event at `now`, the user's or Claude's: today's tallies turn over with her day, work streaks."""
        self._roll_day(now)
        if self.last_activity is None or now - self.last_activity > WORK_GAP:
            self.streak_start = now
        self.last_activity = now

    def _seen(self, now: datetime) -> list[Moment]:
        """Note the user themself at `now` (after _activity): a new day together, a return after a gap, late night."""
        moments: list[Moment] = []
        date = self.today["date"]
        if self.today["first"] is None:
            self.today["first"] = now.strftime("%H:%M")
        if date not in self.days:
            self.days.append(date)
            if len(self.days) in DAY_MILESTONES:
                moments.append(Moment("milestone", {"days_together": len(self.days)}))
        first_today = self.greeted != date
        gap = None if self.last_seen is None else now - self.last_seen
        news = self._news() if gap is not None and gap >= AWAY_AFTER else {}
        if first_today or (gap is not None and gap >= GREETING_GAP):
            hours = None if gap is None else round(gap.total_seconds() / 3600, 1)
            moments.insert(0, Moment("greeting", {"first_today": first_today, "hours_away": hours, **news}))
            self.greeted = date
            self.last_moment["greeting"] = now
        elif gap is not None and (
            (self.profile.back_after is not None and gap >= self.profile.back_after) or (news and self.profile.news)
        ):
            moments.insert(0, Moment("back", {"minutes_away": int(gap.total_seconds() // 60), **news}))
        moments += self._clock(now, quiet=bool(moments))
        self._present(now)
        return moments

    def _present(self, when: datetime) -> None:
        """The user is here at `when`: seen, and nothing has happened behind their back since."""
        if self.last_seen is None or when > self.last_seen:
            self.last_seen = when
        self.while_away = {"turns": 0, "errors": 0}

    def _news(self) -> dict:
        """What happened while the user was away from the desk, as moment detail; only with the idle sense."""
        if self.input_idle is None:
            return {}
        return {f"{key}_while_away": count for key, count in self.while_away.items() if count}

    def _clock(self, now: datetime, quiet: bool = False) -> list[Moment]:
        """With the user around: late night once a night, and each new part of the day once (none when `quiet`).

        A day's first part is the one she greeted them in, so it passes without a word.
        """
        moments = []
        if 1 <= now.hour < 5:
            night = (now - timedelta(hours=5)).date().isoformat()
            if self.late_night != night:
                self.late_night = night
                moments.append(Moment("late_night", {"time": now.strftime("%H:%M")}))
        part, parts = part_of_day(now), self.today["parts"]
        if part is not None and part not in parts and self.greeted == self.today["date"] == day_of(now).isoformat():
            parts.append(part)
            if len(parts) > 1 and self.profile.day_parts and not quiet:
                moments.append(Moment("day_part", {"part": part, "time": now.strftime("%H:%M"), "since": self.today["first"]}))
        return moments

    def _once(self, kind: str, now: datetime, detail: dict) -> list[Moment]:
        last = self.last_moment.get(kind)
        cooldown = self.cooldowns.get(kind, timedelta(0))
        if last is not None and now - last < cooldown:
            return []
        self.last_moment[kind] = now
        return [Moment(kind, {k: v for k, v in detail.items() if v is not None})]

    def _turn_done(self, session: str, project: object, now: datetime) -> list[Moment]:
        self.today["turns"] += 1
        self.done_total += 1
        self.while_away["turns"] += 1
        moments = []
        started = self.turn_started.pop(session, None)
        if started is not None and now - started >= self.profile.long_turn:
            minutes = int((now - started).total_seconds() // 60)
            moments.append(Moment("long_done", {"minutes": minutes, **({"project": project} if project else {})}))
        elif started is not None and self.profile.turn_done_chance and self.rng.random() < self.profile.turn_done_chance:
            moments += self._once("turn_done", now, {"seconds": int((now - started).total_seconds()), "project": project})
        if self.done_total in DONE_MILESTONES or (self.done_total > 1000 and self.done_total % 500 == 0):
            moments.append(Moment("milestone", {"turns_done": self.done_total}))
        return moments

    def _noted(self, moments: list[Moment], now: datetime) -> list[Moment]:
        if moments:
            self.last_any = now
        return moments

    def _error(self, now: datetime, project: object) -> list[Moment]:
        self.today["errors"] += 1
        self.while_away["errors"] += 1
        self.errors = [t for t in self.errors if now - t < ERROR_WINDOW] + [now]
        if len(self.errors) < ERROR_STREAK:
            return []
        return self._once("error_streak", now, {"errors": len(self.errors), "project": project})

    def _quota(self, event: dict, now: datetime) -> list[Moment]:
        pct, resets_at = event.get("five_hour_pct"), event.get("five_hour_resets_at")
        if not isinstance(pct, (int, float)) or isinstance(pct, bool) or pct < QUOTA_HIGH:
            return []
        window = resets_at if isinstance(resets_at, (int, float)) and not isinstance(resets_at, bool) else -1.0
        if self.quota_window == window:
            return []
        self.quota_window = window
        detail: dict = {"pct": round(pct)}
        if window > 0:
            detail["resets_in_min"] = max(0, int((window - now.timestamp()) // 60))
        return [Moment("quota_high", detail)]


def _iso(when: datetime | None) -> str | None:
    return None if when is None else when.isoformat()


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
