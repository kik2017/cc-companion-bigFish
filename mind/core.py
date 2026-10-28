"""The mind: messages from the daemon in, moments, mood, memory and lines out.

No threads and no network here. handle() and tick() run on the main loop and return
jobs (callables) for each line worth asking the model for; __main__ runs the jobs on a
worker thread, so a slow API call never holds up the next event. Shared state is
touched under `lock`, never across the API call itself.

Sleep (sleep.py) runs as a job too, when an ended day is still unslept and either the
mind has just started or you have been away IDLE_BEFORE_SLEEP. It holds `busy`, so she
does not talk in her sleep; her first greeting after it may mention her dream.

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

A chat gets an answer sent as a reply (/say with "reply": true), which the daemon lets
through once past its line interval because it asked for it. The conversation is kept in
memory for the day and, unless the day is private, in her episodes as kind "chat", where
sleep learns from what the user said. While a day is private, nothing is written to her
episodes at all (events, moments, lines, notes or chats), so sleep has nothing of it.
"""

from __future__ import annotations

import logging
import threading
import unicodedata
from collections.abc import Callable
from datetime import datetime

from companion.server import UNPRINTABLE

from . import facts as facts_mod
from .budget import OUT_OF_LINES, Budget
from .files import CHOICES, Files, read_json, valid_config, write_json
from .mood import Mood, energy_at
from .prompts import Redactor, chat_prompt, system_prompt, user_prompt
from .senses import Moment, Senses, day_of
from .sleep import IDLE_BEFORE_SLEEP, Sleep
from .voice import Voice

log = logging.getLogger("mind")

Job = Callable[..., None]
SayFn = Callable[..., int]  # (text or None for mood-only, mood, reply=False) -> HTTP status
CHAT_MAX_CHARS = 300
CHAT_CONTEXT = 10  # the last this many chat lines of today go with each new message
RECENT_KEPT = 20


def clean_chat(raw: object) -> str | None:
    """What the user typed as one printable line of at most CHAT_MAX_CHARS; None if nothing is left."""
    if not isinstance(raw, str):
        return None
    spaced = (" " if ch.isspace() else ch for ch in raw[: 4 * CHAT_MAX_CHARS])
    text = " ".join("".join(ch for ch in spaced if unicodedata.category(ch) not in UNPRINTABLE).split())
    return text[:CHAT_MAX_CHARS] or None


class Mind:
    def __init__(self, files: Files, config: dict, voice: Voice | None, say: SayFn, now: Callable[[], datetime] = datetime.now) -> None:
        self.files = files
        self.config = config
        self.voice = voice
        self.say = say
        self.now = now
        self.lock = threading.Lock()
        state = read_json(files.state)
        self.senses = Senses.from_state(state.get("senses", {}), config["chattiness"])
        self.senses.set_no_gap(config.get("no_gap") is True)
        self.mood = Mood.from_state(state.get("mood", {}))
        self.budget = Budget(config, read_json(files.spend))
        self.redact = Redactor(config["redact_projects"])
        self.mode = "on"
        self.busy = False  # a voice or sleep job is in flight
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
            jobs = [job for moment in moments if (job := self._react(moment, now)) is not None]
            self._save()
        return jobs

    def tick(self) -> list[Job]:
        now = self.now()
        with self.lock:
            moments = self.senses.tick(now)
            jobs = [job for moment in moments if (job := self._react(moment, now)) is not None]
            if moments:
                self._save()
            sleep = self._sleep_job(now)
        return jobs + ([sleep] if sleep is not None else [])

    # --- sleeping ------------------------------------------------------------------------

    def _sleep_job(self, now: datetime) -> Job | None:
        started, self.just_started = self.just_started, False
        if self.sleeper is None or self.busy:
            return None
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
            self.say(None, "sleepy")
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

    def _react(self, moment: Moment, now: datetime) -> Job | None:
        """Log the moment, move her mood, and return a job if it is worth a line."""
        mood = self.mood.apply(moment.kind, now)
        self._remember(now, "moment", moment=moment.kind, **_redacted(moment.detail, self.redact))
        waiting = self.senses.display.get("state") == "waiting"
        if self.mode == "quiet" or self.voice is None:
            return self._mood_only(moment, mood)
        reason = self.budget.why_not(moment.kind, now, waiting)
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
        )
        log.info("moment %s: asking for a line", moment.kind)
        return lambda: self._speak(moment, system, user, mood)

    def _mood_only(self, moment: Moment, mood: str) -> Job:
        def run() -> None:
            status = self.say(None, mood)
            log.info("moment %s: mood %s (%s)", moment.kind, mood, status)

        return run

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
                self.say(None, reply.mood)
                return
            status = self.say(reply.say, reply.mood)
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
        user = chat_prompt(
            text,
            history=history,
            now=now,
            display=self.senses.display,
            today=self.senses.summary(),
            mood=mood,
            energy=energy_at(now),
            project=self.redact,
        )
        log.info("chat: asking for an answer")
        return [lambda: self._answer(system, user)]

    def _mood_reply(self, mood: str, why: str) -> Job:
        def run() -> None:
            status = self.say(None, mood, reply=True)
            log.info("chat: mood %s only, %s (%s)", mood, why, status)

        return run

    def _answer(self, system: str, user: str) -> None:
        """A job: one API call, then her answer as a reply. A failed call sends nothing; the box times out and says so."""
        reply, usage = self.voice.speak(system, user)
        now = self.now()
        self._spent("chat", usage, reply, now, "chat")
        if reply is None:
            return
        if not reply.say:
            log.info("chat: she answered without words")
            self.say(None, reply.mood, reply=True)
            return
        status = self.say(reply.say, reply.mood, reply=True)
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
