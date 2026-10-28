"""Preferences, app data paths and logging setup. No Qt imports."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, timedelta
from pathlib import Path

APP_NAME = "cc-companion"
LOG_MAX_BYTES = 2_000_000

# Pet height in logical px: 0.6, 0.8 and 1.0 of the native 344 px frame.
SIZES = {"S": 206, "M": 275, "L": 344}
BUBBLE_MODES = ("always", "hidden", "attention")
ACTIVITY_LEVELS = ("quiet", "normal", "lively")
# off: /say is refused; on: her lines show and her mood plays; quiet: mood clips only.
PERSONA_MODES = ("off", "on", "quiet")
# A skin is a folder name under skins/, never a path.
SKIN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def app_dir() -> Path:
    """Settings and log root; CC_COMPANION_HOME overrides it (tests use this)."""
    override = os.environ.get("CC_COMPANION_HOME")
    if override:
        root = Path(override)
    elif sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_NAME
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def settings_path() -> Path:
    return app_dir() / "settings.json"


def log_path() -> Path:
    return app_dir() / "companion.log"


def skins_dir() -> Path:
    return app_dir() / "skins"


def persona_dir() -> Path:
    """The persona's own files (identity, memory, diary); kept out of the repo."""
    return app_dir() / "persona"


# The persona's chattiness, No pause, Limit and Private today live in her own config.json, which her mind
# reads at start; the menu writes them there too. mind/files.py holds the same choices and
# default, and mind/senses.py the same day boundary.
CHATTINESS = ("quiet", "normal", "chatty")
DEFAULT_CHATTINESS = "normal"
DAY_START_HOUR = 5  # her day runs 05:00 to 05:00


def persona_config_path() -> Path:
    return persona_dir() / "config.json"


def her_day(now: datetime | None = None) -> str:
    """Her day for a local time, as YYYY-MM-DD: 00:00-05:00 belongs to the day before."""
    return ((now or datetime.now()) - timedelta(hours=DAY_START_HOUR)).date().isoformat()


def _persona_config() -> dict:
    return _read_persona_json("config.json")


def _read_persona_json(name: str) -> dict:
    try:
        raw = json.loads((persona_dir() / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_persona_json(name: str, data: dict) -> bool:
    """Atomic, so neither process ever reads half a file."""
    path = persona_dir() / name
    tmp = path.with_suffix(".json.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        logging.getLogger("companion").warning("could not write %s", path)
        return False
    return True


def read_chattiness() -> str:
    value = _persona_config().get("chattiness")
    return value if value in CHATTINESS else DEFAULT_CHATTINESS


def write_chattiness(level: str) -> bool:
    """Set "chattiness" in her config.json, keeping every other key; False if `level` is not a choice."""
    if level not in CHATTINESS:
        return False
    return _write_persona_config("chattiness", level)


def read_no_gap() -> bool:
    """No pause: True lifts the gaps between her lines (min_gap_s, the hush after a chat, the pat cooldown, the /say interval)."""
    return _persona_config().get("no_gap") is True


# Her limit, as mind/budget.py reads it: "normal" (her hourly lines and daily dollar cap),
# "unlimited", or N lines and replies a day. The normal numbers default as in mind/files.py.
LIMIT_MODES = ("normal", "unlimited")
DEFAULT_LINES_PER_HOUR = 30
DEFAULT_DAILY_USD = 0.50
MAX_DAILY_LINES = 100_000


def valid_limit(value: object) -> bool:
    return value in LIMIT_MODES or (isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_DAILY_LINES)


def read_limit() -> str | int:
    value = _persona_config().get("limit")
    return value if valid_limit(value) else "normal"


def write_limit(value: str | int) -> bool:
    """Set her limit, keeping every other key; False if `value` is not "normal", "unlimited" or 1..MAX_DAILY_LINES."""
    if not valid_limit(value):
        return False
    return _write_persona_config("limit", value)


def read_normal_limit() -> tuple[int, float]:
    """(lines an hour, dollars a day) of the normal limit, from her config.json."""
    raw = _persona_config()
    lines, usd = raw.get("lines_per_hour"), raw.get("daily_usd")
    return (
        int(lines) if _non_negative(lines) else DEFAULT_LINES_PER_HOUR,
        float(usd) if _non_negative(usd) else DEFAULT_DAILY_USD,
    )


def _non_negative(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


def write_no_gap(on: bool) -> bool:
    return _write_persona_config("no_gap", bool(on))


def read_private_day() -> str | None:
    """The day of hers marked private, if any (it may be an earlier day, already over)."""
    value = _persona_config().get("private_day")
    return value if isinstance(value, str) else None


def write_private_day(day: str | None) -> bool:
    """Mark `day` private in her config.json, or clear it with None, keeping every other key."""
    return _write_persona_config("private_day", day)


def _write_persona_config(key: str, value: object) -> bool:
    raw = _persona_config()
    raw[key] = value
    return _write_persona_json("config.json", raw)


# Her spend.json (written by mind/budget.py) and facts.json (mind/facts.py), read here for
# the menu's spend line and the What she knows window, which can also archive a fact.
ARCHIVED_BY_YOU = "archived by you"


def read_spend_today(today: date | None = None) -> tuple[int, float]:
    """(lines and replies, dollars) she has spent today; (0, 0.0) if nothing yet."""
    raw = _read_persona_json("spend.json")
    if raw.get("date") != (today or date.today()).isoformat():
        return 0, 0.0
    lines, usd = raw.get("lines"), raw.get("usd")
    return (
        lines if isinstance(lines, int) and not isinstance(lines, bool) else 0,
        float(usd) if _non_negative(usd) else 0.0,
    )


def read_facts() -> list[dict]:
    """Her active facts as {"id", "text", "days", "seen", "told"}, malformed ones left out."""
    facts = _read_persona_json("facts.json").get("facts")
    shown = []
    for fact in facts if isinstance(facts, list) else []:
        if not isinstance(fact, dict) or not isinstance(fact.get("id"), str) or not isinstance(fact.get("text"), str):
            continue
        if fact.get("archived"):
            continue
        days = [d for d in fact.get("days", []) if isinstance(d, str)] if isinstance(fact.get("days"), list) else []
        seen = fact.get("seen") if isinstance(fact.get("seen"), int) else len(days)
        shown.append({"id": fact["id"], "text": fact["text"], "days": days, "seen": seen, "told": fact.get("told") is True})
    return shown


def set_fact_archived(fact_id: str, archived: bool) -> bool:
    """Archive a fact you say is wrong (kept, never deleted), or undo that; False if it is not there or not yours to undo."""
    raw = _read_persona_json("facts.json")
    facts = raw.get("facts")
    for fact in facts if isinstance(facts, list) else []:
        if not isinstance(fact, dict) or fact.get("id") != fact_id:
            continue
        if archived and not fact.get("archived"):
            fact["archived"], fact["reason"] = True, ARCHIVED_BY_YOU
        elif not archived and fact.get("archived") and fact.get("reason") == ARCHIVED_BY_YOU:
            fact["archived"], fact["reason"] = False, ""
        else:
            return False
        return _write_persona_json("facts.json", raw)
    return False


def say_token_path() -> Path:
    """Where the daemon leaves this run's /say token for local tools and the persona."""
    return app_dir() / "say-token"


def write_say_token(token: str) -> None:
    """Replace the token file atomically, so a reader never sees half a token."""
    path = say_token_path()
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(token, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        logging.getLogger("companion").warning("could not write the say token to %s", path)


@dataclass
class Settings:
    x: int | None = None
    y: int | None = None
    size: str = "M"
    bubble_mode: str = "always"
    activity: str = "normal"
    reduced_motion: bool = False
    sounds: bool = False
    subagents: bool = True
    skin: str = ""  # empty: the bundled character
    events_feed: bool = False  # the loopback WebSocket feed in feed.py
    persona: str = "off"  # one of PERSONA_MODES

    @property
    def size_px(self) -> int:
        return SIZES.get(self.size, SIZES["M"])


_CHOICES = {"size": tuple(SIZES), "bubble_mode": BUBBLE_MODES, "activity": ACTIVITY_LEVELS, "persona": PERSONA_MODES}


def _valid(name: str, value: object, default: object) -> bool:
    if name in ("x", "y"):
        return value is None or (isinstance(value, int) and not isinstance(value, bool))
    if name == "skin":
        return isinstance(value, str) and (value == "" or SKIN_NAME.fullmatch(value) is not None)
    if type(value) is not type(default):
        return False
    return name not in _CHOICES or value in _CHOICES[name]


def load() -> Settings:
    """Read settings.json; a missing, corrupt or partly invalid file falls back to defaults per field."""
    settings = Settings()
    try:
        raw = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return settings
    if not isinstance(raw, dict):
        return settings
    for f in fields(Settings):
        if f.name in raw and _valid(f.name, raw[f.name], getattr(settings, f.name)):
            setattr(settings, f.name, raw[f.name])
    return settings


def update(settings: Settings, name: str, value: object) -> bool:
    """Set one field if `name` is a Settings field and `value` passes _valid; else leave it and return False."""
    if name not in {f.name for f in fields(Settings)}:
        return False
    # Check against the default, as load() does, so a menu write cannot store what load() would reject.
    if not _valid(name, value, getattr(Settings(), name)):
        return False
    setattr(settings, name, value)
    return True


def save(settings: Settings) -> None:
    """Write atomically so a crash mid-write never leaves a truncated file."""
    path = settings_path()
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        logging.getLogger("companion").warning("could not save settings to %s", path)


def rotate_log_if_large(path: Path | None = None) -> None:
    """Move an oversized log aside. Fails quietly when another process holds the file open."""
    path = path or log_path()
    try:
        if path.stat().st_size > LOG_MAX_BYTES:
            os.replace(path, path.with_suffix(".log.1"))
    except OSError:
        pass


def setup_logging() -> logging.Logger:
    rotate_log_if_large()
    logger = logging.getLogger("companion")
    if not logger.handlers:
        handler = logging.FileHandler(log_path(), encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
