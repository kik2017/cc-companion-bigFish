"""Sleep: each ended day of hers becomes a diary entry, facts, a new self and a dream.

    python -m mind.sleep --dry-run               print what tonight's sleep would write
    python -m mind.sleep --dry-run --day 2026-10-27
    python -m mind.sleep                         sleep every unslept day now

Four passes per day, one model call each, in order:
  diary         a first-person entry from the day's timeline (code builds the timeline)
  consolidate   her notices become facts: add, reinforce, merge, archive (facts.py)
  self          the next self.md, in four sections, coloured by her bond; the old one goes to self_history/
  dream         a short dream from fragments of her days, saved in the diary

Rules every pass keeps: it never invents (prompts quote real records; a diary that names
a time or a project-like name the timeline lacks is rejected), it never loses (episodes
are untouched, facts are archived, every self.md is kept) and it resumes (sleep.json
records each finished pass). The persona's files are backed up before a day's first pass.
A day ends at 05:00 (senses.day_of); the mind sleeps ended days when it starts and when
you have been away IDLE_BEFORE_SLEEP.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
import shutil
import sys
from collections import Counter
from collections.abc import Callable
from datetime import date, datetime, timedelta

from . import facts as facts_mod
from .files import Files, read_json, write_json
from .mood import bond_stage
from .senses import day_of, tool_label

log = logging.getLogger("mind")

PASSES = ("diary", "consolidate", "self", "dream")
IDLE_BEFORE_SLEEP = timedelta(minutes=30)
MAX_CATCH_UP_DAYS = 7  # after a long break, only the most recent days are slept
KEEP_BACKUPS = 14
BUCKET_MIN = 30
# Size as CJK characters plus other words, so one limit fits Chinese and English text.
DIARY_SIZE, SELF_SIZE, DREAM_SIZE = 450, 700, 220
EFFORT = "medium"
MAX_TOKENS = 8000

FRAME = """You are the inner voice of the desktop companion character described below, at night, after her day. You write as her, in the first person, in her own language and voice, as her identity says.

All she ever knows of the user's work is metadata: event and tool names, times, counts, project folder names, and her own lines and notes. She never sees code, prompts or Claude's replies. The one exception is what the user typed to her in her chat box, which she remembers as they said it. Never invent anything that is not in the material you are given.

Each request gives you one task and asks for JSON."""

TEXT_SCHEMAS = {
    "diary": {"type": "object", "properties": {"entry": {"type": "string"}}, "required": ["entry"], "additionalProperties": False},
    "self": {
        "type": "object",
        "properties": {"self": {"type": "string"}, "change": {"type": "string"}},
        "required": ["self", "change"],
        "additionalProperties": False,
    },
    "dream": {"type": "object", "properties": {"dream": {"type": "string"}}, "required": ["dream"], "additionalProperties": False},
}
CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")
TIME = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")
IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-_.][A-Za-z0-9]+)+")


def size(text: str) -> int:
    return len(CJK.findall(text)) + len(CJK.sub(" ", text).split())


# --- the timeline ------------------------------------------------------------------------


def timeline(day: date, records: list[dict]) -> tuple[str, set[str]]:
    """The day as plain text for the model, and the names it mentions (projects and tools).

    Events are counted in BUCKET_MIN-minute blocks; moments, her lines, her notices and
    the chat (the user's words and her answers) are listed one by one with their times.
    """
    events = [r for r in records if r.get("kind") == "event"]
    projects = Counter(r["project"] for r in events if isinstance(r.get("project"), str))
    tools = Counter(label for r in events if r.get("event") == "PreToolUse" and (label := tool_label(r.get("tool_name"))))
    turns = sum(1 for r in events if r.get("event") == "Stop" and not r.get("subagent"))
    errors = sum(1 for r in events if r.get("event") in ("StopFailure", "PostToolUseFailure"))
    sessions = sum(1 for r in events if r.get("event") == "SessionStart" and r.get("source") in ("startup", "resume"))
    times = [_hm(r["t"]) for r in records]
    lines = [f"Her day {day.isoformat()} ({day.strftime('%A')}), which runs from 05:00 to 05:00 the next morning."]
    if times:
        lines.append(f"Active from {times[0]} to {times[-1]}.")
    lines.append(f"{sessions} session(s) started; {turns} turn(s) of work done; {errors} error(s).")
    if projects:
        lines.append("Projects: " + ", ".join(f"{p} ({n} events)" for p, n in projects.most_common()))
    if tools:
        lines.append("Tools Claude used: " + ", ".join(f"{t} x{n}" for t, n in tools.most_common(8)))
    lines.append("Timeline:")
    buckets: dict[str, Counter] = {}
    entries: list[tuple[str, str]] = []
    for record in records:
        when = datetime.fromisoformat(record["t"])
        kind = record.get("kind")
        if kind == "event":
            start = when.replace(minute=when.minute - when.minute % BUCKET_MIN, second=0)
            key = start.strftime("%H:%M") + "-" + (start + timedelta(minutes=BUCKET_MIN)).strftime("%H:%M")
            bucket = buckets.setdefault(key, Counter())
            if not entries or entries[-1] != (key, "bucket"):
                if key not in [k for k, kind_ in entries if kind_ == "bucket"]:
                    entries.append((key, "bucket"))
            name = record.get("event")
            if name == "Stop" and not record.get("subagent"):
                bucket["turns done"] += 1
            elif name in ("StopFailure", "PostToolUseFailure"):
                bucket["errors"] += 1
            elif name == "PreToolUse" and (label := tool_label(record.get("tool_name"))):
                bucket[label] += 1
            elif name == "SessionStart":
                bucket["session started"] += 1
            if record.get("project"):
                bucket["@" + record["project"]] += 1
        elif kind == "moment":
            entries.append((_hm(record["t"]), f"moment: {record.get('moment')}"))
        elif kind == "said":
            entries.append((_hm(record["t"]), f'she said: "{record.get("text")}" ({record.get("mood")})'))
        elif kind == "notice":
            entries.append((_hm(record["t"]), f"she noted: {record.get('text')}"))
        elif kind == "chat" and record.get("from") == "user":
            entries.append((_hm(record["t"]), f'the user said to her: "{record.get("text")}"'))
        elif kind == "chat":
            entries.append((_hm(record["t"]), f'she answered: "{record.get("text")}" ({record.get("mood")})'))
    for key, what in entries:
        if what != "bucket":
            lines.append(f"{key} {what}")
            continue
        counts = buckets[key]
        where = [k[1:] for k, _ in counts.most_common() if k.startswith("@")]
        doing = [f"{k} x{n}" if n > 1 else k for k, n in counts.most_common() if not k.startswith("@")]
        lines.append(f"{key} working" + (f" in {', '.join(where)}" if where else "") + (": " + ", ".join(doing) if doing else ""))
    return "\n".join(lines), set(projects) | set(tools)


def _hm(stamp: str) -> str:
    return datetime.fromisoformat(stamp).strftime("%H:%M")


def grounded(entry: str, timeline_text: str, names: set[str]) -> str | None:
    """None if every time and project-like name in `entry` appears in the timeline, else what is not."""
    allowed_times = {f"{int(h):02d}:{m}" for h, m in TIME.findall(timeline_text)}
    for h, m in TIME.findall(entry):
        if f"{int(h):02d}:{m}" not in allowed_times:
            return f"time {h}:{m}"
    lowered = {n.lower() for n in names}
    for token in IDENTIFIER.findall(entry):
        if token.lower() not in lowered and token.lower() not in timeline_text.lower():
            return f"name {token}"
    return None


# --- the night ---------------------------------------------------------------------------


class Sleep:
    def __init__(
        self,
        files: Files,
        voice,
        record: Callable[[dict], None] = lambda usage: None,
        *,
        dry_run: bool = False,
        out: Callable[[str], None] = lambda text: None,
    ) -> None:
        self.files = files
        self.voice = voice
        self.record = record
        self.dry_run = dry_run
        self.out = out
        self.state = read_json(files.sleep)
        self.state.setdefault("days", {})

    def due_days(self, now: datetime) -> list[date]:
        """Ended days with episodes and unfinished passes, oldest first; at most MAX_CATCH_UP_DAYS."""
        today = day_of(now)
        days = [d for d in self.files.episode_days() if d < today and not self.state["days"].get(d.isoformat(), {}).get("done")]
        return days[-MAX_CATCH_UP_DAYS:]

    def run(self, day: date) -> dict:
        """Sleep one day: every pass not yet done. Returns the day's pass results."""
        key = day.isoformat()
        done = dict(self.state["days"].get(key, {}))
        records = self.files.day_records(day)
        text, names = timeline(day, records)
        if not done and not self.dry_run:
            self._backup(key)
        identity, current_self = self.files.identity_text(), self.files.self_text()
        system = FRAME + f"\n\n<identity>\n{identity}\n</identity>" + (f"\n\n<self>\n{current_self}\n</self>" if current_self else "")
        diary_entry = self._read_diary(key)
        changes: list[str] = []
        for name in PASSES:
            if done.get(name) in ("ok", "skipped"):
                continue
            if name == "diary":
                result, diary_entry = self._diary(system, day, text, names)
            elif name == "consolidate":
                result, changes = self._consolidate(system, key, records, text)
            elif name == "self":
                result = self._self(system, key, current_self, diary_entry, changes)
            else:
                result = self._dream(system, day, records)
            done[name] = result
            log.info("sleep %s: %s %s", key, name, result)
            self.out(f"{key} {name}: {result}")
            if not self.dry_run:
                self.state["days"][key] = done
                write_json(self.files.sleep, self.state)
        done["done"] = True
        if not self.dry_run:
            self.state["days"][key] = done
            write_json(self.files.sleep, self.state)
        return done

    # --- passes ----------------------------------------------------------------------------

    def _ask(self, system: str, user: str, schema: dict) -> dict | None:
        text, usage = self.voice.ask(system, user, schema, effort=EFFORT, max_tokens=MAX_TOKENS)
        if usage:
            self.record(usage)
        try:
            data = json.loads(text) if text else None
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def _diary(self, system: str, day: date, text: str, names: set[str]) -> tuple[str, str]:
        previous = self._previous_diary(day)
        user = (
            f"Task: write her diary entry for her day {day.isoformat()}, in her voice and language, at most about "
            f"250 words (or 400 Chinese characters). Write about how the day felt to her as well as what happened. "
            f"Mention a clock time only if it appears in the timeline, and name only projects and tools that appear in it.\n\n"
            + (f"Her previous diary entry, for continuity:\n{previous}\n\n" if previous else "")
            + f"The day's timeline:\n{text}"
        )
        for attempt in range(2):
            data = self._ask(system, user, TEXT_SCHEMAS["diary"])
            entry = (data or {}).get("entry", "").strip() if data else ""
            problem = None if entry else "empty"
            problem = problem or ("too long" if size(entry) > DIARY_SIZE else grounded(entry, text, names))
            if problem is None:
                self._write(self.files.diary / f"{day.isoformat()}.md", f"# {day.isoformat()}\n\n{entry}\n")
                return "ok", entry
            log.info("sleep %s: diary rejected (%s)", day, problem)
            user += f"\n\nYour last entry was rejected ({problem}). Use only times and names from the timeline, and stay short."
        return "failed", ""

    def _consolidate(self, system: str, key: str, records: list[dict], text: str) -> tuple[str, list[str]]:
        doc = facts_mod.clean(read_json(self.files.facts))
        notices = [r["text"] for r in records if r.get("kind") == "notice" and isinstance(r.get("text"), str)]
        user_spoke = any(r.get("kind") == "chat" and r.get("from") == "user" for r in records)
        listing = "\n".join(
            f"{f['id']}: {f['text']} (seen on {len(f['days'])} day(s)" + ("; the user told her" if f["told"] else "") + ")"
            for f in facts_mod.active(doc)
        ) or "(none yet)"
        user = (
            "Task: update what she knows about the user, from today's material.\n"
            "Facts are short, third-person notes about the user's habits, rhythms and preferences that the metadata "
            "really shows, for example \"often works past 1am\" or \"mostly works on cc-companion\", or that the user "
            "told her themself in the chat (the best evidence there is). Her own lines are never evidence. "
            "Operations: add (a new fact, in text), reinforce (ids of facts today's material supports again), "
            "merge (two or more ids that say the same thing, with one better text), archive (ids that today shows "
            "are wrong or stale, with a reason). Prefer reinforce over adding a near-duplicate. "
            "Set \"told\" to true only when the user said it themself in today's chat (a line marked "
            "\"the user said to her\"); false for anything taken from metadata, her notes or her own lines. "
            "Return an empty list if nothing is worth changing.\n\n"
            f"Her active facts:\n{listing}\n\nHer notes from today:\n" + ("\n".join(f"- {n}" for n in notices) or "(none)")
            + f"\n\nThe day's timeline:\n{text}"
        )
        data = self._ask(system, user, facts_mod.OPS_SCHEMA)
        if data is None:
            return "failed", []
        # Read again: you may have archived a fact in What she knows while the model thought,
        # and applying to the copy read before the call would bring it back.
        doc = facts_mod.clean(read_json(self.files.facts))
        doc, changes = facts_mod.apply_ops(doc, data.get("ops", []), key, user_spoke=user_spoke)
        self._write_json(self.files.facts, doc)
        for change in changes:
            self.out(f"  {change}")
        return "ok", changes

    def _self(self, system: str, key: str, current: str, diary_entry: str, changes: list[str]) -> str:
        doc = facts_mod.clean(read_json(self.files.facts))
        user = (
            "Task: rewrite her self.md: who she is becoming, in the first person, in her voice and language, at most about "
            "400 words (or 700 Chinese characters). Write it in four short sections, each a markdown heading (## ...) in "
            "her own language and then a few sentences: who she is with the user; their habits together; running jokes "
            "and small rituals; what she has come to like. Grow it gently from the current version: keep what has lasted "
            "over days and fold in what is new. Keep traits and patterns, not one-off details: drop exact times, dates and "
            "single events unless they have become a habit. When space runs short, merge and shorten rather than drop "
            "something lasting. Let how close she feels to the user show in how she writes about them. Her identity always "
            "wins: never change her name, language, core personality or honesty, and never contradict it. Also give a "
            "one-line summary of what changed.\n\n"
            f"How close she feels to the user now: {self._bond()} (new, familiar, close or dear; never name it).\n\n"
            f"Her current self.md:\n{current or '(empty: this is her first night)'}\n\n"
            f"Tonight's diary entry:\n{diary_entry or '(none)'}\n\n"
            "What she learned tonight:\n" + ("\n".join(f"- {c}" for c in changes) or "(nothing new)")
            + "\n\nWhat she knows about the user:\n" + ("\n".join(f"- {k}" for k in facts_mod.known(doc)) or "(nothing certain yet)")
        )
        data = self._ask(system, user, TEXT_SCHEMAS["self"])
        new = (data or {}).get("self", "").strip() if data else ""
        if not new or size(new) > SELF_SIZE:
            return "failed"
        change = " ".join(str((data or {}).get("change", "")).split())[:200]
        if current:
            self._write(self.files.self_history / f"{key}.md", f"<!-- replaced on {key}: {change} -->\n\n{current}\n")
        self._write(self.files.self_model, new + "\n")
        self.out(f"  self: {change}")
        return "ok"

    def _bond(self) -> str:
        """Her bond with the user as of now, from the mind's saved state (affinity and days together)."""
        state = read_json(self.files.state)
        mood = state.get("mood") if isinstance(state.get("mood"), dict) else {}
        senses = state.get("senses") if isinstance(state.get("senses"), dict) else {}
        affinity = mood.get("affinity") if isinstance(mood.get("affinity"), int) else 0
        days = senses.get("days") if isinstance(senses.get("days"), list) else []
        return bond_stage(affinity, len(days))

    def _dream(self, system: str, day: date, records: list[dict]) -> str:
        rng = random.Random(day.toordinal())
        fragments = [
            r.get("text") or r.get("moment") or tool_label(r.get("tool_name"))
            for r in records
            if r.get("kind") in ("said", "notice", "moment", "chat") or (r.get("kind") == "event" and tool_label(r.get("tool_name")))
        ]
        fragments = [f for f in dict.fromkeys(f for f in fragments if isinstance(f, str))]
        picked = rng.sample(fragments, min(6, len(fragments)))
        older = sorted(p for p in self.files.diary.glob("*.md") if p.stem < day.isoformat()) if self.files.diary.exists() else []
        memories = [p.read_text(encoding="utf-8").split("\n", 2)[-1][:300] for p in rng.sample(older, min(2, len(older)))]
        if not picked and not memories:
            return "skipped"
        user = (
            "Task: dream. Write one short dream she has tonight, in her voice and language, at most about 120 words "
            "(or 200 Chinese characters): strange, gentle and playful, woven loosely from these fragments of her days. "
            "It is a dream, so it does not need to make sense.\n\nFragments:\n"
            + "\n".join(f"- {f}" for f in picked)
            + ("\n\nOlder memories:\n" + "\n".join(f"- {m}" for m in memories) if memories else "")
        )
        data = self._ask(system, user, TEXT_SCHEMAS["dream"])
        dream = (data or {}).get("dream", "").strip() if data else ""
        if not dream or size(dream) > DREAM_SIZE:
            return "failed"
        path = self.files.diary / f"{day.isoformat()}.md"
        existing = path.read_text(encoding="utf-8") if path.exists() else f"# {day.isoformat()}\n"
        self._write(path, existing.rstrip("\n") + f"\n\n## Dream\n\n{dream}\n")
        if not self.dry_run:
            self.state["dream_to_tell"] = {"day": day.isoformat(), "text": dream}
        return "ok"

    def take_dream(self) -> str | None:
        """The last dream she has not told yet; taking it marks it told."""
        dream = self.state.pop("dream_to_tell", None)
        if not isinstance(dream, dict) or not isinstance(dream.get("text"), str):
            return None
        if not self.dry_run:
            write_json(self.files.sleep, self.state)
        return dream["text"]

    # --- files -----------------------------------------------------------------------------

    def _previous_diary(self, day: date) -> str:
        if not self.files.diary.exists():
            return ""
        older = sorted(p for p in self.files.diary.glob("*.md") if p.stem < day.isoformat())
        if not older:
            return ""
        return older[-1].read_text(encoding="utf-8").split("\n## Dream")[0].strip()[:1500]

    def _read_diary(self, key: str) -> str:
        path = self.files.diary / f"{key}.md"
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8").split("\n## Dream")[0].split("\n", 1)[-1].strip()

    def _write(self, path, text: str) -> None:
        if self.dry_run:
            self.out(f"--- would write {path.relative_to(self.files.root)} ---\n{text}")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)

    def _write_json(self, path, data: dict) -> None:
        if self.dry_run:
            self.out(f"--- would write {path.relative_to(self.files.root)} ---\n{json.dumps(data, ensure_ascii=False, indent=2)}")
            return
        write_json(path, data)

    def _backup(self, key: str) -> None:
        target = self.files.backups / key
        target.mkdir(parents=True, exist_ok=True)
        for path in (self.files.facts, self.files.self_model, self.files.state, self.files.sleep, self.files.identity, self.files.config):
            if path.exists():
                shutil.copy2(path, target / path.name)
        if self.files.diary.exists():
            shutil.copytree(self.files.diary, target / "diary", dirs_exist_ok=True)
        for old in sorted(p for p in self.files.backups.iterdir() if p.is_dir())[:-KEEP_BACKUPS]:
            shutil.rmtree(old, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sleep: diary, facts, self and dream for each ended day.")
    parser.add_argument("--dry-run", action="store_true", help="call the model but write nothing; print what would be written")
    parser.add_argument("--day", type=date.fromisoformat, help="sleep this day (YYYY-MM-DD), even if already slept")
    args = parser.parse_args(argv)

    import anthropic

    from .budget import Budget
    from .voice import Voice, find_api_key

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    files = Files()
    files.setup()
    config = files.load_config()
    key = find_api_key()
    if key is None:
        print("no API key: set CC_COMPANION_API_KEY", file=sys.stderr)
        return 3
    budget = Budget(config, read_json(files.spend))

    def record(usage: dict) -> None:
        budget.record(config["model"], usage, datetime.now())
        if not args.dry_run:
            write_json(files.spend, budget.spend)

    voice = Voice(anthropic.Anthropic(api_key=key, timeout=120.0, max_retries=1), config["model"])
    sleep = Sleep(files, voice, record, dry_run=args.dry_run, out=print)
    if args.day:
        sleep.state["days"].pop(args.day.isoformat(), None)
        days = [args.day]
    else:
        days = sleep.due_days(datetime.now())
    if not days:
        print("nothing to sleep on: every ended day is already slept")
        return 0
    for day in days:
        sleep.run(day)
    print(f"spent ${budget.spent_today(datetime.now()):.4f} today in all")
    return 0


if __name__ == "__main__":
    sys.exit(main())
