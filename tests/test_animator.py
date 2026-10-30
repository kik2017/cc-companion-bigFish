import random
from datetime import datetime
from pathlib import Path

import pytest

from companion.animator import CLICK_COOLDOWN_S, REST_WINDOWS_S, Animator
from companion.model import State
from companion.skin import Clip, DateClip, Skin, Timed, Zone

STEP = 0.1  # every test clip runs 100 ms per frame


def clip(name, frames=4, loop=True):
    return Clip(name, tuple(Path(f"{name}/{i:04d}.png") for i in range(frames)), int(STEP * 1000), loop)


def make_skin(idle=("blink", "hop")):
    clips = [
        clip("idle"), clip("think"), clip("work"), clip("wait"),
        clip("done", loop=False), clip("error", loop=False),
        clip("pat", loop=False), clip("poke", loop=False), clip("tail", loop=False),
        clip("drag"), clip("land", frames=2, loop=False),
        clip("blink", loop=False), clip("hop", loop=False), clip("yawn", loop=False),
    ]  # fmt: skip
    states = {
        State.IDLE: "idle", State.THINKING: "think", State.COMPACTING: "think", State.WAITING: "wait",
        State.SEARCHING: "work", State.EDITING: "work", State.RUNNING: "work", State.DELEGATING: "work",
        State.DONE: "done", State.ERROR: "error",
    }  # fmt: skip
    reactions = {"head": "pat", "body": "poke", "tail": "tail", "drag": "drag", "land": "land"}
    zones = (Zone("head", 0, 0, 1, 0.45), Zone("tail", 0.72, 0.45, 1, 1))
    return Skin("test", Path("."), (100, 100), {c.name: c for c in clips}, states, reactions, idle, zones)


def make(now=0.0, **kwargs):
    return Animator(make_skin(kwargs.pop("idle", ("blink", "hop"))), now, rng=random.Random(7), **kwargs)


def shown(anim, now):
    frame = anim.frame(now)
    return frame.clip, frame.index


# --- state clips ------------------------------------------------------------------------


def test_starts_on_the_idle_clip_and_loops():
    anim = make()
    assert shown(anim, 0.0) == ("idle", 0)
    assert shown(anim, 0.25) == ("idle", 2)
    assert shown(anim, 0.45) == ("idle", 0)


def test_state_change_switches_clip_at_once_from_frame_zero():
    anim = make()
    anim.set_state(State.EDITING, 0.33)
    assert shown(anim, 0.33) == ("work", 0)


def test_states_sharing_a_looping_clip_carry_on_without_restart():
    anim = make()
    anim.set_state(State.EDITING, 0.0)
    anim.set_state(State.RUNNING, 0.25)
    assert shown(anim, 0.25) == ("work", 2)


def test_repeated_state_does_not_restart():
    anim = make()
    anim.set_state(State.THINKING, 0.0)
    anim.set_state(State.THINKING, 0.25)
    assert shown(anim, 0.25) == ("think", 2)


def test_non_looping_state_clip_holds_its_last_frame():
    anim = make()
    anim.set_state(State.DONE, 0.0)
    assert shown(anim, 5.0) == ("done", 3)
    assert anim.next_change(5.0) is None


# --- click reactions --------------------------------------------------------------------


def test_pat_during_editing_plays_through_and_returns_to_editing():
    anim = make()
    anim.set_state(State.EDITING, 0.0)
    assert anim.click("head", 1.0)
    assert [shown(anim, 1.0 + i * STEP)[0] for i in range(4)] == ["pat"] * 4
    assert shown(anim, 1.0 + 4 * STEP + 0.001) == ("work", 0)


def test_each_zone_has_its_reaction():
    for zone, expected in (("head", "pat"), ("body", "poke"), ("tail", "tail")):
        anim = make()
        assert anim.click(zone, 1.0)
        assert shown(anim, 1.0)[0] == expected


def test_state_change_interrupts_a_click_reaction():
    anim = make()
    anim.click("body", 1.0)
    anim.set_state(State.RUNNING, 1.1)
    assert shown(anim, 1.1) == ("work", 0)


def test_click_ignored_while_a_reaction_plays_and_during_cooldown():
    anim = make()
    assert anim.click("body", 1.0)
    assert not anim.click("head", 1.2), "a reaction is playing"
    assert not anim.click("head", 1.0 + CLICK_COOLDOWN_S - 0.01), "cooldown"
    assert anim.click("head", 1.0 + CLICK_COOLDOWN_S)


def test_cooldown_survives_a_state_interrupt():
    anim = make()
    anim.click("body", 1.0)
    anim.set_state(State.THINKING, 1.1)
    assert not anim.click("body", 1.5)


def test_click_ignored_while_done_plays_then_allowed_once_it_holds():
    anim = make()
    anim.set_state(State.DONE, 0.0)
    assert not anim.click("head", 0.2)
    assert anim.click("head", 0.5)


def test_double_click_turns_its_first_click_into_a_head_pat():
    anim = make()
    assert anim.click("body", 1.0)
    assert anim.double_click(1.2)
    assert shown(anim, 1.2) == ("pat", 0)


def test_double_click_alone_is_a_head_pat():
    anim = make()
    assert anim.double_click(1.0)
    assert shown(anim, 1.0)[0] == "pat"


# --- drag -------------------------------------------------------------------------------


def test_drag_loops_hover_and_holds_state_changes_until_it_lands():
    anim = make()
    anim.drag_start(1.0)
    assert shown(anim, 1.0)[0] == "drag"
    anim.set_state(State.EDITING, 1.2)
    assert shown(anim, 3.05) == ("drag", 0), "hover loops while held; state waits"
    anim.drag_end(3.1)
    assert shown(anim, 3.1) == ("land", 0)
    assert shown(anim, 3.25) == ("land", 1)
    assert shown(anim, 3.31) == ("work", 0), "after landing, the latest state"


def test_drag_interrupts_a_click_reaction():
    anim = make()
    anim.click("body", 1.0)
    anim.drag_start(1.1)
    assert shown(anim, 1.1)[0] == "drag"


# --- idle clips -------------------------------------------------------------------------


def test_idle_clip_after_rest_window_then_back_to_idle():
    anim = make()
    low, high = REST_WINDOWS_S["normal"]
    due = anim.idle_due
    assert low <= due <= high
    assert shown(anim, due - 0.01)[0] == "idle"
    first = shown(anim, due)[0]
    assert first in ("blink", "hop")
    assert shown(anim, due + 0.41)[0] == "idle"
    assert low <= anim.idle_due - (due + 0.4) <= high, "rest restarts when the idle clip ends"


def test_idle_clips_never_repeat_back_to_back():
    anim = make()
    picks = []
    now = 0.0
    for _ in range(12):
        now = anim.idle_due
        picks.append(shown(anim, now)[0])
        now += 0.5
        anim.frame(now)
    assert all(a != b for a, b in zip(picks, picks[1:]))


def test_state_change_during_idle_clip_switches_at_once():
    anim = make()
    due = anim.idle_due
    shown(anim, due)
    anim.set_state(State.THINKING, due + 0.05)
    assert shown(anim, due + 0.05) == ("think", 0)
    assert anim.idle_due is None


def test_no_idle_clips_outside_idle():
    anim = make()
    anim.set_state(State.WAITING, 0.0)
    assert anim.idle_due is None
    assert shown(anim, 200.0)[0] == "wait"


def test_activity_sets_the_rest_window():
    anim = make(activity="quiet")
    assert REST_WINDOWS_S["quiet"][0] <= anim.idle_due <= REST_WINDOWS_S["quiet"][1]


def test_skin_without_idle_clips_never_schedules_one():
    anim = make(idle=())
    assert anim.idle_due is None


# --- reduced motion ---------------------------------------------------------------------


def test_reduced_motion_holds_the_pose_for_60_s():
    anim = make(reduced_motion=True)
    frames = {anim.frame(t * 0.042).path for t in range(int(60 / 0.042))}
    assert len(frames) == 1
    assert anim.next_change(10.0) is None


def test_reduced_motion_ignores_clicks_and_drags_but_follows_state():
    anim = make(reduced_motion=True)
    assert not anim.click("head", 1.0)
    anim.drag_start(1.0)
    anim.set_state(State.EDITING, 2.0)
    assert shown(anim, 2.5) == ("work", 0)


# --- live settings ----------------------------------------------------------------------


def test_reduced_motion_on_during_a_click_reaction_holds_the_state_pose():
    anim = make()
    anim.set_state(State.EDITING, 0.0)
    assert anim.click("head", 1.0)
    anim.set_reduced_motion(True, 1.15)
    assert anim.reaction is None
    assert shown(anim, 1.15) == ("work", 0)
    assert shown(anim, 30.0) == ("work", 0)
    assert anim.next_change(1.15) is None


def test_reduced_motion_on_during_an_idle_clip_drops_it_and_the_schedule():
    anim = make()
    due = anim.idle_due
    assert shown(anim, due)[0] in ("blink", "hop")
    anim.set_reduced_motion(True, due + 0.1)
    assert anim.idle_clip is None and anim.idle_due is None
    assert shown(anim, due + 0.1) == ("idle", 0)
    assert anim.next_change(due + 0.1) is None


def test_reduced_motion_on_during_a_drag_drops_it_and_the_release_lands_nothing():
    anim = make()
    anim.drag_start(1.0)
    anim.set_reduced_motion(True, 1.2)
    anim.drag_end(1.5)
    assert anim.reaction is None
    assert shown(anim, 1.5) == ("idle", 0)


def test_reduced_motion_off_while_idle_schedules_an_idle_clip():
    anim = make(reduced_motion=True)
    assert anim.idle_due is None
    anim.set_reduced_motion(False, 10.0)
    low, high = REST_WINDOWS_S["normal"]
    assert 10.0 + low <= anim.idle_due <= 10.0 + high
    assert shown(anim, 10.25) == ("idle", 2), "the state clip plays again from the switch"
    assert anim.next_change(10.25) == pytest.approx(10.3)
    assert shown(anim, anim.idle_due)[0] in ("blink", "hop")


def test_reduced_motion_off_restarts_a_shared_looping_clip_from_frame_zero():
    anim = make()
    anim.set_state(State.EDITING, 0.0)
    anim.set_reduced_motion(True, 0.25)
    anim.set_reduced_motion(False, 0.35)
    assert shown(anim, 0.35) == ("work", 0)
    assert anim.idle_due is None, "no idle clips outside idle"


def test_set_activity_reschedules_into_the_new_rest_window():
    anim = make(activity="quiet")
    anim.set_activity("lively", 5.0)
    low, high = REST_WINDOWS_S["lively"]
    assert anim.activity == "lively"
    assert 5.0 + low <= anim.idle_due <= 5.0 + high


def test_set_activity_during_an_idle_clip_does_not_interrupt_it():
    anim = make()
    due = anim.idle_due
    playing = shown(anim, due)[0]
    anim.set_activity("lively", due + 0.15)
    assert shown(anim, due + 0.15) == (playing, 1)
    assert anim.idle_due is None
    end = due + 0.4
    assert shown(anim, end + 0.01)[0] == "idle"
    low, high = REST_WINDOWS_S["lively"]
    assert low <= anim.idle_due - end <= high, "the next rest uses the new level"


def test_set_activity_outside_idle_schedules_nothing():
    anim = make()
    anim.set_state(State.RUNNING, 0.0)
    anim.set_activity("lively", 1.0)
    assert anim.idle_due is None


def test_setters_are_no_ops_when_the_value_is_unchanged():
    anim = make()
    anim.set_state(State.EDITING, 0.0)
    assert anim.click("head", 1.0)
    anim.set_reduced_motion(False, 1.1)
    assert shown(anim, 1.1) == ("pat", 1), "the reaction plays on"

    anim = make(reduced_motion=True)
    started = anim.started
    anim.set_reduced_motion(True, 5.0)
    assert anim.started == started and anim.idle_due is None

    anim = make()
    due = anim.idle_due
    anim.set_activity("normal", 3.0)
    assert anim.idle_due == due, "the rest window is not redrawn"


# --- scheduling and fades ---------------------------------------------------------------


def test_next_change_is_the_next_frame_boundary():
    anim = make()
    assert anim.next_change(0.25) == pytest.approx(0.3)


def test_next_change_includes_the_end_of_a_reaction():
    anim = make()
    anim.click("body", 1.0)
    assert anim.next_change(1.35) == pytest.approx(1.4)


def test_fades_between_clips_but_not_around_drag():
    anim = make()
    assert anim.fades("idle", "work")
    assert not anim.fades("work", "work")
    assert not anim.fades("idle", "drag")
    assert not anim.fades("land", "idle")

# --- mood clips -------------------------------------------------------------------------


def mood(anim):
    return anim.skin.clips["yawn"]


def test_a_mood_plays_while_idle_then_returns_to_idle():
    anim = make()
    assert anim.play_mood(mood(anim), 1.0)
    assert shown(anim, 1.0) == ("yawn", 0)
    assert anim.idle_due is None  # no idle clip lands on top of it
    assert shown(anim, 1.0 + 4 * STEP + 0.01) == ("idle", 0)
    assert anim.idle_due is not None


def test_a_state_change_interrupts_a_mood_at_once():
    anim = make()
    anim.play_mood(mood(anim), 1.0)
    anim.set_state(State.THINKING, 1.1)
    assert shown(anim, 1.1) == ("think", 0)


@pytest.mark.parametrize("state", [State.THINKING, State.WAITING, State.DONE, State.ERROR])
def test_no_mood_outside_idle(state):
    anim = make()
    anim.set_state(state, 0.0)
    assert not anim.play_mood(mood(anim), 0.5)
    assert shown(anim, 0.5)[0] != "yawn"


def test_a_mood_never_cuts_a_reaction_or_an_idle_clip():
    anim = make()
    anim.click("head", 0.0)
    assert not anim.play_mood(mood(anim), 0.1)
    assert shown(anim, 0.1)[0] == "pat"
    later = make()
    due = later.idle_due
    later.update(due)
    assert later.idle_clip is not None
    assert not later.play_mood(mood(later), due + 0.05)


def test_reduced_motion_plays_no_mood():
    anim = make(reduced_motion=True)
    assert not anim.play_mood(mood(anim), 0.0)


# --- variants, wake, gestures, times of day and usage ------------------------------------------


def rich_skin():
    """make_skin's clips plus variants, a nap that a click wakes, talk, cues, timed clips and usage tiers."""
    base = make_skin()
    extra = [
        clip("write"), clip("nap", loop=False), clip("startle", loop=False), clip("talk", loop=False),
        clip("bow", loop=False), clip("meal", loop=False), clip("doze", loop=False),
        clip("rich", loop=False), clip("mid", loop=False), clip("broke", loop=False),
    ]  # fmt: skip
    states = {**base.states, State.EDITING: ("work", "write")}
    reactions = {**base.reactions, "body": ("poke", "tail")}
    return Skin(
        "rich", Path("."), (100, 100), {**base.clips, **{c.name: c for c in extra}}, states, reactions,
        ("blink",), base.zones, talk=("talk",), cues={"greet": ("bow",), "sleep": ("nap",)},
        wake_during=frozenset({"nap"}), wake="startle",
        timed=(Timed("meal", 7 * 60, 9 * 60 + 30), Timed("doze", 23 * 60 + 30, 2 * 60)),
        usage=("rich", "mid", "broke"),
    )  # fmt: skip


def rich(now=0.0, at=datetime(2026, 10, 30, 12, 0), **kwargs):
    clock = {"at": at}
    anim = Animator(rich_skin(), now, rng=random.Random(7), local_now=lambda: clock["at"], **kwargs)
    anim.wall = clock
    return anim


def idle_now(anim, now):
    """Make her rest window end at `now` and return the idle clip she then plays."""
    anim.idle_due = now
    return anim.frame(now).clip


def test_a_state_with_variants_picks_one_and_may_change_at_the_end_of_a_pass(monkeypatch):
    import companion.animator as animator_mod

    anim = rich()
    anim.set_state(State.EDITING, 0.0)
    first = anim.frame(0.0).clip
    assert first in ("work", "write")
    monkeypatch.setattr(animator_mod, "VARIANT_SWITCH", 1.0)
    assert anim.frame(0.35).clip == first, "mid-pass nothing changes"
    after = anim.frame(0.45)
    assert after.clip != first and after.clip in ("work", "write") and after.index == 0
    monkeypatch.setattr(animator_mod, "VARIANT_SWITCH", 0.0)
    assert anim.frame(0.85).clip == after.clip and anim.frame(0.85).index == 0


def test_a_reaction_with_several_clips_picks_among_them():
    seen = set()
    for seed in range(12):
        anim = Animator(rich_skin(), 0.0, rng=random.Random(seed))
        anim.click("body", 0.0)
        seen.add(anim.frame(0.0).clip)
    assert seen == {"poke", "tail"}


def test_a_click_during_her_nap_wakes_her():
    anim = rich()
    anim.idle_clip = "nap"
    anim._play(anim.skin.clips["nap"], 0.0)
    assert anim.click("head", 0.1)
    assert anim.reaction == "wake" and anim.frame(0.1).clip == "startle"
    anim.set_state(State.THINKING, 0.2)
    assert anim.frame(0.2).clip == "think", "a state change cuts the waking short"


def test_a_gesture_plays_on_the_reaction_tier_then_hands_back():
    anim = rich()
    assert anim.gesture(anim.skin.talk_clips(), 0.0)
    assert anim.reaction == "gesture" and anim.frame(0.1).clip == "talk"
    assert not anim.gesture(anim.skin.cue_clips("greet"), 0.1), "one at a time"
    assert not anim.click("head", 0.1)
    assert anim.frame(0.5).clip == "idle", "back to the state clip when it ends"


def test_a_gesture_waits_for_nothing_but_respects_done_and_reduced_motion():
    anim = rich()
    anim.set_state(State.DONE, 0.0)
    assert not anim.gesture(anim.skin.talk_clips(), 0.1), "the done clip is still playing"
    assert anim.gesture(anim.skin.talk_clips(), 0.5)
    anim.set_state(State.EDITING, 0.6)
    assert anim.reaction is None, "a state change cuts it short"
    still = rich(reduced_motion=True)
    assert not still.gesture(still.skin.talk_clips(), 0.0)
    assert not rich().gesture((), 0.0)


def test_a_time_of_day_clip_plays_once_in_its_window_each_day():
    anim = rich(at=datetime(2026, 10, 30, 7, 45))
    assert idle_now(anim, 0.0) == "meal"
    anim.frame(1.0)  # it ends
    assert idle_now(anim, 1.0) == "blink", "once is enough for today"
    anim.frame(2.0)
    anim.wall["at"] = datetime(2026, 10, 31, 8, 0)
    assert idle_now(anim, 2.0) == "meal", "and again tomorrow"
    anim.frame(3.0)
    anim.wall["at"] = datetime(2026, 10, 31, 10, 0)
    assert idle_now(anim, 3.0) == "blink", "outside the window, the usual idle clips"


def test_a_window_past_midnight_belongs_to_the_evening_it_started():
    anim = rich(at=datetime(2026, 10, 30, 23, 45))
    assert idle_now(anim, 0.0) == "doze"
    anim.frame(1.0)
    anim.wall["at"] = datetime(2026, 10, 31, 0, 30)
    assert idle_now(anim, 1.0) == "blink", "still the same night's window"
    anim.frame(2.0)
    anim.wall["at"] = datetime(2026, 10, 31, 23, 40)
    assert idle_now(anim, 2.0) == "doze"


def test_climbing_into_a_usage_tier_plays_its_clip_once():
    anim = rich()
    anim.set_usage(10, 0.0)
    assert anim.usage_due is None, "the first report only sets where she starts"
    anim.set_usage(40, 0.1)
    assert anim.usage_due is None, "still the first tier"
    anim.set_usage(60, 0.2)
    assert anim.frame(0.2).clip == "mid", "idle with nothing playing: at once"
    anim.frame(1.0)
    anim.set_usage(100, 1.0)
    assert anim.frame(1.0).clip == "broke"
    anim.frame(2.0)
    anim.set_usage(5, 2.0)
    assert anim.usage_due is None and anim.usage_tier == 0, "a new window starts over"


def test_a_usage_clip_waits_while_she_works():
    anim = rich()
    anim.set_usage(0, 0.0)
    anim.set_state(State.THINKING, 0.1)
    anim.set_usage(70, 0.2)
    assert anim.frame(0.3).clip == "think" and anim.usage_due == "mid"
    anim.set_state(State.IDLE, 1.0)
    assert idle_now(anim, 1.5) == "mid", "her next idle moment is the purse's"
    anim.set_usage(None, 2.0)
    assert anim.usage_tier == 1, "no report changes nothing"


# --- festivals by date --------------------------------------------------------------------------


def festive(at):
    """rich_skin with a Halloween pumpkin every year and a lion dance on one Spring Festival."""
    base = rich_skin()
    extra = [clip("pumpkin", loop=False), clip("lion", loop=False)]
    skin = Skin(
        "festive", Path("."), (100, 100), {**base.clips, **{c.name: c for c in extra}}, base.states, base.reactions,
        base.idle, base.zones, timed=base.timed,
        dates=(DateClip("pumpkin", every_year=frozenset({"10-31"})), DateClip("lion", once=frozenset({"2027-02-06"}))),
    )  # fmt: skip
    clock = {"at": at}
    anim = Animator(skin, 0.0, rng=random.Random(7), local_now=lambda: clock["at"])
    anim.wall = clock
    return anim


def test_a_festival_clip_plays_once_on_its_day():
    anim = festive(datetime(2026, 10, 31, 15, 0))
    assert idle_now(anim, 0.0) == "pumpkin"
    anim.frame(1.0)
    assert idle_now(anim, 1.0) == "blink", "once is enough"
    anim.frame(2.0)
    anim.wall["at"] = datetime(2026, 11, 1, 15, 0)
    assert idle_now(anim, 2.0) == "blink", "the day after, nothing"
    anim.frame(3.0)
    anim.wall["at"] = datetime(2027, 10, 31, 9, 0)
    assert idle_now(anim, 3.0) == "pumpkin", "and again next year"


def test_a_one_day_festival_plays_only_that_year():
    anim = festive(datetime(2027, 2, 6, 10, 0))
    assert idle_now(anim, 0.0) == "lion"
    anim.frame(1.0)
    anim.wall["at"] = datetime(2028, 2, 6, 10, 0)
    assert idle_now(anim, 1.0) == "blink"


def test_a_festival_comes_before_breakfast_and_after_a_due_purse():
    anim = festive(datetime(2026, 10, 31, 8, 0))
    anim.usage_due = "mid"
    assert idle_now(anim, 0.0) == "mid"
    anim.frame(1.0)
    assert idle_now(anim, 1.0) == "pumpkin"
    anim.frame(2.0)
    assert idle_now(anim, 2.0) == "meal"
