"""When she may spend a line, and what she has spent. Pure, no Qt, no network.

Her limit ("limit" in config.json, the menu's Persona > Limit) is one of:
  "normal"     lines_per_hour lines an hour, CHAT_PER_HOUR chat replies an hour, and the
               daily dollar cap daily_usd (past it she goes mood-only until midnight)
  "unlimited"  none of those
  a number N   at most N lines and chat replies a day, counted together until midnight;
               no hourly ceilings and no dollar cap
Past a daily limit she answers with moods only. Every line and reply is one API call.

A line also needs all of:
  - no session waiting on you, unless you just patted her
  - someone at the desk (keyboard or mouse used in the last AWAY_AFTER), unless you just
    patted her: nobody would read it, and what happened meanwhile waits for your return
  - outside quiet hours, unless it is a late-night or pat moment, or a greeting after
    her day starts (05:00), so an early start still gets its morning hello
  - min_gap_s since the last line, except for a pat: you asked, so a pat waits only for
    the daemon's own interval between lines (core.py holds it until then)
  - no chat with you in the last CHAT_HUSH, so a moment never talks over her answer
With "no_gap" on (the menu's "No pause"), min_gap_s and CHAT_HUSH are skipped.

A chat reply (you typed to her) needs only her limit: quiet hours, a waiting session
and the gaps do not apply, since you asked.

Spend is counted from each response's usage at the model's list price, per local day.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .senses import DAY_START_HOUR

# $ per million tokens: (input, output). Cache writes cost 1.25x input, cache reads 0.1x.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}
UNKNOWN_PRICE = (10.00, 50.00)  # an unlisted model is costed high, so the cap trips early
ALWAYS_TALK = {"pat"}
QUIET_HOURS_OK = {"late_night", "pat"}
CHAT_PER_HOUR = 60
CHAT_HUSH = timedelta(minutes=2)
OUT_OF_LINES = ("daily cap", "daily limit")  # reasons she answers with a mood instead


def cost_usd(model: str, usage: dict) -> float:
    inp, out = PRICES.get(model, UNKNOWN_PRICE)
    tokens_in = usage.get("input_tokens", 0) or 0
    written = usage.get("cache_creation_input_tokens", 0) or 0
    read = usage.get("cache_read_input_tokens", 0) or 0
    tokens_out = usage.get("output_tokens", 0) or 0
    return (tokens_in * inp + written * inp * 1.25 + read * inp * 0.1 + tokens_out * out) / 1_000_000


def in_quiet_hours(hour: int, quiet: list[int]) -> bool:
    start, end = quiet
    if start == end:
        return False
    return start <= hour < end if start < end else hour >= start or hour < end


def describe_limit(config: dict) -> str:
    """Her limit and No pause in a few words, for the log."""
    limit = config.get("limit", "normal")
    if limit == "unlimited":
        words = "no limit"
    elif isinstance(limit, int) and not isinstance(limit, bool):
        words = f"at most {limit:,} lines and replies a day"
    else:
        words = f"at most {config['lines_per_hour']} lines an hour and ${config['daily_usd']:.2f} a day"
    return words + (", no pause" if config.get("no_gap") is True else "")


class Budget:
    def __init__(self, config: dict, spend: dict | None = None) -> None:
        self.config = config
        self.spoken: list[datetime] = []
        self.chatted: list[datetime] = []  # chat replies asked for
        self.spend = spend if isinstance(spend, dict) else {}

    def why_not(self, kind: str, now: datetime, waiting: bool, away: bool = False) -> str | None:
        """None if a line for a moment of `kind` may be spent now, else the reason it may not."""
        if (reason := self._over_limit(now)) is not None:
            return reason
        no_gap = self.config.get("no_gap") is True
        if not no_gap and self.chatted and now - self.chatted[-1] < CHAT_HUSH:
            return "talking with you"
        if waiting and kind not in ALWAYS_TALK:
            return "a session needs you"
        if away and kind not in ALWAYS_TALK:
            return "nobody at the desk"
        morning = kind == "greeting" and now.hour >= DAY_START_HOUR
        if in_quiet_hours(now.hour, self.config["quiet_hours"]) and kind not in QUIET_HOURS_OK and not morning:
            return "quiet hours"
        self.spoken = [t for t in self.spoken if now - t < timedelta(hours=1)]
        if self._limit() == "normal" and len(self.spoken) >= self.config["lines_per_hour"]:
            return "hourly limit"
        if not no_gap and kind not in ALWAYS_TALK and self.spoken and (now - self.spoken[-1]).total_seconds() < self.config["min_gap_s"]:
            return "too soon"
        return None

    def take(self, now: datetime) -> None:
        """Claim a line slot; taken before the call, so two moments cannot both pass the gap."""
        self.spoken.append(now)
        self._count(now)

    def why_not_chat(self, now: datetime) -> str | None:
        """None if she may answer something you typed to her now, else the reason she may not."""
        if (reason := self._over_limit(now)) is not None:
            return reason
        self.chatted = [t for t in self.chatted if now - t < timedelta(hours=1)]
        if self._limit() == "normal" and len(self.chatted) >= CHAT_PER_HOUR:
            return "chat limit"
        return None

    def take_chat(self, now: datetime) -> None:
        self.chatted.append(now)
        self._count(now)

    def record(self, model: str, usage: dict, now: datetime) -> float:
        """Add one response's usage to today's spend; the cost of this call."""
        today = self._today(now)
        cost = cost_usd(model, usage)
        today["usd"] = round(today["usd"] + cost, 6)
        today["calls"] += 1
        today["input_tokens"] += (usage.get("input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0)
        today["cached_tokens"] += usage.get("cache_read_input_tokens") or 0
        today["output_tokens"] += usage.get("output_tokens") or 0
        return cost

    def spent_today(self, now: datetime) -> float:
        if self.spend.get("date") != now.date().isoformat():
            return 0.0
        usd = self.spend.get("usd")
        return float(usd) if isinstance(usd, (int, float)) else 0.0

    def lines_today(self, now: datetime) -> int:
        """Lines and chat replies asked for today (sleep's calls are not counted)."""
        if self.spend.get("date") != now.date().isoformat():
            return 0
        lines = self.spend.get("lines")
        return lines if isinstance(lines, int) else 0

    # --- internals -----------------------------------------------------------------------

    def _limit(self) -> str | int:
        limit = self.config.get("limit", "normal")
        if limit == "unlimited" or (isinstance(limit, int) and not isinstance(limit, bool) and limit >= 1):
            return limit
        return "normal"

    def _over_limit(self, now: datetime) -> str | None:
        limit = self._limit()
        if limit == "normal" and self.spent_today(now) >= self.config["daily_usd"]:
            return "daily cap"
        if isinstance(limit, int) and self.lines_today(now) >= limit:
            return "daily limit"
        return None

    def _today(self, now: datetime) -> dict:
        """Today's spend record, started fresh when the day changed."""
        day = now.date().isoformat()
        if self.spend.get("date") != day:
            self.spend = {"date": day, "usd": 0.0, "calls": 0, "lines": 0, "input_tokens": 0, "cached_tokens": 0, "output_tokens": 0}
        for key in ("usd", "calls", "lines", "input_tokens", "cached_tokens", "output_tokens"):
            if not isinstance(self.spend.get(key), (int, float)) or isinstance(self.spend.get(key), bool):
                self.spend[key] = 0
        return self.spend

    def _count(self, now: datetime) -> None:
        self._today(now)["lines"] += 1
