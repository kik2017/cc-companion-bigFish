"""The mind: messages from the daemon in, moments, mood, memory and lines out.

No threads and no network here. handle() and tick() run on the main loop and return
jobs (callables) for each line worth asking the model for; __main__ runs the jobs on a
worker thread, so a slow API call never holds up the next event. Shared state is
touched under `lock`, never across the API call itself.

Sleep (sleep.py) runs as a job too, when an ended day is still unslept and either the
mind has just started or you have been away IDLE_BEFORE_SLEEP. It holds `busy`, so she
does not talk in her sleep; her first greeting after it may mention her dream.

A pat always gets its turn: one that comes while a line is on its way, or under the
daemon's SAY_INTERVAL_S after her last line (so her lines never replace each other too
fast), is held, and tick() asks for it once she is free and the interval has passed. Pats
held meanwhile count together; one that waited over PAT_WAIT is let go. While a pat is
held, the main loop ticks every PAT_POLL_S (wait_s()).

Moments that come in one batch with her greeting or "back" (a milestone on a new day,
say) are told in that line, when it gets one, instead of each losing to it; if the
greeting gets no line, each has its own chance as usual.

Each tick also reads the desk: the seconds since the last keyboard or mouse input, from
the `idle` function (idle.py), handed to the senses. Away from the desk she keeps her lines.

Messages from the daemon, one JSON object per line on stdin:
  {"type": "event", "event": {...}}        an allowlisted hook or status line event
  {"type": "display", "display": {...}}    what the pet shows, on each change
  {"type": "pat", "zone": "head"}          a click reaction on her head, body or tail
  {"type": "persona", "mode": "on"}        on: words and moods; quiet: moods only, no API calls
  {"type": "chattiness", "level": "chatty"} the menu changed her chattiness (already saved to config.json)
  {"type": "chat", "text": "..."}          the user typed this to her in the chat box
  {"type": "private", "day": "2026-10-28"} Private today: nothing of that day of hers is written (null: off)
  {"type": "no_gap", "on": true}           No pause (already saved to config.json)
  {"type": "limit", "limit": "unlimited"}  her limit: "normal", "unlimited" or N lines a day (saved too)
  {"type": "search", "search": "normal"}   web search: "off", "normal", "unlimited" or N a day (saved too)

A chat gets an answer sent as a reply (/say with "reply": true), which the daemon lets
through once past its line interval because it asked for it. With web search on, the
answer may search (budget.searches_for_answer, counted when the job runs, so answers
queued together share today's searches); a notice from an answer that searched is
dropped, since what she read is not something the user said. The conversation is kept in
memory for the day and, unless the day is private, in her episodes as kind "chat", where
sleep learns from what the user said. While a day is private, nothing is written to her
episodes at all (events, moments, lines, notes or chats), so sleep has nothing of it.
"""

from __future__ import annotations

import dataclasses
import functools
import logging
import threading
import unicodedata
from collections.abc import Callable
from datetime import datetime, timedelta

from companion.server import SAY_INTERVAL_S, UNPRINTABLE

from . import facts as facts_mod
from .budget import OUT_OF_LINES, Budget
from .files import CHOICES, Files, read_json, valid_config, write_json
from .mood import Mood, bond_stage, energy_at
from .prompts import Redactor, chat_prompt, system_prompt, user_prompt
from .senses import AWAY_AFTER, Moment, Senses, day_of
from .sleep import IDLE_BEFORE_SLEEP, Sleep
from .voice import Voice

log = logging.getLogger("mind")

Job = Callable[..., None]
SayFn = Callable[..., int]  # (text or None for mood-only, mood, reply=False, cue=None) -> HTTP status
IdleFn = Callable[[], float | None]  # seconds since the last keyboard or mouse input; None: unknown
CHAT_MAX_CHARS = 300
CHAT_CONTEXT = 10  # the last this many chat lines of today go with each new message
RECENT_KEPT = 20
PAT_WAIT = timedelta(seconds=60)
PAT_POLL_S = 1.0
SEEN_SAVE = timedelta(minutes=1)  # input at the desk moves last_seen every tick; it is saved at most this often
GREETINGS = ("greeting", "back")  # moments that come with these are told in their line; her body greets too


def clean_chat(raw: object) -> str | None:
    """What the user typed as one printable line of at most CHAT_MAX_CHARS; None if nothing is left."""
    if not isinstance(raw, str):
        return None
    spaced = (" " if ch.isspace() else ch for ch in raw[: 4 * CHAT_MAX_CHARS])
    text = " ".join("".join(ch for ch in spaced if unicodedata.category(ch) not in UNPRINTABLE).split())
    return text[:CHAT_MAX_CHARS] or None


class Mind:
    def __init__(
        self, files: Files, config: dict, voice: Voice | None, say: SayFn,
        now: Callable[[], datetime] = datetime.now, idle: IdleFn = lambda: None,
    ) -> None:  # fmt: skip
        self.files = files
        self.config = config
        self.voice = voice
        self.say = say
        self.now = now
        self.idle = idle
        self.lock = threading.Lock()
        state = read_json(files.state)
        self.senses = Senses.from_state(state.get("senses", {}), config["chattiness"])
        self.senses.set_no_gap(config.get("no_gap") is True)
        self.mood = Mood.from_state(state.get("mood", {}))
        self.budget = Budget(config, read_json(files.spend))
        self.redact = Redactor(config["redact_projects"])
        self.mode = "on"
        self.busy = False  # a voice or sleep job is in flight
        self.held_pat: tuple[Moment, datetime] | None = None  # a pat waiting its turn, and since when
        self.last_say: datetime | None = None  # her last line or mood the daemon took
        self.saved_seen = self.senses.last_seen  # as of the last save
        self.sleeper = Sleep(files, voice, self._record) if voice is not None else None
        self.just_started = True
        private = read_json(files.config).get("private_day")
        self.private_day: str | None = private if isinstance(private, str) else None
        # Her lines and today's chat, kept here too so a private day still has them in mind.
        start = now()
        today = day_of(start).isoformat()
        self.recent: list[tuple[str, str]] = [(today, line) for line in files.recent_said(start)]
        self.chat_log: list[tuple[str, str, str]] = [  # (day, "user" or "her", text)
            (today, r["from"], r["text"])
            for r in files.day_records(day_of(start))
            if r.get("kind") == "chat" and r.get("from") in ("user", "her") and isinstance(r.get("text"), str)
        ]

    # --- inputs --------------------------------------------------------------------------

    def handle(self, message: object) -> list[Job]:
        if not isinstance(message, dict):
            return []
        kind, now = message.get("type"), self.now()
        with self.lock:
            if kind == "event" and isinstance(message.get("event"), dict):
                event = message["event"]
                moments = self.senses.on_event(event, now)
                if event.get("hook_event_name") != "StatusLine":
                    self._remember(now, "event", **_event_fields(event, self.redact))
            elif kind == "display":
                self.senses.on_display(message.get("display"))
                return []
            elif kind == "pat":
                moments = self.senses.on_pat(str(message.get("zone")), now)
            elif kind == "chattiness" and message.get("level") in CHOICES["chattiness"]:
                self.config["chattiness"] = message["level"]
                self.senses.set_chattiness(message["level"])
                log.info("chattiness %s", message["level"])
                return []
            elif kind == "persona" and message.get("mode") in ("on", "quiet"):
                self.mode = message["mode"]
                log.info("persona mode %s", self.mode)
                return []
            elif kind == "no_gap" and isinstance(message.get("on"), bool):
                self.config["no_gap"] = message["on"]
                self.senses.set_no_gap(message["on"])
                log.info("no pause %s", "on" if message["on"] else "off")
                return []
            elif kind == "limit" and valid_config("limit", message.get("limit")):
                self.config["limit"] = message["limit"]
                log.info("limit %s", message["limit"])
                return []
            elif kind == "search" and valid_config("search", message.get("search")):
                self.config["search"] = message["search"]
                log.info("web search %s", message["search"])
                return []
            elif kind == "private":
                day = message.get("day")
                self.private_day = day if isinstance(day, str) else None
                log.info("private today %s", "on" if self.private_day else "off")
                return []
            elif kind == "chat":
                jobs = self._chat(message.get("text"), now)
                self._save()
                return jobs
            else:
                return []
            jobs = self._react_all(moments, now)
            self._save()
        return jobs

    def tick(self) -> list[Job]:
        now = self.now()
        with self.lock:
            moments = self.senses.on_idle(self._idle(), now) + self.senses.tick(now)
            jobs = self._react_all(moments, now)
            if (pat := self._held_pat_job(now)) is not None:
                jobs.append(pat)
            seen, saved = self.senses.last_seen, self.saved_seen
            if moments or (seen is not None and (saved is None or seen - saved >= SEEN_SAVE)):
                self._save()
            sleep = self._sleep_job(now)
        return jobs + ([sleep] if sleep is not None else [])

    def wait_s(self, default: float) -> float:
        """How long the main loop may wait for a message before the next tick: briefly while a pat is held."""
        with self.lock:
            return PAT_POLL_S if self.held_pat is not None else default

    # --- sleeping ------------------------------------------------------------------------

    def _sleep_job(self, now: datetime) -> Job | None:
        if self.sleeper is None or self.busy:
            return None  # a start's catch-up waits for the line in flight (the greeting a hook's launch brings)
        started, self.just_started = self.just_started, False
        last = max((t for t in (self.senses.last_activity, self.senses.last_seen) if t is not None), default=None)
        if not started and last is not None and now - last < IDLE_BEFORE_SLEEP:
            return None
        days = self.sleeper.due_days(now)
        if not days:
            return None
        self.busy = True
        log.info("sleeping on %d day(s)", len(days))
        return lambda: self._sleep(days)

    def _sleep(self, days: list) -> None:
        try:
            self._post(None, "sleepy", cue="sleep")
            for day in days:
                self.sleeper.run(day)
        finally:
            with self.lock:
                self.busy = False

    def _record(self, usage: dict) -> None:
        with self.lock:
            self.budget.record(self.config["model"], usage, self.now())
            write_json(self.files.spend, self.budget.spend)

    # --- reacting ------------------------------------------------------------------------

    def _react_all(self, moments: list[Moment], now: datetime) -> list[Job]:
        """React to one batch of moments. Those that come with her greeting (or "back") are told in its line, if it
        gets one, rather than each losing to the line already on its way; otherwise each has its own chance."""
        lead = next((m for m in moments if m.kind in GREETINGS), None)
        riders = [m for m in moments if lead is not None and m is not lead]
        if not riders:
            return [job for moment in moments if (job := self._react(moment, now)) is not None]
        was_busy = self.busy
        jobs = [job] if (job := self._react(lead, now, also=riders)) is not None else []
        told = self.busy and not was_busy  # the lead asked for a line, and that line carries the riders
        for moment in riders:
            if told:
                self._note(moment, now)
                log.info("moment %s: told with her %s", moment.kind, lead.kind)
            elif (job := self._react(moment, now)) is not None:
                jobs.append(job)
        return jobs

    def _react(self, moment: Moment, now: datetime, also: list[Moment] | tuple = ()) -> Job | None:
        """Log the moment, move her mood, and return a job if it is worth a line (one that also tells `also`)."""
        mood = self._note(moment, now)
        if self.mode == "quiet" or self.voice is None:
            return self._mood_only(moment, mood)
        return self._line(moment, now, mood, also)

    def _note(self, moment: Moment, now: datetime) -> str:
        """Log the moment in her episodes and move her mood; her mood now."""
        mood = self.mood.apply(moment.kind, now)
        self._remember(now, "moment", moment=moment.kind, **_redacted(moment.detail, self.redact))
        return mood

    def _line(self, moment: Moment, now: datetime, mood: str, also: list[Moment] | tuple = ()) -> Job | None:
        """The job that asks for a line, if her budget allows one now; a pat that must wait is held."""
        waiting = self.senses.display.get("state") == "waiting"
        reason = self.budget.why_not(moment.kind, now, waiting, away=self._away())
        if reason is None and moment.kind == "pat" and (self.busy or self._say_wait(now) > 0):
            self._hold_pat(moment, now)
            return None
        if reason is None and self.busy:
            reason = "a line is already on its way"
        if reason is not None:
            log.info("moment %s: no line (%s)", moment.kind, reason)
            return self._mood_only(moment, mood) if reason in OUT_OF_LINES else None
        self.budget.take(now)
        self.busy = True
        known = facts_mod.known(facts_mod.clean(read_json(self.files.facts)))
        system = system_prompt(self.files.identity_text(), self.files.self_text(), known)
        dream = self.sleeper.take_dream() if moment.kind == "greeting" and self.sleeper is not None else None
        user = user_prompt(
            moment.kind,
            moment.detail,
            now=now,
            display=self.senses.display,
            today=self.senses.summary(),
            mood=mood,
            energy=energy_at(now),
            recent=[line for day, line in self.recent if day == day_of(now).isoformat()][-5:],
            project=self.redact,
            dream=dream,
            also=[(m.kind, m.detail) for m in also],
            bond=self._bond(),
        )
        log.info("moment %s: asking for a line", moment.kind)
        return lambda: self._speak(moment, system, user, mood)

    def _mood_only(self, moment: Moment, mood: str) -> Job:
        def run() -> None:
            status = self._post(None, mood, cue=_cue(moment))
            log.info("moment %s: mood %s (%s)", moment.kind, mood, status)

        return run

    # --- pats waiting their turn ---------------------------------------------------------

    def _hold_pat(self, moment: Moment, now: datetime) -> None:
        if self.held_pat is None:
            self.held_pat = (Moment("pat", {**moment.detail, "waited": True, "times": 1}), now)
        else:
            held, since = self.held_pat
            self.held_pat = (Moment("pat", {**held.detail, **moment.detail, "times": held.detail["times"] + 1}), since)
        log.info("moment pat: held until she is free")

    def _held_pat_job(self, now: datetime) -> Job | None:
        """The held pat's line, once she is free and the daemon will take it; None while it waits."""
        if self.held_pat is None:
            return None
        moment, since = self.held_pat
        if now - since > PAT_WAIT:
            self.held_pat = None
            log.info("moment pat: let go after waiting too long")
            return None
        if self.busy or self._say_wait(now) > 0:
            return None
        self.held_pat = None
        return self._line(moment, now, self.mood.name(now))

    def _say_wait(self, now: datetime) -> float:
        """Seconds until the daemon takes another line: its interval since her last one (none with No pause)."""
        if self.last_say is None or self.config.get("no_gap") is True:
            return 0.0
        return max(0.0, SAY_INTERVAL_S - (now - self.last_say).total_seconds())

    def _bond(self) -> str:
        return bond_stage(self.mood.affinity, self.senses.summary()["days_together"])

    # --- the desk ------------------------------------------------------------------------

    def _idle(self) -> timedelta | None:
        seconds = self.idle()
        return timedelta(seconds=seconds) if isinstance(seconds, (int, float)) and seconds >= 0 else None

    def _away(self) -> bool:
        """Nobody at the desk right now (read fresh, so a return between ticks counts)."""
        idle = self._idle()
        return idle is not None and idle >= AWAY_AFTER

    def _post(self, text: str | None, mood: str, reply: bool = False, cue: str | None = None) -> int:
        """Send a line or a mood (and a cue for her body) to the daemon; the time it was taken, for a held pat."""
        extra = {**({"reply": True} if reply else {}), **({"cue": cue} if cue else {})}
        status = self.say(text, mood, **extra)
        if status == 204:
            with self.lock:
                self.last_say = self.now()
        return status

    def _speak(self, moment: Moment, system: str, user: str, mood: str) -> None:
        """A job: one API call, then the line (or silence), the spend and the notice."""
        try:
            reply, usage = self.voice.speak(system, user)
            now = self.now()
            self._spent(f"moment {moment.kind}", usage, reply, now, moment.kind)
            if reply is None:
                return
            if not reply.say:
                log.info("moment %s: she chose silence", moment.kind)
                self._post(None, reply.mood, cue=_cue(moment))
                return
            status = self._post(reply.say, reply.mood, cue=_cue(moment))
            log.info("moment %s: said %d chars, mood %s (%s)", moment.kind, len(reply.say), reply.mood, status)
            if status == 204:
                with self.lock:
                    self.recent = (self.recent + [(day_of(now).isoformat(), reply.say)])[-RECENT_KEPT:]
                    self._remember(now, "said", text=reply.say, mood=reply.mood, moment=moment.kind)
        finally:
            with self.lock:
                self.busy = False

    # --- chatting ------------------------------------------------------------------------

    def _chat(self, raw: object, now: datetime) -> list[Job]:
        """The user typed to her: note it, and return the job that answers (or a mood-only reply)."""
        text = clean_chat(raw)
        if text is None:
            return []
        today = day_of(now).isoformat()
        history = [(who, said) for day, who, said in self.chat_log if day == today][-CHAT_CONTEXT:]
        self.chat_log = (self.chat_log + [(today, "user", text)])[-4 * CHAT_CONTEXT:]
        self._remember(now, "chat", **{"from": "user", "text": text})
        self.senses.on_chat(now)
        mood = self.mood.apply("chat", now)
        if self.mode == "quiet" or self.voice is None:
            return [self._mood_reply(mood, "quiet")]
        reason = self.budget.why_not_chat(now)
        if reason is not None:
            log.info("chat: no answer (%s)", reason)
            return [self._mood_reply(mood, reason)]
        self.budget.take_chat(now)
        known = facts_mod.known(facts_mod.clean(read_json(self.files.facts)))
        system = system_prompt(self.files.identity_text(), self.files.self_text(), known)
        user = functools.partial(
            chat_prompt,
            text,
            history=history,
            now=now,
            display=self.senses.display,
            today=self.senses.summary(),
            mood=mood,
            energy=energy_at(now),
            project=self.redact,
            bond=self._bond(),
        )
        log.info("chat: asking for an answer")
        return [lambda: self._answer(system, user)]

    def _mood_reply(self, mood: str, why: str) -> Job:
        def run() -> None:
            status = self._post(None, mood, reply=True)
            log.info("chat: mood %s only, %s (%s)", mood, why, status)

        return run

    def _answer(self, system: str, user: Callable[..., str]) -> None:
        """A job: one API call, then her answer as a reply. A failed call sends nothing; the box times out and says so.

        `user` builds the prompt once the searches she may make are known.
        """
        with self.lock:
            searches = self.budget.searches_for_answer(self.now())
        if searches:
            log.info("chat: she may search the web up to %d time(s)", searches)
        reply, usage = self.voice.speak(system, user(searches=searches), searches=searches)
        now = self.now()
        if usage.get("web_search_requests"):
            log.info("chat: searched the web %d time(s)", usage["web_search_requests"])
            if reply is not None and reply.notice:
                reply = dataclasses.replace(reply, notice="")
        self._spent("chat", usage, reply, now, "chat")
        if reply is None:
            return
        if not reply.say:
            log.info("chat: she answered without words")
            self._post(None, reply.mood, reply=True)
            return
        status = self._post(reply.say, reply.mood, reply=True)
        log.info("chat: said %d chars, mood %s (%s)", len(reply.say), reply.mood, status)
        if status == 204:
            with self.lock:
                today = day_of(now).isoformat()
                self.chat_log = (self.chat_log + [(today, "her", reply.say)])[-4 * CHAT_CONTEXT:]
                self._remember(now, "chat", **{"from": "her", "text": reply.say, "mood": reply.mood})

    # --- memory --------------------------------------------------------------------------

    def private(self, now: datetime) -> bool:
        return self.private_day == day_of(now).isoformat()

    def _remember(self, now: datetime, kind: str, **fields: object) -> None:
        """Append to her episodes, unless this day of hers is private. Call under `lock`."""
        if not self.private(now):
            self.files.append_episode(now, kind, **fields)

    def _spent(self, label: str, usage: dict, reply, now: datetime, moment: str) -> None:
        """Record one call's spend and her notice, if any."""
        with self.lock:
            if usage:
                cost = self.budget.record(self.config["model"], usage, now)
                write_json(self.files.spend, self.budget.spend)
                log.info("%s: %d in, %d out, $%.5f", label, sum(
                    usage.get(k, 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
                ), usage.get("output_tokens", 0), cost)
            if reply is not None and reply.notice:
                self._remember(now, "notice", text=reply.notice, moment=moment)

    def _save(self) -> None:
        write_json(self.files.state, {"senses": self.senses.to_state(), "mood": self.mood.to_state()})
        self.saved_seen = self.senses.last_seen


def _cue(moment: Moment) -> str | None:
    """The cue her body plays with a moment's line: a greeting greets."""
    return "greet" if moment.kind in GREETINGS else None


def _redacted(detail: dict, redact: Redactor) -> dict:
    return {k: (redact(v) if k == "project" else v) for k, v in detail.items()}


def _event_fields(event: dict, redact: Redactor) -> dict:
    """The few fields an episode keeps of an event: enough for a diary, nothing more."""
    fields = {"event": event.get("hook_event_name")}
    for key in ("tool_name", "source", "reason", "error_type"):
        if isinstance(event.get(key), str):
            fields[key] = event[key]
    if redact(event.get("project")):
        fields["project"] = redact(event.get("project"))
    if isinstance(event.get("session_id"), str):
        fields["session"] = event["session_id"][:8]
    if event.get("subagent"):
        fields["subagent"] = True
    return fields
