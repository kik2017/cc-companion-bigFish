"""Done and error sounds: the cue rules, the WAV files and the player."""

import math
import struct
import wave
from dataclasses import replace

import pytest

from companion import sounds
from companion.model import DisplayState, State

LIVE = DisplayState(state=State.THINKING, session_id="s1", sessions=1)


def at(state, **fields):
    return replace(LIVE, state=state, **fields)


# --- cue_for ------------------------------------------------------------------------------


def test_nothing_plays_on_the_first_display():
    assert sounds.cue_for(None, at(State.DONE)) is None


def test_done_plays_when_the_turn_finishes():
    assert sounds.cue_for(at(State.RUNNING), at(State.DONE)) == "done"


def test_done_plays_once_while_done_holds():
    assert sounds.cue_for(at(State.DONE), at(State.DONE, tasks_created=1)) is None


def test_error_plays_when_a_turn_stops_on_an_error():
    assert sounds.cue_for(at(State.THINKING), at(State.ERROR, error_reason="rate_limit")) == "error"


def test_a_tool_failure_flash_stays_silent():
    assert sounds.cue_for(at(State.RUNNING), at(State.ERROR, error_reason=None)) is None


def test_error_plays_when_a_turn_stops_during_a_tool_failure_flash():
    assert sounds.cue_for(at(State.ERROR), at(State.ERROR, error_reason="rate_limit")) == "error"


def test_error_plays_once_while_the_turn_error_holds():
    held = at(State.ERROR, error_reason="rate_limit")
    assert sounds.cue_for(held, replace(held, sessions=2)) is None


@pytest.mark.parametrize("state", [s for s in State if s not in (State.DONE, State.ERROR)])
def test_other_states_are_silent(state):
    assert sounds.cue_for(at(State.DONE), at(state)) is None


# --- the WAV files ------------------------------------------------------------------------


@pytest.mark.parametrize("cue", sounds.CUES)
def test_cue_is_a_short_audible_mono_wav(cue):
    with wave.open(str(sounds.SOUNDS_DIR / f"{cue}.wav")) as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 44_100)
        frames = wav.readframes(wav.getnframes())
        seconds = wav.getnframes() / wav.getframerate()
    samples = struct.unpack(f"<{len(frames) // 2}h", frames)
    assert 0.2 <= seconds <= 0.6
    assert 0.2 * 32767 <= max(abs(s) for s in samples) <= 0.5 * 32767, "audible, but not at full scale"
    assert abs(samples[0]) < 0.01 * 32767 and abs(samples[-1]) < 0.01 * 32767, "starts and ends without a click"


def test_done_and_error_sound_different():
    rms = {}
    for cue in sounds.CUES:
        with wave.open(str(sounds.SOUNDS_DIR / f"{cue}.wav")) as wav:
            frames = wav.readframes(wav.getnframes())
        samples = struct.unpack(f"<{len(frames) // 2}h", frames)
        rms[cue] = (len(samples), round(math.sqrt(sum(s * s for s in samples) / len(samples))))
    assert rms["done"] != rms["error"]


# --- the player ---------------------------------------------------------------------------


class FakeEffect:
    created = []

    def __init__(self):
        self.source = self.volume = None
        self.plays = 0
        FakeEffect.created.append(self)

    def setSource(self, url):
        self.source = url.toLocalFile()

    def setVolume(self, volume):
        self.volume = volume

    def play(self):
        self.plays += 1


@pytest.fixture
def fake_effects(monkeypatch):
    FakeEffect.created = []
    monkeypatch.setattr(sounds, "QSoundEffect", FakeEffect)
    return FakeEffect.created


def test_player_loads_each_cue_once_and_plays_it(fake_effects):
    player = sounds.SoundPlayer()
    player.play("done")
    player.play("done")
    player.play("error")
    assert len(fake_effects) == 2
    assert fake_effects[0].source.endswith("done.wav") and fake_effects[0].plays == 2
    assert fake_effects[1].source.endswith("error.wav") and fake_effects[1].plays == 1
    assert all(effect.volume == sounds.VOLUME for effect in fake_effects)


def test_preload_loads_every_cue_without_playing(fake_effects):
    sounds.SoundPlayer().preload()
    assert len(fake_effects) == len(sounds.CUES)
    assert all(effect.plays == 0 for effect in fake_effects)


def test_a_missing_file_is_skipped(fake_effects, tmp_path):
    sounds.SoundPlayer(directory=tmp_path).play("done")
    assert fake_effects == []
