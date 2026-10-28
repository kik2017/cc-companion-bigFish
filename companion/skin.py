"""Skin packs: a manifest plus one folder of frames per clip. No Qt imports.

    <pack>/manifest.json
    <pack>/<clip folder>/0001.png ...   (PNG or WebP, played in name order)
    <pack>/LICENSE.txt                  (the art's license, when the manifest names one)

manifest.json:

    format      1
    name        display name
    frameSize   [width, height] of every frame
    license     optional file name inside the pack
    clips       {clip: {"dir": folder, "frameMs": int, "loop": bool}}
    states      {display state: clip}, one entry for every State
    reactions   {"head", "body", "tail", "drag", "land": clip}
    idle        [clip, ...] played at random while idle; may be empty
    moods       optional {mood: clip} for the persona's moods (model.MOODS); a mood left
                out falls back to a reaction (happy: tail, proud: head, worried: body)
                or to no clip
    zones       {"head" | "tail": [left, top, right, bottom]} as fractions of the frame,
                checked in order; anywhere else on the character is "body"

The repo ships one pack, the bundled character in companion/assets/character (CC BY-NC-SA 4.0
art, non-commercial, with its LICENSE.txt and SOURCE.md). Other packs live under the app data skins/ folder
and are never committed; the tests use the placeholder pack in tests/fixtures.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from . import settings as prefs
from .model import MOODS, State

FORMAT = 1
FRAME_SUFFIXES = (".png", ".webp")
REACTIONS = ("head", "body", "tail", "drag", "land")
ZONES = ("head", "tail")
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
class Skin:
    name: str
    root: Path
    frame_size: tuple[int, int]
    clips: dict[str, Clip]
    states: dict[State, str]
    reactions: dict[str, str]
    idle: tuple[str, ...]
    zones: tuple[Zone, ...]
    license: Path | None = None
    moods: dict[str, str] = field(default_factory=dict)

    def state_clip(self, state: State) -> Clip:
        return self.clips[self.states[state]]

    def reaction_clip(self, reaction: str) -> Clip:
        return self.clips[self.reactions[reaction]]

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

    raw_states = manifest.get("states")
    _require(isinstance(raw_states, dict), "states must be an object")
    missing = [s.value for s in State if s.value not in raw_states]
    _require(not missing, f"states is missing {', '.join(missing)}")
    states = {s: clip_ref(raw_states[s.value], f"states.{s.value}") for s in State}

    raw_reactions = manifest.get("reactions")
    _require(isinstance(raw_reactions, dict), "reactions must be an object")
    missing = [r for r in REACTIONS if r not in raw_reactions]
    _require(not missing, f"reactions is missing {', '.join(missing)}")
    reactions = {r: clip_ref(raw_reactions[r], f"reactions.{r}") for r in REACTIONS}

    raw_idle = manifest.get("idle", [])
    _require(isinstance(raw_idle, list), "idle must be a list")
    idle = tuple(clip_ref(name, "idle") for name in raw_idle)

    raw_moods = manifest.get("moods", {})
    _require(isinstance(raw_moods, dict), "moods must be an object")
    unknown = [m for m in raw_moods if m not in MOODS]
    _require(not unknown, f"moods has unknown mood {', '.join(map(repr, unknown))}")
    moods = {m: clip_ref(raw_moods[m], f"moods.{m}") for m in raw_moods}

    # A looping reaction, idle or mood clip would never hand back to the state clip.
    for where, name in [
        *((f"reactions.{r}", reactions[r]) for r in REACTIONS if r != "drag"),
        *(("idle", c) for c in idle),
        *((f"moods.{m}", c) for m, c in moods.items()),
    ]:
        _require(not clips[name].loop, f"{where} clip {name!r} must not loop")
    _require(clips[reactions["drag"]].loop, f"reactions.drag clip {reactions['drag']!r} must loop")

    raw_zones = manifest.get("zones", {})
    _require(isinstance(raw_zones, dict), "zones must be an object")
    zones = tuple(_zone(name, box) for name, box in raw_zones.items())

    license_path = None
    if manifest.get("license") is not None:
        name = manifest["license"]
        _require(isinstance(name, str) and (root / name).is_file(), f"license file {name!r} not found in the pack")
        license_path = root / name

    name = manifest.get("name") if isinstance(manifest.get("name"), str) else root.name
    return Skin(name, root, (size[0], size[1]), clips, states, reactions, idle, zones, license_path, moods)


def resolve(name: str) -> Skin:
    """The named pack from the app data skins folder, or the bundled character if it is unset or broken."""
    if name:
        try:
            return load_skin(prefs.skins_dir() / name)
        except SkinError as error:
            log.warning("skin %s not usable (%s); using the bundled character", name, error)
    return load_skin(default_dir())
