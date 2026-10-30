"""Skin packs: a manifest plus one folder of frames per clip. No Qt imports.

    <pack>/manifest.json
    <pack>/<clip folder>/0001.png ...   (PNG or WebP, played in name order)
    <pack>/LICENSE.txt                  (the art's license, when the manifest names one)

manifest.json:

    format      1
    name        display name
    frameSize   [width, height] of every frame
    license     optional file name inside the pack
    clips       {clip: {"dir": folder, "frameMs": int, "loop": bool}}; two clips may share
                a folder (a looping state clip and a once-through idle one, say)
    states      {display state: clip or [clip, ...]}, one entry for every State; with a
                list, one is picked when the state starts and, for a looping state, may
                change at the end of each pass
    reactions   {"head", "body", "tail", "drag", "land": clip or [clip, ...]}, one picked
                at random each time
    idle        [clip, ...] played at random while idle; may be empty
    moods       optional {mood: clip} for the persona's moods (model.MOODS); a mood left
                out falls back to a reaction (happy: tail, proud: head, worried: body)
                or to no clip
    zones       {"head" | "tail": [left, top, right, bottom]} as fractions of the frame,
                checked in order; anywhere else on the character is "body"

Optional, for the persona and the clock:

    talk        [clip, ...] one plays while she says a line
    cues        {"greet" | "sleep": clip or [clip, ...]} for her greeting and her sleep,
                when the persona names one in POST /say
    wake        {"during": [clip, ...], "clip": clip}: a click while she plays one of
                `during` (a nap) wakes her with `clip` instead of the zone's reaction
    timed       [{"clip": clip, "from": "HH:MM", "to": "HH:MM"}, ...] idle clips for a time
                of day (breakfast at 07:00-09:30); each plays once in its window a day, at
                her first idle moment in it; a window may run past midnight
    usage       [clip, ...] from plenty to none, for the 5-hour quota: the last is for 100%
                used and the others split 0-100% evenly; when the quota climbs into a new
                tier its clip plays once, at her next idle moment
    dates       [{"clip": clip, "on": ["MM-DD" | "YYYY-MM-DD", ...]}, ...] festival clips: each
                plays once on each of its days, at her first idle moment; MM-DD is that day
                every year, YYYY-MM-DD one day only (for festivals that move, like the lunar ones)

The repo ships one pack, the bundled character in companion/assets/character (CC BY-NC-SA 4.0
art, non-commercial, with its LICENSE.txt and SOURCE.md). Other packs live under the app data skins/ folder
and are never committed; the tests use the placeholder pack in tests/fixtures.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from . import settings as prefs
from .model import MOODS, State

FORMAT = 1
FRAME_SUFFIXES = (".png", ".webp")
REACTIONS = ("head", "body", "tail", "drag", "land")
ZONES = ("head", "tail")
CUES = ("greet", "sleep")
# A mood the manifest leaves out plays this reaction, if any.
MOOD_REACTIONS = {"happy": "tail", "proud": "head", "worried": "body"}
FRAME_MS_RANGE = (10, 1000)

log = logging.getLogger("companion")


class SkinError(ValueError):
    pass


@dataclass(frozen=True)
class Clip:
    name: str
    frames: tuple[Path, ...]
    frame_ms: int
    loop: bool

    @property
    def duration_s(self) -> float:
        return len(self.frames) * self.frame_ms / 1000


@dataclass(frozen=True)
class Zone:
    name: str
    left: float
    top: float
    right: float
    bottom: float

    def contains(self, fx: float, fy: float) -> bool:
        return self.left <= fx < self.right and self.top <= fy < self.bottom


@dataclass(frozen=True)
class Timed:
    clip: str
    start: int  # minutes after midnight
    end: int  # minutes after midnight; before start, the window runs past midnight

    def contains(self, minute: int) -> bool:
        return self.start <= minute < self.end if self.start < self.end else minute >= self.start or minute < self.end


@dataclass(frozen=True)
class DateClip:
    clip: str
    every_year: frozenset[str] = frozenset()  # MM-DD
    once: frozenset[str] = frozenset()  # YYYY-MM-DD

    def on(self, day: date) -> bool:
        return day.strftime("%m-%d") in self.every_year or day.isoformat() in self.once


@dataclass(frozen=True)
class Skin:
    name: str
    root: Path
    frame_size: tuple[int, int]
    clips: dict[str, Clip]
    states: dict[State, tuple[str, ...]]
    reactions: dict[str, tuple[str, ...]]
    idle: tuple[str, ...]
    zones: tuple[Zone, ...]
    license: Path | None = None
    moods: dict[str, str] = field(default_factory=dict)
    talk: tuple[str, ...] = ()
    cues: dict[str, tuple[str, ...]] = field(default_factory=dict)
    wake_during: frozenset[str] = frozenset()
    wake: str | None = None
    timed: tuple[Timed, ...] = ()
    usage: tuple[str, ...] = ()
    dates: tuple[DateClip, ...] = ()

    def __post_init__(self) -> None:
        # A state or reaction may be given as one clip name; it is kept as a tuple of one.
        for name in ("states", "reactions"):
            mapping = getattr(self, name)
            object.__setattr__(self, name, {k: (v,) if isinstance(v, str) else tuple(v) for k, v in mapping.items()})

    def state_clip(self, state: State) -> Clip:
        """The state's first clip: the one reduced motion holds and the tray draws from."""
        return self.clips[self.states[state][0]]

    def state_clips(self, state: State) -> tuple[Clip, ...]:
        return tuple(self.clips[name] for name in self.states[state])

    def reaction_clip(self, reaction: str) -> Clip:
        return self.clips[self.reactions[reaction][0]]

    def reaction_clips(self, reaction: str) -> tuple[Clip, ...]:
        return tuple(self.clips[name] for name in self.reactions[reaction])

    def talk_clips(self) -> tuple[Clip, ...]:
        return tuple(self.clips[name] for name in self.talk)

    def cue_clips(self, cue: str) -> tuple[Clip, ...]:
        return tuple(self.clips[name] for name in self.cues.get(cue, ()))

    def usage_tier(self, pct: float) -> int | None:
        """Which usage clip a 5-hour quota of `pct` percent used belongs to, or None without any."""
        count = len(self.usage)
        if count == 0:
            return None
        if pct >= 100 or count == 1:
            return count - 1
        return min(count - 2, max(0, int(pct * (count - 1) // 100)))

    def mood_clip(self, mood: str) -> Clip | None:
        """The clip for a persona mood: the manifest's, else its fallback reaction, else None."""
        if mood in self.moods:
            return self.clips[self.moods[mood]]
        reaction = MOOD_REACTIONS.get(mood)
        return None if reaction is None else self.reaction_clip(reaction)

    def zone_at(self, fx: float, fy: float) -> str:
        """Zone under a point given as fractions of the frame."""
        return next((zone.name for zone in self.zones if zone.contains(fx, fy)), "body")


def default_dir() -> Path:
    """The bundled character, used when settings name no pack."""
    return Path(__file__).parent / "assets" / "character"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SkinError(message)


def _clip(root: Path, name: str, spec: object) -> Clip:
    _require(isinstance(spec, dict), f"clip {name!r} is not an object")
    folder, frame_ms, loop = spec.get("dir"), spec.get("frameMs"), spec.get("loop")
    _require(isinstance(folder, str) and folder and "\\" not in folder, f"clip {name!r} needs a folder name in 'dir'")
    directory = (root / folder).resolve()
    _require(directory.is_relative_to(root) and directory.is_dir(), f"clip {name!r}: folder {folder!r} not found in the pack")
    _require(
        isinstance(frame_ms, int) and not isinstance(frame_ms, bool) and FRAME_MS_RANGE[0] <= frame_ms <= FRAME_MS_RANGE[1],
        f"clip {name!r}: frameMs must be an integer from {FRAME_MS_RANGE[0]} to {FRAME_MS_RANGE[1]}",
    )
    _require(isinstance(loop, bool), f"clip {name!r}: loop must be true or false")
    frames = tuple(sorted(p for p in directory.iterdir() if p.suffix.lower() in FRAME_SUFFIXES))
    _require(bool(frames), f"clip {name!r}: no PNG or WebP frames in {folder!r}")
    return Clip(name, frames, frame_ms, loop)


def _zone(name: str, box: object) -> Zone:
    _require(name in ZONES, f"zone {name!r} is not one of {', '.join(ZONES)}")
    _require(
        isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in box),
        f"zone {name!r} must be [left, top, right, bottom] fractions from 0 to 1",
    )
    left, top, right, bottom = (float(v) for v in box)
    _require(left < right and top < bottom, f"zone {name!r} has no area")
    return Zone(name, left, top, right, bottom)


def _time(value: object, where: str) -> int:
    ok = isinstance(value, str) and len(value) == 5 and value[2] == ":" and value[:2].isdigit() and value[3:].isdigit()
    _require(ok and int(value[:2]) < 24 and int(value[3:]) < 60, f"{where} must be a time as HH:MM")
    return int(value[:2]) * 60 + int(value[3:])


def _day(value: object, where: str) -> tuple[str, bool]:
    """A day as (text, every year): "MM-DD" for every year, "YYYY-MM-DD" for one day."""
    if isinstance(value, str) and len(value) in (5, 10):
        every_year = len(value) == 5
        try:
            datetime.strptime(f"2000-{value}" if every_year else value, "%Y-%m-%d")  # 2000 was a leap year: 02-29 counts
            return value, every_year
        except ValueError:
            pass
    raise SkinError(f"{where} must be a day as MM-DD or YYYY-MM-DD")


def load_skin(root: Path) -> Skin:
    root = Path(root).resolve()
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SkinError(f"cannot read {root / 'manifest.json'}: {error}") from error
    _require(isinstance(manifest, dict), "manifest.json is not an object")
    _require(manifest.get("format") == FORMAT, f"manifest format must be {FORMAT}")

    size = manifest.get("frameSize")
    _require(
        isinstance(size, list) and len(size) == 2 and all(isinstance(v, int) and v > 0 for v in size),
        "frameSize must be [width, height] in pixels",
    )
    raw_clips = manifest.get("clips")
    _require(isinstance(raw_clips, dict) and raw_clips, "clips must be a non-empty object")
    clips = {name: _clip(root, name, spec) for name, spec in raw_clips.items()}

    def clip_ref(value: object, where: str) -> str:
        _require(isinstance(value, str) and value in clips, f"{where} names unknown clip {value!r}")
        return value

    def clip_refs(value: object, where: str) -> tuple[str, ...]:
        """One clip name, or a non-empty list of them."""
        if isinstance(value, list):
            _require(bool(value), f"{where} is an empty list")
            return tuple(clip_ref(v, where) for v in value)
        return (clip_ref(value, where),)

    raw_states = manifest.get("states")
    _require(isinstance(raw_states, dict), "states must be an object")
    missing = [s.value for s in State if s.value not in raw_states]
    _require(not missing, f"states is missing {', '.join(missing)}")
    states = {s: clip_refs(raw_states[s.value], f"states.{s.value}") for s in State}

    raw_reactions = manifest.get("reactions")
    _require(isinstance(raw_reactions, dict), "reactions must be an object")
    missing = [r for r in REACTIONS if r not in raw_reactions]
    _require(not missing, f"reactions is missing {', '.join(missing)}")
    reactions = {r: clip_refs(raw_reactions[r], f"reactions.{r}") for r in REACTIONS}

    raw_idle = manifest.get("idle", [])
    _require(isinstance(raw_idle, list), "idle must be a list")
    idle = tuple(clip_ref(name, "idle") for name in raw_idle)

    raw_moods = manifest.get("moods", {})
    _require(isinstance(raw_moods, dict), "moods must be an object")
    unknown = [m for m in raw_moods if m not in MOODS]
    _require(not unknown, f"moods has unknown mood {', '.join(map(repr, unknown))}")
    moods = {m: clip_ref(raw_moods[m], f"moods.{m}") for m in raw_moods}

    raw_talk = manifest.get("talk", [])
    _require(isinstance(raw_talk, list), "talk must be a list")
    talk = tuple(clip_ref(name, "talk") for name in raw_talk)

    raw_cues = manifest.get("cues", {})
    _require(isinstance(raw_cues, dict), "cues must be an object")
    unknown = [c for c in raw_cues if c not in CUES]
    _require(not unknown, f"cues has unknown cue {', '.join(map(repr, unknown))}; known: {', '.join(CUES)}")
    cues = {c: clip_refs(raw_cues[c], f"cues.{c}") for c in raw_cues}

    raw_wake = manifest.get("wake")
    wake_during: frozenset[str] = frozenset()
    wake = None
    if raw_wake is not None:
        _require(isinstance(raw_wake, dict) and isinstance(raw_wake.get("during"), list), 'wake must be {"during": [clip, ...], "clip": clip}')
        wake_during = frozenset(clip_ref(name, "wake.during") for name in raw_wake["during"])
        wake = clip_ref(raw_wake.get("clip"), "wake.clip")

    raw_timed = manifest.get("timed", [])
    _require(isinstance(raw_timed, list), "timed must be a list")
    timed = []
    for i, entry in enumerate(raw_timed):
        _require(isinstance(entry, dict), f"timed[{i}] must be an object")
        start, end = _time(entry.get("from"), f"timed[{i}].from"), _time(entry.get("to"), f"timed[{i}].to")
        _require(start != end, f"timed[{i}] has no length")
        timed.append(Timed(clip_ref(entry.get("clip"), f"timed[{i}].clip"), start, end))

    raw_usage = manifest.get("usage", [])
    _require(isinstance(raw_usage, list), "usage must be a list")
    usage = tuple(clip_ref(name, "usage") for name in raw_usage)

    raw_dates = manifest.get("dates", [])
    _require(isinstance(raw_dates, list), "dates must be a list")
    dates = []
    for i, entry in enumerate(raw_dates):
        _require(isinstance(entry, dict) and isinstance(entry.get("on"), list) and entry["on"], f"dates[{i}] must be {{\"clip\": clip, \"on\": [day, ...]}}")
        days = [_day(value, f"dates[{i}].on") for value in entry["on"]]
        dates.append(DateClip(
            clip_ref(entry.get("clip"), f"dates[{i}].clip"),
            frozenset(d for d, every in days if every), frozenset(d for d, every in days if not every),
        ))  # fmt: skip

    # A looping reaction, idle, mood or persona clip would never hand back to the state clip.
    for where, name in [
        *((f"reactions.{r}", c) for r in REACTIONS if r != "drag" for c in reactions[r]),
        *(("idle", c) for c in idle),
        *((f"moods.{m}", c) for m, c in moods.items()),
        *(("talk", c) for c in talk),
        *((f"cues.{k}", c) for k, names in cues.items() for c in names),
        *(("wake.clip", c) for c in ([wake] if wake else [])),
        *(("timed", entry.clip) for entry in timed),
        *(("usage", c) for c in usage),
        *(("dates", entry.clip) for entry in dates),
    ]:
        _require(not clips[name].loop, f"{where} clip {name!r} must not loop")
    for name in reactions["drag"]:
        _require(clips[name].loop, f"reactions.drag clip {name!r} must loop")

    raw_zones = manifest.get("zones", {})
    _require(isinstance(raw_zones, dict), "zones must be an object")
    zones = tuple(_zone(name, box) for name, box in raw_zones.items())

    license_path = None
    if manifest.get("license") is not None:
        name = manifest["license"]
        _require(isinstance(name, str) and (root / name).is_file(), f"license file {name!r} not found in the pack")
        license_path = root / name

    name = manifest.get("name") if isinstance(manifest.get("name"), str) else root.name
    return Skin(
        name, root, (size[0], size[1]), clips, states, reactions, idle, zones, license_path, moods,
        talk, cues, wake_during, wake, tuple(timed), usage, tuple(dates),
    )  # fmt: skip


def resolve(name: str) -> Skin:
    """The named pack from the app data skins folder, or the bundled character if it is unset or broken."""
    if name:
        try:
            return load_skin(prefs.skins_dir() / name)
        except SkinError as error:
            log.warning("skin %s not usable (%s); using the bundled character", name, error)
    return load_skin(default_dir())
