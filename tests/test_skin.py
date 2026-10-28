import json
import shutil
from pathlib import Path

import pytest

from companion import settings as prefs
from companion import skin as skins
from companion.model import MOODS, State

PLACEHOLDER = Path(__file__).parent / "fixtures" / "placeholder-skin"


@pytest.fixture
def pack(tmp_path):
    """A copy of the placeholder pack whose manifest each test can break."""
    root = tmp_path / "pack"
    shutil.copytree(PLACEHOLDER, root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))

    def write(**changes):
        data = {**manifest, **changes}
        (root / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
        return root

    write.manifest = manifest
    write.root = root
    return write


def test_placeholder_pack_loads_and_covers_every_state():
    skin = skins.load_skin(PLACEHOLDER)
    assert set(skin.states) == set(State)
    assert set(skin.reactions) == set(skins.REACTIONS)
    assert skin.idle
    assert all(clip.frames for clip in skin.clips.values())


def test_zones_resolve_head_tail_then_body():
    skin = skins.load_skin(PLACEHOLDER)
    assert skin.zone_at(0.5, 0.1) == "head"
    assert skin.zone_at(0.9, 0.8) == "tail"
    assert skin.zone_at(0.4, 0.8) == "body"


def test_frames_play_in_name_order(pack):
    root = pack()
    idle = json.loads((root / "manifest.json").read_text(encoding="utf-8"))["states"]["idle"]
    frames = skins.load_skin(root).state_clip(State.IDLE).frames
    assert [p.name for p in frames] == sorted(p.name for p in (root / idle).iterdir())


@pytest.mark.parametrize(
    "change, message",
    [
        ({"format": 2}, "format"),
        ({"frameSize": [0, 10]}, "frameSize"),
        ({"states": {"idle": "idle"}}, "missing"),
        ({"reactions": {"head": "pat"}}, "missing"),
        ({"idle": ["nope"]}, "unknown clip"),
        ({"zones": {"ear": [0, 0, 1, 1]}}, "zone"),
        ({"zones": {"head": [0.5, 0, 0.5, 1]}}, "no area"),
        ({"license": "LICENSE.txt"}, "license"),
    ],
)
def test_bad_manifest_is_rejected_with_a_reason(pack, change, message):
    with pytest.raises(skins.SkinError, match=message):
        skins.load_skin(pack(**change))


def test_clip_folder_must_stay_inside_the_pack(pack):
    clips = {**pack.manifest["clips"], "idle": {"dir": "../outside", "frameMs": 100, "loop": True}}
    (pack.root.parent / "outside").mkdir()
    with pytest.raises(skins.SkinError, match="not found"):
        skins.load_skin(pack(clips=clips))


def test_reaction_and_idle_clips_must_play_once(pack):
    clips = {**pack.manifest["clips"], "pat": {**pack.manifest["clips"]["pat"], "loop": True}}
    with pytest.raises(skins.SkinError, match="must not loop"):
        skins.load_skin(pack(clips=clips))


def test_drag_clip_must_loop(pack):
    clips = {**pack.manifest["clips"], "drag": {**pack.manifest["clips"]["drag"], "loop": False}}
    with pytest.raises(skins.SkinError, match="must loop"):
        skins.load_skin(pack(clips=clips))


def test_frame_time_is_bounded(pack):
    clips = {**pack.manifest["clips"], "idle": {**pack.manifest["clips"]["idle"], "frameMs": 0}}
    with pytest.raises(skins.SkinError, match="frameMs"):
        skins.load_skin(pack(clips=clips))


def test_license_file_is_kept_when_named(pack):
    (pack.root / "LICENSE.txt").write_text("MIT", encoding="utf-8")
    assert skins.load_skin(pack(license="LICENSE.txt")).license.name == "LICENSE.txt"


def test_resolve_loads_a_pack_from_the_skins_folder(pack):
    target = prefs.skins_dir() / "mine"
    shutil.copytree(pack(name="mine"), target)
    assert skins.resolve("mine").name == "mine"


def test_resolve_falls_back_to_the_bundled_character(pack):
    target = prefs.skins_dir() / "broken"
    shutil.copytree(pack(format=99), target)
    bundled = skins.default_dir().resolve()
    assert skins.resolve("broken").root == bundled
    assert skins.resolve("missing").root == bundled
    assert skins.resolve("").root == bundled


def test_the_bundled_character_loads_with_its_license_and_source():
    skin = skins.load_skin(skins.default_dir())
    assert set(skin.states) == set(State)
    assert set(skin.reactions) == set(skins.REACTIONS)
    assert skin.frame_size == (412, 344)
    assert sum(len(clip.frames) for clip in skin.clips.values()) == 3444
    license_text = skin.license.read_text(encoding="utf-8")
    # Must ship with the frames: the art terms and every credit, and never an MIT claim for the art.
    for part in ("CC BY-NC-SA 4.0", "NOT covered by the MIT license", "https://github.com/PC2005-cloud/dsh-pet", "ZipZipPipe", "上善无形"):
        assert part in license_text, part
    assert "github.com/PC2005-cloud/dsh-pet" in (skin.root / "SOURCE.md").read_text(encoding="utf-8")
    assert skin.zone_at(0.5, 0.2) == "head" and skin.zone_at(0.9, 0.8) == "tail" and skin.zone_at(0.4, 0.8) == "body"

# --- moods ------------------------------------------------------------------------------


def test_a_mood_without_a_manifest_entry_falls_back_to_a_reaction(pack):
    skin = skins.load_skin(pack())
    assert skin.mood_clip("happy").name == skin.reactions["tail"]
    assert skin.mood_clip("proud").name == skin.reactions["head"]
    assert skin.mood_clip("worried").name == skin.reactions["body"]
    assert skin.mood_clip("calm") is None
    assert skin.mood_clip("no-such-mood") is None


def test_manifest_moods_override_the_fallback(pack):
    skin = skins.load_skin(pack(moods={"happy": "idle_hop", "sleepy": "idle_blink"}))
    assert skin.mood_clip("happy").name == "idle_hop"
    assert skin.mood_clip("sleepy").name == "idle_blink"
    assert skin.mood_clip("proud").name == skin.reactions["head"]


@pytest.mark.parametrize(
    "moods, message",
    [
        ({"angry": "pat"}, "unknown mood"),
        ({"happy": "nope"}, "unknown clip"),
        ({"happy": "idle"}, "must not loop"),
        (["happy"], "moods must be an object"),
    ],
)
def test_bad_moods_are_rejected(pack, moods, message):
    with pytest.raises(skins.SkinError, match=message):
        skins.load_skin(pack(moods=moods))


def test_the_bundled_character_maps_every_mood_but_calm():
    skin = skins.load_skin(skins.default_dir())
    assert {m for m in MOODS if skin.mood_clip(m) is not None} == set(MOODS) - {"calm"}
