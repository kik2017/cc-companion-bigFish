"""The persona's files under the app data folder, and her config. No Qt, no network.

    persona/
      identity.md     you write it; copied from mind/identity.example.md on first run
      self.md         who she has become (written by sleep; may be absent)
      config.json     her knobs, filled with defaults on first run
      state.json      code-owned numbers: mood, counters, today's tallies
      spend.json      API tokens and dollars for the current day
      episodes/<YYYY-MM-DD>.jsonl   append-only log of what she saw, noticed and said, one
                      file per day of hers (05:00 to 05:00; senses.day_of)
      facts.json      what she has learned about you (sleep's consolidate pass)
      diary/<YYYY-MM-DD>.md         one entry per slept day, with its dream
      self_history/<YYYY-MM-DD>.md  each self.md before sleep replaced it
      sleep.json      which passes of which days are done, so a night resumes
      backups/<YYYY-MM-DD>/         her files before each night's sleep (the last 14)
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from companion import settings as prefs

from .senses import day_of

log = logging.getLogger("mind")

TEMPLATE = Path(__file__).with_name("identity.example.md")
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

DEFAULT_CONFIG: dict = {
    "model": "claude-haiku-5-5",
    "lines_per_hour": 30,  # a ceiling, not a target
    "min_gap_s": 60,
    "quiet_hours": [0, 7],  # local [start, end): only late-night lines and pats get an answer
    "daily_usd": 0.50,  # past this she goes mood-only until midnight
    "redact_projects": False,  # True: project folder names reach the API as "project A", "project B"
    "chattiness": "normal",  # quiet, normal or chatty: how many moments she gets to speak on (senses.PROFILES)
    "no_gap": False,  # True: no min_gap_s, no hush after a chat, no pat cooldown (the menu's "No pause")
    "limit": "normal",  # "normal" (lines_per_hour and daily_usd), "unlimited", or N lines and replies a day (budget.py)
    "search": "off",  # web search for chat replies: "off", "normal" (searches_per_day), "unlimited", or N a day
    "searches_per_day": 10,
}
CHOICES = {"chattiness": ("quiet", "normal", "chatty")}


def valid_config(name: str, value: object) -> bool:
    """Whether `value` is a valid setting for config key `name`."""
    default = DEFAULT_CONFIG[name]
    if name == "limit":
        return value in ("normal", "unlimited") or (isinstance(value, int) and not isinstance(value, bool) and value >= 1)
    if name == "search":
        return value in ("off", "normal", "unlimited") or (isinstance(value, int) and not isinstance(value, bool) and value >= 1)
    if name == "searches_per_day":
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0
    if name in CHOICES:
        return value in CHOICES[name]
    if name == "quiet_hours":
        return (
            isinstance(value, list)
            and len(value) == 2
            and all(isinstance(h, int) and not isinstance(h, bool) and 0 <= h <= 24 for h in value)
        )
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, (int, float)):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0
    return isinstance(value, str) and bool(value)


class Files:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or prefs.persona_dir()
        self.identity = self.root / "identity.md"
        self.self_model = self.root / "self.md"
        self.config = self.root / "config.json"
        self.state = self.root / "state.json"
        self.spend = self.root / "spend.json"
        self.episodes = self.root / "episodes"
        self.facts = self.root / "facts.json"
        self.diary = self.root / "diary"
        self.self_history = self.root / "self_history"
        self.sleep = self.root / "sleep.json"
        self.backups = self.root / "backups"

    def setup(self) -> None:
        """Create the folder, her identity from the template and a default config, each only if missing."""
        self.episodes.mkdir(parents=True, exist_ok=True)
        if not self.identity.exists():
            self.identity.write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
        if not self.config.exists():
            write_json(self.config, DEFAULT_CONFIG)

    def load_config(self) -> dict:
        """The defaults, overridden by each valid value in config.json."""
        config = dict(DEFAULT_CONFIG)
        raw = read_json(self.config)
        for name in DEFAULT_CONFIG:
            if name in raw and valid_config(name, raw[name]):
                config[name] = raw[name]
            elif name in raw:
                log.warning("config.json: ignoring invalid %s", name)
        return config

    def identity_text(self) -> str:
        """Her identity with the <!-- notes --> removed: they are for you, never for the model."""
        try:
            text = self.identity.read_text(encoding="utf-8")
        except OSError:
            text = TEMPLATE.read_text(encoding="utf-8")
        return re.sub(r"\n{3,}", "\n\n", COMMENT.sub("", text)).strip()

    def self_text(self) -> str:
        try:
            return self.self_model.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def append_episode(self, when: datetime, kind: str, **fields: object) -> None:
        record = {"t": when.isoformat(timespec="seconds"), "kind": kind, **fields}
        path = self.episodes / f"{day_of(when).isoformat()}.jsonl"
        try:
            with path.open("a", encoding="utf-8") as out:
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            log.warning("could not append to %s", path.name)

    def recent_said(self, when: datetime, count: int = 5) -> list[str]:
        """Her last `count` lines today, oldest first."""
        day = day_of(when)
        said = [r["text"] for r in self.day_records(day) if r.get("kind") == "said" and isinstance(r.get("text"), str)]
        return said[-count:]

    def day_records(self, day: date) -> list[dict]:
        """Every episode of her `day`, in time order.

        Read from that day's file and the next calendar day's, keeping records whose
        time falls in the day: logs written before days ran 05:00 to 05:00 were
        filed by calendar date.
        """
        records = []
        seen: set[Path] = set()
        for name in (day, day + timedelta(days=1)):
            path = self.episodes / f"{name.isoformat()}.jsonl"
            if path in seen:
                continue
            seen.add(path)
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    record = json.loads(line)
                    when = datetime.fromisoformat(record["t"])
                except (ValueError, KeyError, TypeError):
                    continue
                if day_of(when) == day:
                    records.append(record)
        unique = {json.dumps(r, sort_keys=True, ensure_ascii=False): r for r in records}
        return sorted(unique.values(), key=lambda r: r["t"])

    def episode_days(self) -> list[date]:
        """Every day of hers with at least one episode, oldest first."""
        days = set()
        for path in self.episodes.glob("*.jsonl"):
            try:
                calendar = date.fromisoformat(path.stem)
            except ValueError:
                continue
            for day in (calendar - timedelta(days=1), calendar):
                if self.day_records(day):
                    days.add(day)
        return sorted(days)


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path: Path, data: dict) -> None:
    """Atomic, so a crash mid-write never leaves half a file."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        log.warning("could not write %s", path.name)
