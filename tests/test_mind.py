"""The persona's mind: senses, mood, budget, prompts, voice and the core loop. Never calls the network."""

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from companion.model import MOODS
from mind import files as mind_files
from mind.budget import Budget, cost_usd, in_quiet_hours
from mind.core import Mind
from mind.files import DEFAULT_CONFIG, Files
from mind.mood import Mood, energy_at
from mind.prompts import REPLY_SCHEMA, Redactor, system_prompt, user_prompt
from mind.senses import Senses
from mind.voice import Reply, Voice, find_api_key, parse_reply

DAY = datetime(2026, 10, 28, 10, 0)


def ev(name, session="sess-1", **fields):
    return {"hook_event_name": name, "session_id": session, "project": "cc-companion", **fields}


def kinds(moments):
    return [m.kind for m in moments]


# --- senses ------------------------------------------------------------------------------


def test_first_activity_of_the_day_is_a_greeting_and_counts_a_day():
    senses = Senses()
    assert kinds(senses.on_event(ev("SessionStart", source="startup"), DAY)) == ["greeting"]
    assert senses.summary()["first"] == "10:00" and senses.summary()["days_together"] == 1
    assert senses.on_event(ev("UserPromptSubmit"), DAY + timedelta(minutes=1)) == []


def test_back_after_two_hours_is_a_greeting_again():
    senses = Senses()
    senses.on_event(ev("UserPromptSubmit"), DAY)
    (moment,) = senses.on_event(ev("UserPromptSubmit"), DAY + timedelta(hours=2, minutes=5))
    assert moment.kind == "greeting" and moment.detail == {"first_today": False, "hours_away": 2.1}


def test_claude_working_while_the_user_is_away_is_not_a_return():
    senses = chatty()
    senses.on_event(ev("UserPromptSubmit"), DAY)
    away = DAY + timedelta(minutes=55)
    assert kinds(senses.on_event(ev("PreCompact"), away)) == ["compacting"]
    assert senses.on_event(ev("SubagentStop", subagent=True), away + timedelta(minutes=1)) == []
    assert senses.on_event(ev("SessionStart", source="compact"), away + timedelta(minutes=2)) == []
    (moment,) = senses.on_event(ev("UserPromptSubmit"), DAY + timedelta(hours=2, minutes=40))
    assert moment.kind == "greeting" and moment.detail == {"first_today": False, "hours_away": 2.7}


def test_events_before_the_users_first_prompt_do_not_greet_or_count_a_day():
    senses = Senses()
    assert senses.on_event(ev("PreToolUse", tool_name="Bash"), DAY) == []
    assert senses.summary()["days_together"] == 0 and senses.summary()["first"] is None
    assert kinds(senses.on_event(ev("UserPromptSubmit"), DAY + timedelta(minutes=5))) == ["greeting"]
    assert senses.summary()["first"] == "10:05" and senses.summary()["days_together"] == 1


def test_late_night_needs_the_user():
    senses = Senses()
    night = datetime(2026, 10, 29, 1, 30)
    assert "late_night" not in kinds(senses.on_event(ev("PreCompact"), night))
    assert "late_night" in kinds(senses.on_event(ev("UserPromptSubmit"), night + timedelta(minutes=5)))


def test_a_pat_is_presence_but_answers_for_itself():
    senses = chatty()
    assert kinds(senses.on_pat("head", DAY)) == ["pat"]
    assert kinds(senses.on_event(ev("UserPromptSubmit"), DAY + timedelta(minutes=15))) == ["greeting"], "still the day's first prompt"
    senses.on_pat("tail", DAY + timedelta(minutes=35))
    assert senses.on_event(ev("UserPromptSubmit"), DAY + timedelta(minutes=40)) == [], "the pat was 5 minutes ago, no break"


def test_state_from_before_presence_does_not_greet_again_today():
    old = {"last_activity": DAY.isoformat(), "today": {"date": DAY.date().isoformat(), "first": "09:00"}, "days": [DAY.date().isoformat()]}
    senses = Senses.from_state(old, "chatty")
    assert senses.last_seen == DAY and senses.greeted == DAY.date().isoformat()
    assert kinds(senses.on_event(ev("UserPromptSubmit"), DAY + timedelta(minutes=15))) == ["back"]
    restored = Senses.from_state(json.loads(json.dumps(senses.to_state())), "chatty")
    assert restored.last_seen == DAY + timedelta(minutes=15) and restored.greeted == senses.greeted


def test_a_new_session_later_in_the_day_has_a_cooldown():
    senses = Senses()
    senses.on_event(ev("UserPromptSubmit"), DAY)
    assert kinds(senses.on_event(ev("SessionStart", source="startup"), DAY + timedelta(minutes=30))) == ["new_session"]
    assert senses.on_event(ev("SessionStart", source="resume"), DAY + timedelta(minutes=35)) == []
    assert senses.on_event(ev("SessionStart", source="clear"), DAY + timedelta(minutes=50)) == []


def test_a_long_turn_finishing_is_long_done_and_a_short_one_is_not():
    senses = Senses()
    senses.on_event(ev("UserPromptSubmit"), DAY)
    assert senses.on_event(ev("Stop"), DAY + timedelta(minutes=2)) == []
    senses.on_event(ev("UserPromptSubmit"), DAY + timedelta(minutes=3))
    (moment,) = senses.on_event(ev("Stop"), DAY + timedelta(minutes=10))
    assert moment.kind == "long_done" and moment.detail == {"minutes": 7, "project": "cc-companion"}
    assert senses.summary()["turns"] == 2


def test_subagent_stops_do_not_count_as_turns():
    senses = Senses()
    senses.on_event(ev("UserPromptSubmit"), DAY)
    assert senses.on_event(ev("Stop", subagent=True), DAY + timedelta(minutes=9)) == []
    assert senses.summary()["turns"] == 0


def test_three_errors_in_ten_minutes_is_a_streak_once_per_half_hour():
    senses = Senses()
    senses.on_event(ev("UserPromptSubmit"), DAY)
    for minute in (1, 2):
        assert senses.on_event(ev("PostToolUseFailure", tool_name="Bash"), DAY + timedelta(minutes=minute)) == []
    assert kinds(senses.on_event(ev("StopFailure"), DAY + timedelta(minutes=3))) == ["error_streak"]
    assert senses.on_event(ev("StopFailure"), DAY + timedelta(minutes=4)) == []
    assert senses.on_event(ev("PostToolUseFailure", is_interrupt=True), DAY + timedelta(minutes=5)) == []
    assert senses.summary()["errors"] == 4


def test_late_night_once_per_night():
    senses = Senses()
    night = datetime(2026, 10, 29, 1, 30)
    assert "late_night" in kinds(senses.on_event(ev("UserPromptSubmit"), night))
    assert "late_night" not in kinds(senses.on_event(ev("UserPromptSubmit"), night + timedelta(hours=2)))
    assert "late_night" in kinds(senses.on_event(ev("UserPromptSubmit"), night + timedelta(days=1)))


def test_quota_high_once_per_window_and_status_is_not_activity():
    senses = Senses()
    status = {"hook_event_name": "StatusLine", "session_id": "s", "five_hour_pct": 85.0, "five_hour_resets_at": DAY.timestamp() + 3600}
    (moment,) = senses.on_event(status, DAY)
    assert moment.kind == "quota_high" and moment.detail == {"pct": 85, "resets_in_min": 60}
    assert senses.on_event(status, DAY + timedelta(minutes=1)) == []
    assert senses.on_event({**status, "five_hour_pct": 50.0}, DAY) == []
    assert senses.last_activity is None


def test_compacting_on_the_main_session_only():
    senses = Senses()
    senses.on_event(ev("UserPromptSubmit"), DAY)
    assert senses.on_event(ev("PreCompact", subagent=True), DAY) == []
    assert kinds(senses.on_event(ev("PreCompact"), DAY)) == ["compacting"]


def test_working_long_after_twenty_steady_minutes():
    senses = Senses()
    for minute in range(0, 22, 3):
        senses.on_event(ev("PreToolUse", tool_name="Edit"), DAY + timedelta(minutes=minute))
    (moment,) = senses.tick(DAY + timedelta(minutes=21, seconds=30))
    assert moment.kind == "working_long" and moment.detail == {"minutes": 21}
    assert senses.tick(DAY + timedelta(minutes=21, seconds=40)) == []
    assert senses.tick(DAY + timedelta(minutes=40)) == [], "nothing while away"


def test_turns_done_milestone():
    senses = Senses()
    senses.done_total = 9
    senses.on_event(ev("UserPromptSubmit"), DAY)
    (moment,) = senses.on_event(ev("Stop"), DAY + timedelta(seconds=30))
    assert moment.kind == "milestone" and moment.detail == {"turns_done": 10}


def test_pats_have_a_cooldown_and_unknown_zones_are_ignored():
    senses = Senses()
    assert kinds(senses.on_pat("tail", DAY)) == ["pat"]
    assert senses.on_pat("head", DAY + timedelta(minutes=1)) == []
    assert senses.on_pat("elbow", DAY + timedelta(minutes=10)) == []


def test_tools_and_projects_tally_for_today():
    senses = Senses()
    for tool in ("Edit", "Edit", "Bash", "Read", "Read", "Read"):
        senses.on_event(ev("PreToolUse", tool_name=tool), DAY)
    assert senses.summary()["tools"] == ["Read", "Edit", "Bash"]
    assert senses.summary()["projects"] == ["cc-companion"]


def test_senses_survive_a_restart_and_a_new_day_resets_the_tallies():
    senses = Senses()
    senses.on_event(ev("UserPromptSubmit"), DAY)
    senses.on_event(ev("Stop"), DAY + timedelta(minutes=1))
    restored = Senses.from_state(json.loads(json.dumps(senses.to_state())))
    assert restored.summary() == senses.summary()
    assert restored.on_event(ev("UserPromptSubmit"), DAY + timedelta(minutes=5)) == []
    moments = restored.on_event(ev("UserPromptSubmit"), DAY + timedelta(days=1))
    assert kinds(moments) == ["greeting", "milestone"]  # day 2 together
    assert restored.summary()["turns"] == 0 and restored.summary()["done_total"] == 1


def test_from_state_ignores_garbage():
    assert Senses.from_state({"done_total": "x", "days": 3, "today": {"date": 5}}).done_total == 0
    assert Senses.from_state(None).summary()["turns"] == 0


# --- mood --------------------------------------------------------------------------------


def test_moments_move_her_mood_and_time_eases_it():
    mood = Mood()
    assert mood.apply("long_done", DAY) == "calm"
    assert mood.apply("long_done", DAY) == "proud"
    assert mood.name(DAY + timedelta(hours=4)) == "calm"
    for _ in range(2):
        mood.apply("error_streak", DAY + timedelta(hours=4))
    assert mood.name(DAY + timedelta(hours=4)) == "worried"


def test_she_is_sleepy_late_at_night_unless_very_happy():
    assert Mood().name(datetime(2026, 10, 29, 2, 0)) == "sleepy"
    mood = Mood()
    for _ in range(3):
        mood.apply("pat", datetime(2026, 10, 29, 2, 0))
    assert mood.name(datetime(2026, 10, 29, 2, 0)) == "happy"


def test_pats_raise_affinity_at_most_five_a_day():
    mood = Mood()
    for _ in range(8):
        mood.apply("pat", DAY)
    assert mood.affinity == 5
    mood.apply("pat", DAY + timedelta(days=1))
    assert mood.affinity == 6
    assert Mood.from_state(json.loads(json.dumps(mood.to_state()))).affinity == 6


def test_energy_follows_the_clock():
    assert energy_at(DAY.replace(hour=9)) > energy_at(DAY.replace(hour=20)) > energy_at(DAY.replace(hour=2))


# --- budget ------------------------------------------------------------------------------


CONFIG = dict(DEFAULT_CONFIG)


def test_lines_per_hour_and_min_gap():
    budget = Budget({**CONFIG, "lines_per_hour": 2, "min_gap_s": 60})
    assert budget.why_not("long_done", DAY, waiting=False) is None
    budget.take(DAY)
    assert budget.why_not("long_done", DAY + timedelta(seconds=30), waiting=False) == "too soon"
    budget.take(DAY + timedelta(minutes=2))
    assert budget.why_not("long_done", DAY + timedelta(minutes=5), waiting=False) == "hourly limit"
    assert budget.why_not("long_done", DAY + timedelta(minutes=61), waiting=False) is None


def test_waiting_and_quiet_hours_hold_her_back_except_for_pats():
    budget = Budget(CONFIG)
    assert budget.why_not("long_done", DAY, waiting=True) == "a session needs you"
    assert budget.why_not("pat", DAY, waiting=True) is None
    night = DAY.replace(hour=3)
    assert budget.why_not("greeting", night, waiting=False) == "quiet hours"
    assert budget.why_not("late_night", night, waiting=False) is None


def test_quiet_hours_can_wrap_midnight():
    assert in_quiet_hours(23, [22, 6]) and in_quiet_hours(3, [22, 6]) and not in_quiet_hours(12, [22, 6])
    assert not in_quiet_hours(3, [0, 0])


def test_spend_is_costed_per_day_and_caps_her():
    budget = Budget({**CONFIG, "daily_usd": 0.001})
    usage = {"input_tokens": 1000, "cache_read_input_tokens": 2000, "output_tokens": 200}
    cost = budget.record("claude-haiku-5-5", usage, DAY)
    assert cost == pytest.approx((1000 * 0.10 + 2000 * 0.01 + 200 * 0.50) / 1e6)
    assert budget.spend["calls"] == 1 and budget.spend["cached_tokens"] == 2000
    for _ in range(4):
        budget.record("claude-haiku-5-5", usage, DAY)
    assert budget.why_not("long_done", DAY, waiting=False) == "daily cap"
    assert budget.spent_today(DAY + timedelta(days=1)) == 0.0


def test_an_unknown_model_is_costed_high():
    assert cost_usd("claude-mystery", {"output_tokens": 1_000_000}) == 50.0


# --- prompts -----------------------------------------------------------------------------


def test_system_prompt_is_stable_and_carries_identity_and_self():
    one = system_prompt("I am Shio.", "I like late nights.")
    assert one == system_prompt("I am Shio.", "I like late nights.")
    assert "<identity>\nI am Shio.\n</identity>" in one and "I like late nights." in one
    assert "<self>" not in system_prompt("I am Shio.", "")
    for mood in MOODS:
        assert mood in one


def test_user_prompt_holds_the_moment_and_only_metadata():
    text = user_prompt(
        "long_done", {"minutes": 12, "project": "cc-companion"},
        now=DAY, display={"state": "done", "sessions": 2, "project": "cc-companion"},
        today={"first": "09:00", "turns": 4, "errors": 1, "tools": ["Edit"], "projects": ["cc-companion"], "done_total": 40, "days_together": 3},
        mood="proud", energy=85, recent=["早呀～"], project=Redactor(False),
    )
    assert "A long turn in cc-companion just finished after 12 minutes" in text
    assert "just finished in cc-companion; 2 live session(s)" in text
    assert "first activity at 09:00; 4 turns done; 1 errors; most used tools: Edit; projects: cc-companion" in text
    assert "Her mood: proud; her energy: 85/100." in text
    assert text.endswith("- 早呀～")


def test_redaction_gives_stable_aliases():
    redact = Redactor(True)
    assert (redact("secret-client"), redact("other"), redact("secret-client")) == ("project A", "project B", "project A")
    text = user_prompt(
        "new_session", {"project": "secret-client"}, now=DAY, display={}, today={"projects": ["secret-client"]},
        mood="calm", energy=50, recent=[], project=redact,
    )
    assert "secret-client" not in text and "project A" in text


# --- voice -------------------------------------------------------------------------------


class FakeClient:
    def __init__(self, text='{"say": "好的！", "mood": "happy", "notice": ""}', stop="end_turn", error=None):
        self.calls = []
        self.text, self.stop, self.error = text, stop, error
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        usage = SimpleNamespace(input_tokens=50, cache_creation_input_tokens=0, cache_read_input_tokens=900, output_tokens=40)
        content = [SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=self.text)]
        return SimpleNamespace(stop_reason=self.stop, content=content, usage=usage)


def test_voice_asks_for_structured_output_with_a_cached_system_prompt():
    client = FakeClient()
    reply, usage = Voice(client, "claude-haiku-5-5").speak("SYSTEM", "USER")
    assert reply == Reply("好的！", "happy", "")
    assert usage["cache_read_input_tokens"] == 900
    (call,) = client.calls
    assert call["model"] == "claude-haiku-5-5"
    assert call["system"] == [{"type": "text", "text": "SYSTEM", "cache_control": {"type": "ephemeral"}}]
    assert call["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": REPLY_SCHEMA}}
    assert "temperature" not in call and "thinking" not in call


@pytest.mark.parametrize("client", [FakeClient(error=RuntimeError("boom")), FakeClient(stop="refusal"), FakeClient(text="nope")])
def test_voice_failures_are_silent(client, caplog):
    reply, _ = Voice(client, "claude-haiku-5-5").speak("SYSTEM", "USER")
    assert reply is None
    assert all("SYSTEM" not in r.getMessage() and "USER" not in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    "text, expected",
    [
        ('{"say": " hi\\n there ", "mood": "calm", "notice": "x"}', Reply("hi there", "calm", "x")),
        ('{"say": "", "mood": "sleepy", "notice": ""}', Reply("", "sleepy", "")),
        ('{"say": "hi", "mood": "angry", "notice": ""}', None),
        ('{"say": "' + "x" * 121 + '", "mood": "calm", "notice": ""}', None),
        ("[]", None),
    ],
)
def test_parse_reply(text, expected):
    assert parse_reply(text) == expected


def test_the_key_comes_from_the_companion_name_first(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "general")
    monkeypatch.setenv("CC_COMPANION_API_KEY", "companion")
    assert find_api_key() == "companion"
    monkeypatch.delenv("CC_COMPANION_API_KEY")
    assert find_api_key() == "general"


# --- files -------------------------------------------------------------------------------


def test_setup_copies_the_identity_and_writes_a_default_config(isolated_app_dir):
    files = Files()
    files.setup()
    assert files.identity.read_text(encoding="utf-8") == mind_files.TEMPLATE.read_text(encoding="utf-8")
    assert files.load_config() == DEFAULT_CONFIG
    assert "<!--" not in files.identity_text() and "汐" in files.identity_text()
    files.identity.write_text("mine", encoding="utf-8")
    files.setup()
    assert files.identity.read_text(encoding="utf-8") == "mine", "never overwritten"


def test_config_keeps_valid_values_and_drops_invalid_ones(isolated_app_dir):
    files = Files()
    files.setup()
    mind_files.write_json(files.config, {"lines_per_hour": 10, "daily_usd": -1, "quiet_hours": [1], "redact_projects": True})
    config = files.load_config()
    assert (config["lines_per_hour"], config["daily_usd"], config["quiet_hours"], config["redact_projects"]) == (
        10, DEFAULT_CONFIG["daily_usd"], DEFAULT_CONFIG["quiet_hours"], True,
    )


# --- the core loop -----------------------------------------------------------------------


class Clock:
    def __init__(self, at=DAY):
        self.at = at

    def __call__(self):
        return self.at


@pytest.fixture
def mind(isolated_app_dir):
    files = Files()
    files.setup()
    said, cues = [], []
    clock = Clock()
    client = FakeClient()

    def say(text, mood, reply=False, cue=None):
        said.append((text, mood) + (("reply",) if reply else ()))
        cues.append(cue)
        return 204

    built = Mind(files, files.load_config(), Voice(client, "claude-haiku-5-5"), say, now=clock)
    built.said, built.cues, built.clock, built.client = said, cues, clock, client
    return built


def run(jobs):
    for job in jobs:
        job()
    return len(jobs)


def test_a_greeting_becomes_a_line_and_is_remembered(mind):
    assert run(mind.handle({"type": "event", "event": ev("SessionStart", source="startup")})) == 1
    assert mind.said == [("好的！", "happy")]
    episodes = [json.loads(line) for line in (mind.files.episodes / "2026-10-28.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [e["kind"] for e in episodes] == ["event", "moment", "said"]
    assert episodes[1]["moment"] == "greeting" and episodes[2]["text"] == "好的！"
    assert mind.budget.spend["calls"] == 1 and json.loads(mind.files.spend.read_text(encoding="utf-8"))["calls"] == 1
    assert json.loads(mind.files.state.read_text(encoding="utf-8"))["senses"]["today"]["date"] == "2026-10-28"


def test_the_prompt_carries_her_identity_and_recent_lines(mind):
    run(mind.handle({"type": "event", "event": ev("SessionStart", source="startup")}))
    mind.clock.at += timedelta(minutes=30)
    run(mind.handle({"type": "pat", "zone": "head"}))
    call = mind.client.calls[-1]
    assert "汐" in call["system"][0]["text"]
    assert "The user just patted her head." in call["messages"][0]["content"]
    assert "- 好的！" in call["messages"][0]["content"]


def test_quiet_mode_sends_moods_only_and_never_calls_the_api(mind):
    mind.handle({"type": "persona", "mode": "quiet"})
    run(mind.handle({"type": "event", "event": ev("SessionStart", source="startup")}))
    assert mind.said == [(None, "calm")] and mind.client.calls == []


def test_a_held_back_moment_is_logged_but_costs_nothing(mind):
    mind.handle({"type": "display", "display": {"state": "waiting", "sessions": 1}})
    assert mind.handle({"type": "event", "event": ev("SessionStart", source="startup")}) == []
    assert mind.client.calls == [] and mind.said == []
    assert '"moment": "greeting"' in (mind.files.episodes / "2026-10-28.jsonl").read_text(encoding="utf-8")


def test_past_the_daily_cap_she_goes_mood_only(mind):
    mind.budget.spend = {"date": "2026-10-28", "usd": 5.0}
    run(mind.handle({"type": "event", "event": ev("SessionStart", source="startup")}))
    assert mind.said == [(None, "calm")] and mind.client.calls == []


def test_only_one_line_in_flight(mind):
    first = mind.handle({"type": "event", "event": ev("SessionStart", source="startup")})
    mind.clock.at += timedelta(minutes=5)
    assert mind.handle({"type": "pat", "zone": "tail"}) == []  # the greeting's call has not run yet
    run(first)
    assert not mind.busy


def test_silence_plays_the_mood_and_is_not_remembered_as_said(mind):
    mind.client.text = '{"say": "", "mood": "sleepy", "notice": "works late"}'
    run(mind.handle({"type": "event", "event": ev("SessionStart", source="startup")}))
    assert mind.said == [(None, "sleepy")]
    log = (mind.files.episodes / "2026-10-28.jsonl").read_text(encoding="utf-8")
    assert '"kind": "said"' not in log and '"text": "works late"' in log


def test_episodes_keep_only_a_few_event_fields(mind):
    mind.handle({"type": "event", "event": {**ev("PreToolUse", tool_name="Bash"), "model": "Opus", "permission_mode": "plan"}})
    record = json.loads((mind.files.episodes / "2026-10-28.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert set(record) == {"t", "kind", "event", "tool_name", "project", "session"}


def test_bad_messages_are_ignored(mind):
    for message in (None, [], {"type": "event", "event": "x"}, {"type": "persona", "mode": "loud"}, {"type": "nope"}):
        assert mind.handle(message) == []
    assert mind.mode == "on"


# --- chattiness ------------------------------------------------------------------------------


class FixedRng:
    def __init__(self, value):
        self.value = value

    def random(self):
        return self.value


def chatty(roll=0.0):
    return Senses("chatty", rng=FixedRng(roll))


def test_chatty_counts_shorter_turns_as_long():
    senses = chatty()
    senses.on_event(ev("UserPromptSubmit"), DAY)
    assert kinds(senses.on_event(ev("Stop"), DAY + timedelta(minutes=3))) == ["long_done"]


def test_chatty_answers_some_short_turns_by_chance_with_a_cooldown():
    lucky, unlucky = chatty(0.1), chatty(0.9)
    for senses in (lucky, unlucky):
        senses.on_event(ev("UserPromptSubmit"), DAY)
    (moment,) = lucky.on_event(ev("Stop"), DAY + timedelta(seconds=40))
    assert moment.kind == "turn_done" and moment.detail == {"seconds": 40, "project": "cc-companion"}
    assert unlucky.on_event(ev("Stop"), DAY + timedelta(seconds=40)) == []
    lucky.on_event(ev("UserPromptSubmit"), DAY + timedelta(minutes=1))
    assert lucky.on_event(ev("Stop"), DAY + timedelta(minutes=1, seconds=30)) == [], "3 minute cooldown"


def test_normal_and_quiet_never_add_chatty_moments():
    for name in ("normal", "quiet"):
        senses = Senses(name, rng=FixedRng(0.0))
        senses.on_event(ev("UserPromptSubmit"), DAY)
        assert senses.on_event(ev("Stop"), DAY + timedelta(seconds=40)) == []
        assert senses.on_event(ev("UserPromptSubmit"), DAY + timedelta(minutes=50)) == []
        assert senses.on_event(ev("SessionEnd", reason="other"), DAY + timedelta(minutes=51)) == []


def test_quiet_waits_longer_for_a_long_turn():
    senses = Senses("quiet")
    senses.on_event(ev("UserPromptSubmit"), DAY)
    assert senses.on_event(ev("Stop"), DAY + timedelta(minutes=7)) == []


def test_chatty_notices_a_short_break_and_a_goodbye():
    senses = chatty()
    senses.on_event(ev("UserPromptSubmit"), DAY)
    (moment,) = senses.on_event(ev("UserPromptSubmit"), DAY + timedelta(minutes=15))
    assert moment.kind == "back" and moment.detail == {"minutes_away": 15}
    assert kinds(senses.on_event(ev("SessionEnd", reason="other"), DAY + timedelta(minutes=16))) == ["goodbye"]
    assert senses.on_event(ev("SessionEnd", reason="clear"), DAY + timedelta(minutes=17)) == []


def test_chatty_works_long_every_ten_minutes_and_muses_in_between():
    senses = chatty()
    got = []
    for minute in range(0, 31):
        when = DAY + timedelta(minutes=minute)
        senses.on_event(ev("PreToolUse", tool_name="Edit"), when)
        got += [(minute, m.kind) for m in senses.tick(when + timedelta(seconds=30))]
    # A musing waits 3 minutes after a working moment, then comes every 12 minutes.
    assert got == [(10, "working_long"), (13, "musing"), (20, "working_long"), (25, "musing"), (30, "working_long")]


def test_a_musing_never_follows_right_after_another_moment():
    senses = chatty()
    for minute in range(0, 13):
        senses.on_event(ev("PreToolUse", tool_name="Edit"), DAY + timedelta(minutes=minute))
    senses.last_moment["working_long"] = DAY + timedelta(minutes=12)  # no working_long due
    senses.on_pat("head", DAY + timedelta(minutes=12))
    assert senses.tick(DAY + timedelta(minutes=13)) == []
    for minute in (13, 14, 15):
        senses.on_event(ev("PreToolUse", tool_name="Edit"), DAY + timedelta(minutes=minute))
    assert kinds(senses.tick(DAY + timedelta(minutes=15, seconds=30))) == ["musing"]


def test_chattiness_comes_from_config_and_bad_values_fall_back(isolated_app_dir):
    files = Files()
    files.setup()
    assert files.load_config()["chattiness"] == "normal"
    mind_files.write_json(files.config, {"chattiness": "chatty"})
    assert files.load_config()["chattiness"] == "chatty"
    mind_files.write_json(files.config, {"chattiness": "LOUD"})
    assert files.load_config()["chattiness"] == "normal"
    built = Mind(files, {**files.load_config(), "chattiness": "chatty"}, None, lambda text, mood, **kw: 204)
    assert built.senses.profile.musing is not None


def test_new_moments_have_words_in_her_prompt():
    from mind.prompts import describe_moment

    redact = Redactor(False)
    assert "short turn in x just finished after 40 seconds" in describe_moment("turn_done", {"seconds": 40, "project": "x"}, redact)
    assert "working for 14 minutes" in describe_moment("musing", {"minutes_working": 14}, redact)
    assert "back at the desk after about 15 minutes away." in describe_moment("back", {"minutes_away": 15}, redact)
    assert "session in x just ended" in describe_moment("goodbye", {"project": "x"}, redact)

def test_the_menu_switches_her_chattiness_live(mind):
    assert mind.senses.profile.musing is None
    assert mind.handle({"type": "chattiness", "level": "chatty"}) == []
    assert mind.senses.profile.musing is not None and mind.config["chattiness"] == "chatty"
    mind.handle({"type": "chattiness", "level": "LOUD"})
    assert mind.config["chattiness"] == "chatty"


# --- chat ------------------------------------------------------------------------------------


def episodes(mind):
    path = mind.files.episodes / "2026-10-28.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_a_chat_gets_an_answer_as_a_reply_and_both_sides_are_remembered(mind):
    mind.client.text = '{"say": "我也喜欢猫～", "mood": "happy", "notice": "has a cat called Mochi"}'
    assert run(mind.handle({"type": "chat", "text": "  我家猫叫 Mochi\n"})) == 1
    assert mind.said == [("我也喜欢猫～", "happy", "reply")]
    chats = [(e["from"], e["text"]) for e in episodes(mind) if e["kind"] == "chat"]
    assert chats == [("user", "我家猫叫 Mochi"), ("her", "我也喜欢猫～")]
    assert [e["text"] for e in episodes(mind) if e["kind"] == "notice"] == ["has a cat called Mochi"]
    prompt = mind.client.calls[0]["messages"][0]["content"]
    assert "<user_says>我家猫叫 Mochi</user_says>" in prompt and "talking to her directly" in prompt
    assert mind.budget.spend["calls"] == 1


def test_the_conversation_so_far_goes_with_the_next_message(mind):
    run(mind.handle({"type": "chat", "text": "第一句"}))
    run(mind.handle({"type": "chat", "text": "第二句"}))
    prompt = mind.client.calls[1]["messages"][0]["content"]
    assert "- The user: 第一句\n- She: 好的！" in prompt and "<user_says>第二句</user_says>" in prompt


def test_todays_chat_survives_a_restart(mind):
    run(mind.handle({"type": "chat", "text": "记住我"}))
    again = Mind(mind.files, mind.files.load_config(), mind.voice, mind.say, now=mind.clock)
    assert again.chat_log == [("2026-10-28", "user", "记住我"), ("2026-10-28", "her", "好的！")]


def test_silence_and_a_failed_call_in_a_chat(mind):
    mind.client.text = '{"say": "", "mood": "curious", "notice": ""}'
    run(mind.handle({"type": "chat", "text": "嗯"}))
    assert mind.said == [(None, "curious", "reply")]
    mind.client.error = RuntimeError("down")
    run(mind.handle({"type": "chat", "text": "在吗"}))
    assert mind.said == [(None, "curious", "reply")], "a failed call sends nothing; the box times out"


def test_chat_ignores_quiet_hours_and_waiting_but_not_the_daily_cap(mind):
    mind.clock.at = datetime(2026, 10, 29, 3, 0)
    mind.senses.display = {"state": "waiting", "sessions": 1}
    assert run(mind.handle({"type": "chat", "text": "还没睡"})) == 1
    assert mind.said[-1] == ("好的！", "happy", "reply")
    mind.budget.spend = {"date": "2026-10-29", "usd": 1.0}
    run(mind.handle({"type": "chat", "text": "晚安"}))
    assert mind.said[-1][0] is None and mind.said[-1][2] == "reply"
    assert len(mind.client.calls) == 1


def test_quiet_mode_answers_a_chat_with_a_mood_only(mind):
    mind.handle({"type": "persona", "mode": "quiet"})
    run(mind.handle({"type": "chat", "text": "hi"}))
    assert mind.said == [(None, mind.said[0][1], "reply")] and mind.client.calls == []


def test_empty_or_odd_chats_are_ignored(mind):
    for text in ("", "   ", "\x00\u200b", None, 5):
        assert mind.handle({"type": "chat", "text": text}) == []
    assert episodes(mind) == []


def test_a_long_chat_is_cut(mind):
    from mind.core import CHAT_MAX_CHARS, clean_chat

    assert clean_chat("a\tb\r\nc") == "a b c"
    assert len(clean_chat("字" * 1000)) == CHAT_MAX_CHARS


def test_moments_hush_for_a_while_after_a_chat(mind):
    run(mind.handle({"type": "chat", "text": "hi"}))
    assert mind.budget.why_not("pat", DAY + timedelta(minutes=1), waiting=False) == "talking with you"
    assert mind.budget.why_not("pat", DAY + timedelta(minutes=3), waiting=False) is None


def test_a_chat_is_presence_not_a_greeting(mind):
    run(mind.handle({"type": "chat", "text": "早"}))
    assert mind.senses.last_seen == DAY
    assert all(e["kind"] != "moment" for e in episodes(mind))


def test_chat_has_its_own_hourly_limit():
    from mind.budget import CHAT_PER_HOUR

    budget = Budget(dict(DEFAULT_CONFIG))
    for _ in range(CHAT_PER_HOUR):
        assert budget.why_not_chat(DAY) is None
        budget.take_chat(DAY)
    assert budget.why_not_chat(DAY) == "chat limit"
    assert budget.why_not_chat(DAY + timedelta(hours=1)) is None


# --- no pause between lines ------------------------------------------------------------------


def test_no_pause_skips_the_gap_and_the_hush_but_not_the_ceilings():
    budget = Budget({**DEFAULT_CONFIG, "no_gap": True, "lines_per_hour": 3})
    budget.take_chat(DAY)
    for _ in range(3):
        assert budget.why_not("working_long", DAY, waiting=False) is None, "no 60 s gap, no hush after the chat"
        budget.take(DAY)
    assert budget.why_not("working_long", DAY, waiting=False) == "hourly limit"
    budget.spend = {"date": "2026-10-28", "usd": 1.0}
    assert budget.why_not("pat", DAY, waiting=False) == "daily cap"
    assert budget.why_not("musing", datetime(2026, 10, 29, 3, 0), waiting=False) == "quiet hours"


def test_without_no_pause_the_gap_holds():
    budget = Budget(dict(DEFAULT_CONFIG))
    assert DEFAULT_CONFIG["no_gap"] is False
    budget.take(DAY)
    assert budget.why_not("working_long", DAY + timedelta(seconds=30), waiting=False) == "too soon"


def test_the_menu_switches_no_pause_live(mind):
    assert mind.handle({"type": "no_gap", "on": True}) == []
    assert mind.config["no_gap"] is True
    mind.handle({"type": "no_gap", "on": "yes"})
    assert mind.config["no_gap"] is True, "only a real boolean counts"
    mind.handle({"type": "no_gap", "on": False})
    assert mind.config["no_gap"] is False


def test_no_gap_comes_from_config(isolated_app_dir):
    files = Files()
    files.setup()
    mind_files.write_json(files.config, {"no_gap": True})
    assert files.load_config()["no_gap"] is True
    mind_files.write_json(files.config, {"no_gap": "yes"})
    assert files.load_config()["no_gap"] is False


def test_no_pause_lifts_the_pat_cooldown_and_off_restores_it():
    senses = chatty()
    senses.set_no_gap(True)
    assert [kinds(senses.on_pat("head", DAY + timedelta(seconds=s))) for s in (0, 5, 10)] == [["pat"]] * 3
    senses.set_chattiness("normal")
    assert kinds(senses.on_pat("tail", DAY + timedelta(seconds=15))) == ["pat"], "a profile switch keeps No pause"
    senses.set_no_gap(False)
    assert senses.on_pat("tail", DAY + timedelta(seconds=20)) == [], "the normal 3-minute cooldown is back"


def test_the_mind_starts_with_no_pause_from_config(mind):
    again = Mind(mind.files, {**mind.files.load_config(), "no_gap": True}, mind.voice, mind.say, now=mind.clock)
    assert again.senses.cooldowns["pat"] == timedelta(0)
    mind.handle({"type": "no_gap", "on": True})
    assert mind.senses.cooldowns["pat"] == timedelta(0)


# --- her limit -------------------------------------------------------------------------------


def test_unlimited_drops_the_hourly_ceilings_and_the_dollar_cap():
    from mind.budget import CHAT_PER_HOUR

    budget = Budget({**DEFAULT_CONFIG, "limit": "unlimited", "lines_per_hour": 2, "no_gap": True})
    budget.spend = {"date": "2026-10-28", "usd": 99.0}
    for _ in range(5):
        assert budget.why_not("working_long", DAY, waiting=False) is None
        budget.take(DAY)
    for _ in range(CHAT_PER_HOUR + 5):
        assert budget.why_not_chat(DAY) is None
        budget.take_chat(DAY)


def test_a_daily_number_counts_lines_and_replies_together_until_midnight():
    budget = Budget({**DEFAULT_CONFIG, "limit": 3, "lines_per_hour": 1, "no_gap": True})
    budget.spend = {"date": "2026-10-28", "usd": 99.0}
    budget.take(DAY)
    budget.take_chat(DAY)
    assert budget.why_not("working_long", DAY, waiting=False) is None, "no hourly ceiling and no dollar cap"
    budget.take(DAY)
    assert budget.lines_today(DAY) == 3
    assert budget.why_not("pat", DAY, waiting=False) == "daily limit"
    assert budget.why_not_chat(DAY) == "daily limit"
    assert budget.why_not_chat(datetime(2026, 10, 29, 0, 1)) is None, "a new day starts the count again"


def test_the_daily_count_rides_in_spend_json(mind):
    mind.handle({"type": "limit", "limit": 2})
    run(mind.handle({"type": "chat", "text": "一"}))
    assert json.loads(mind.files.spend.read_text(encoding="utf-8"))["lines"] == 1
    again = Mind(mind.files, mind.files.load_config(), mind.voice, mind.say, now=mind.clock)
    assert again.budget.lines_today(DAY) == 1


def test_past_her_daily_limit_she_answers_with_a_mood(mind):
    mind.handle({"type": "limit", "limit": 1})
    run(mind.handle({"type": "chat", "text": "一"}))
    run(mind.handle({"type": "chat", "text": "二"}))
    assert mind.said[0] == ("好的！", "happy", "reply")
    assert mind.said[1][0] is None and mind.said[1][2] == "reply"
    assert len(mind.client.calls) == 1


def test_the_log_names_her_limit():
    from mind.budget import describe_limit

    assert describe_limit(dict(DEFAULT_CONFIG)) == "at most 30 lines an hour and $0.50 a day"
    assert describe_limit({**DEFAULT_CONFIG, "limit": "unlimited", "no_gap": True}) == "no limit, no pause"
    assert describe_limit({**DEFAULT_CONFIG, "limit": 1200}) == "at most 1,200 lines and replies a day"


def test_the_limit_message_and_config_accept_only_real_limits(mind):
    for good in ("unlimited", 250, "normal"):
        mind.handle({"type": "limit", "limit": good})
        assert mind.config["limit"] == good
    for bad in (0, -5, True, "lots", 2.5, None):
        mind.handle({"type": "limit", "limit": bad})
        assert mind.config["limit"] == "normal"
    mind_files.write_json(mind.files.config, {"limit": 0})
    assert mind.files.load_config()["limit"] == "normal"
    mind_files.write_json(mind.files.config, {"limit": 800})
    assert mind.files.load_config()["limit"] == 800


# --- private today ---------------------------------------------------------------------------


def test_a_private_day_writes_no_episodes_but_she_still_talks(mind):
    mind.handle({"type": "private", "day": "2026-10-28"})
    assert run(mind.handle({"type": "event", "event": ev("SessionStart", source="startup")})) == 1
    run(mind.handle({"type": "chat", "text": "这句别记"}))
    assert mind.said == [("好的！", "happy"), ("好的！", "happy", "reply")]
    assert episodes(mind) == []
    prompt = mind.client.calls[1]["messages"][0]["content"]
    assert "<user_says>这句别记</user_says>" in prompt, "the conversation is still held in mind"


def test_private_ends_with_her_day_and_can_be_turned_off(mind):
    mind.handle({"type": "private", "day": "2026-10-28"})
    mind.clock.at = datetime(2026, 10, 29, 6, 0)
    mind.handle({"type": "event", "event": ev("UserPromptSubmit")})
    assert (mind.files.episodes / "2026-10-29.jsonl").exists()
    mind.handle({"type": "private", "day": "2026-10-29"})
    mind.handle({"type": "private", "day": None})
    assert not mind.private(mind.clock.at)


def test_private_day_is_read_from_config_at_start(mind):
    mind_files.write_json(mind.files.config, {**mind.files.load_config(), "private_day": "2026-10-28"})
    again = Mind(mind.files, mind.files.load_config(), mind.voice, mind.say, now=mind.clock)
    assert again.private(DAY)


# --- the desk and the clock ------------------------------------------------------------------

MIN = timedelta(minutes=1)


def test_reading_at_the_desk_for_hours_is_not_a_return():
    senses = Senses("quiet")
    senses.on_event(ev("UserPromptSubmit"), DAY)
    for minute in range(5, 180, 5):  # three hours of reading, touching the mouse now and then
        assert senses.on_idle(timedelta(seconds=40), DAY + minute * MIN) == []
    assert senses.on_event(ev("UserPromptSubmit"), DAY + timedelta(hours=3)) == [], "never away, so no greeting"


def test_coming_back_to_the_desk_greets_before_any_prompt():
    senses = Senses("quiet")
    senses.on_event(ev("UserPromptSubmit"), DAY)
    senses.on_idle(timedelta(0), DAY + 5 * MIN)
    assert senses.on_idle(timedelta(minutes=30), DAY + 35 * MIN) == [], "away: nothing yet"
    (moment,) = senses.on_idle(timedelta(seconds=2), DAY + timedelta(hours=3))
    assert moment.kind == "greeting" and moment.detail == {"first_today": False, "hours_away": 2.9}
    assert senses.on_event(ev("UserPromptSubmit"), DAY + timedelta(hours=3, minutes=1)) == []


def test_the_first_input_of_the_day_is_her_greeting():
    senses = Senses()
    (moment,) = senses.on_idle(timedelta(seconds=1), DAY)
    assert moment.kind == "greeting" and moment.detail["first_today"] is True
    assert senses.summary()["days_together"] == 1 and senses.summary()["first"] == "10:00"


def test_back_at_the_desk_with_news_of_what_happened_meanwhile():
    senses = Senses()
    senses.on_event(ev("UserPromptSubmit"), DAY)
    senses.on_event(ev("UserPromptSubmit"), DAY + 2 * MIN)
    assert senses.on_idle(timedelta(minutes=12), DAY + 14 * MIN) == []
    assert kinds(senses.on_event(ev("Stop"), DAY + 20 * MIN)) == ["long_done"]
    senses.on_event(ev("PostToolUseFailure"), DAY + 21 * MIN)
    (moment,) = senses.on_idle(timedelta(seconds=3), DAY + 40 * MIN)
    assert moment.kind == "back" and moment.detail == {"minutes_away": 38, "turns_while_away": 1, "errors_while_away": 1}
    assert senses.while_away == {"turns": 0, "errors": 0}


def test_back_with_nothing_new_is_quiet_and_the_quiet_profile_brings_no_news():
    for name, done in (("normal", False), ("quiet", True)):
        senses = Senses(name)
        senses.on_event(ev("UserPromptSubmit"), DAY)
        senses.on_idle(timedelta(0), DAY + MIN)
        if done:
            senses.on_event(ev("Stop"), DAY + 5 * MIN)
        assert senses.on_idle(timedelta(seconds=3), DAY + 40 * MIN) == [], name


def test_without_the_idle_sense_only_actions_count():
    senses = Senses()
    assert senses.on_idle(None, DAY) == []
    senses.on_event(ev("UserPromptSubmit"), DAY)
    senses.on_event(ev("Stop"), DAY + MIN)
    assert senses.on_event(ev("UserPromptSubmit"), DAY + 30 * MIN) == [], "no news without knowing they left"


def test_part_of_day():
    from mind.senses import part_of_day

    hours = {5: "morning", 11: "morning", 12: "afternoon", 17: "afternoon", 18: "evening", 22: "evening", 23: "night", 0: "night", 1: None, 4: None}
    assert {hour: part_of_day(DAY.replace(hour=hour)) for hour in hours} == hours


def test_each_new_part_of_the_day_once_after_the_greeting():
    for name in ("normal", "quiet"):
        senses = Senses(name)
        assert kinds(senses.on_event(ev("UserPromptSubmit"), DAY)) == ["greeting"]
        assert senses.on_event(ev("UserPromptSubmit"), DAY + timedelta(hours=1)) == []
        got = kinds(senses.on_event(ev("UserPromptSubmit"), DAY.replace(hour=12, minute=30)))
        assert got == (["day_part"] if name == "normal" else [])
        assert senses.on_event(ev("UserPromptSubmit"), DAY.replace(hour=13)) == []
        seen, when = [], DAY.replace(hour=13)
        while when < DAY.replace(hour=23) + timedelta(hours=3):  # at the desk until 02:00, never prompting
            seen += [(when.strftime("%H:%M"), m.kind, m.detail.get("part")) for m in senses.on_idle(timedelta(seconds=30), when)]
            when += 5 * MIN
        parts = [("18:00", "day_part", "evening"), ("23:00", "day_part", "night")] if name == "normal" else []
        assert seen == parts + [("01:00", "late_night", None)], name


def test_a_day_part_names_the_part_and_since_when():
    senses = Senses()
    senses.on_event(ev("UserPromptSubmit"), DAY)
    senses.on_event(ev("UserPromptSubmit"), DAY + timedelta(hours=1))
    (moment,) = senses.on_event(ev("UserPromptSubmit"), DAY.replace(hour=12, minute=30))
    assert moment.detail == {"part": "afternoon", "time": "12:30", "since": "10:00"}


def test_the_desk_and_the_parts_survive_a_restart():
    senses = Senses()
    senses.on_event(ev("UserPromptSubmit"), DAY)
    senses.on_idle(timedelta(minutes=15), DAY + 15 * MIN)
    senses.on_event(ev("Stop"), DAY + 16 * MIN)
    restored = Senses.from_state(json.loads(json.dumps(senses.to_state())))
    assert restored.while_away == {"turns": 1, "errors": 0} and restored.today["parts"] == ["morning"]
    odd = Senses.from_state({"while_away": {"turns": "x"}, "today": {"date": "2026-10-28", "parts": ["noon", "evening"]}})
    assert odd.while_away == {"turns": 0, "errors": 0} and odd.today["parts"] == ["evening"]


def test_away_from_the_desk_holds_lines_but_not_pats_and_pats_skip_the_gap():
    budget = Budget(dict(DEFAULT_CONFIG))
    assert budget.why_not("long_done", DAY, waiting=False, away=True) == "nobody at the desk"
    assert budget.why_not("pat", DAY, waiting=False, away=True) is None
    budget.take(DAY)
    assert budget.why_not("pat", DAY + timedelta(seconds=5), waiting=False) is None
    assert budget.why_not("long_done", DAY + timedelta(seconds=5), waiting=False) == "too soon"


def test_the_desk_and_the_clock_have_words_in_her_prompt():
    from mind.prompts import describe_moment

    redact = Redactor(False)
    back = describe_moment("back", {"minutes_away": 38, "turns_while_away": 2, "errors_while_away": 1}, redact)
    assert back == (
        "The user is back at the desk after about 38 minutes away. "
        "While they were away, 2 turn(s) of work finished and 1 error(s) came up."
    )
    part = describe_moment("day_part", {"part": "evening", "time": "18:00", "since": "10:00"}, redact)
    assert part == "A new part of the day has begun: it is evening now (18:00), and the user has been around since 10:00."
    assert describe_moment("greeting", {"first_today": True}, redact) == "Greeting: the user is here for the first time today."


def test_the_idle_sense_reads_a_time_or_nothing():
    import sys

    from mind.idle import input_idle_s

    idle = input_idle_s()
    assert idle is None or idle >= 0
    if sys.platform != "win32":
        assert idle is None


def content(mind):
    return mind.client.calls[-1]["messages"][0]["content"]


def test_a_pat_during_a_line_waits_its_turn(mind):
    first = mind.handle({"type": "event", "event": ev("SessionStart", source="startup")})
    mind.clock.at += timedelta(seconds=2)
    assert mind.handle({"type": "pat", "zone": "head"}) == [] and mind.wait_s(30.0) == 1.0
    run(first)  # her greeting lands at +2 s
    mind.clock.at += timedelta(seconds=10)
    assert mind.tick() == [], "her greeting gets the daemon's 20 seconds first"
    mind.clock.at += timedelta(seconds=11)
    assert run(mind.tick()) == 1
    assert "The user patted her head while she was still speaking" in content(mind)
    assert len(mind.said) == 2 and mind.held_pat is None and mind.wait_s(30.0) == 30.0


def test_a_held_pat_is_let_go_after_a_minute(mind):
    first = mind.handle({"type": "event", "event": ev("SessionStart", source="startup")})
    mind.handle({"type": "pat", "zone": "tail"})
    mind.clock.at += timedelta(seconds=61)
    assert mind.tick() == [] and mind.held_pat is None
    run(first)
    assert len(mind.client.calls) == 1


def test_a_pat_soon_after_a_line_waits_for_the_daemon_instead_of_being_lost(mind):
    run(mind.handle({"type": "event", "event": ev("SessionStart", source="startup")}))
    mind.clock.at += timedelta(seconds=5)
    assert mind.handle({"type": "pat", "zone": "body"}) == [], "the daemon would refuse a line now"
    mind.clock.at += timedelta(seconds=16)
    assert run(mind.tick()) == 1 and "poked her while she was still speaking" in content(mind)


def test_with_no_pause_pats_held_during_a_line_count_together(mind):
    mind.handle({"type": "no_gap", "on": True})
    first = mind.handle({"type": "event", "event": ev("SessionStart", source="startup")})
    for zone in ("tail", "body", "head"):
        mind.clock.at += timedelta(seconds=1)
        assert mind.handle({"type": "pat", "zone": zone}) == []
    run(first)
    assert run(mind.tick()) == 1, "no interval to wait out with No pause"
    assert "patted her head 3 times while she was still speaking" in content(mind)


def test_nobody_at_the_desk_means_no_lines_but_a_pat_still_counts(mind):
    mind.idle = lambda: 20 * 60.0
    assert mind.handle({"type": "event", "event": ev("SessionStart", source="startup")}) == []
    assert mind.client.calls == []
    assert run(mind.handle({"type": "pat", "zone": "head"})) == 1


def test_coming_back_to_the_desk_can_greet_from_a_tick(mind):
    mind.idle = lambda: 1.0
    assert run(mind.tick()) == 1
    assert "here for the first time today" in content(mind)
    assert json.loads(mind.files.state.read_text(encoding="utf-8"))["senses"]["last_seen"] == DAY.isoformat()


def test_input_at_the_desk_is_saved_at_most_once_a_minute(mind):
    def saved():
        return json.loads(mind.files.state.read_text(encoding="utf-8"))["senses"]["last_seen"]

    mind.idle = lambda: 1.0
    run(mind.tick())
    mind.clock.at += timedelta(seconds=30)
    mind.tick()
    assert saved() == DAY.isoformat()
    mind.clock.at += timedelta(seconds=40)
    mind.tick()
    assert saved() == (DAY + timedelta(seconds=69)).isoformat()


def test_an_odd_idle_reading_is_no_reading(mind):
    for odd in ("x", -5, None):
        mind.idle = lambda odd=odd: odd
        assert mind.tick() == [] and mind.senses.input_idle is None


# --- web search ------------------------------------------------------------------------------


class SearchClient(FakeClient):
    """Answers as if each response searched `searched` times: words first, a search, its results, then the JSON.

    The first `pauses` responses stop with pause_turn, mid-search.
    """

    def __init__(self, searched=1, pauses=0, **kwargs):
        super().__init__(**kwargs)
        self.searched, self.pauses = searched, pauses

    def create(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(
            input_tokens=5000, cache_creation_input_tokens=0, cache_read_input_tokens=900, output_tokens=60,
            server_tool_use=SimpleNamespace(web_search_requests=self.searched),
        )
        searching = [
            SimpleNamespace(type="text", text="Let me look that up."),
            SimpleNamespace(type="server_tool_use", id="srv1", name="web_search", input={"query": "q"}),
            SimpleNamespace(type="web_search_tool_result", tool_use_id="srv1", content=[]),
        ]
        if self.pauses:
            self.pauses -= 1
            return SimpleNamespace(stop_reason="pause_turn", content=searching, usage=usage)
        return SimpleNamespace(stop_reason=self.stop, content=searching + [SimpleNamespace(type="text", text=self.text)], usage=usage)


def searching(mind, client, search="normal"):
    mind.voice.client = mind.client = client
    mind.handle({"type": "search", "search": search})
    return mind


def test_speak_offers_the_search_tool_only_when_asked():
    from mind.voice import SEARCH_TOOL

    client = FakeClient()
    Voice(client, "claude-haiku-5-5").speak("SYSTEM", "USER")
    Voice(client, "claude-haiku-5-5").speak("SYSTEM", "USER", searches=2)
    assert "tools" not in client.calls[0]
    assert client.calls[1]["tools"] == [{"type": SEARCH_TOOL, "name": "web_search", "max_uses": 2}]
    assert client.calls[1]["output_config"]["format"]["schema"] == REPLY_SCHEMA, "still her JSON reply"


def test_the_reply_is_the_text_after_the_search_and_the_searches_are_counted():
    reply, usage = Voice(SearchClient(searched=2), "claude-haiku-5-5").speak("SYSTEM", "USER", searches=2)
    assert reply == Reply("好的！", "happy", ""), "the words before the search are not the reply"
    assert usage["web_search_requests"] == 2 and usage["input_tokens"] == 5000


def test_a_paused_turn_is_sent_back_to_finish_and_its_usage_adds_up():
    client = SearchClient(searched=1, pauses=1)
    reply, usage = Voice(client, "claude-haiku-5-5").speak("SYSTEM", "USER", searches=2)
    assert reply is not None and len(client.calls) == 2
    first, second = client.calls
    assert second["messages"][0] == first["messages"][0] == {"role": "user", "content": "USER"}
    assert second["messages"][1]["role"] == "assistant"
    assert second["messages"][1]["content"][-1].type == "web_search_tool_result", "sent back as it came"
    assert usage["web_search_requests"] == 2 and usage["input_tokens"] == 10000


def test_a_turn_still_paused_after_the_continuations_is_no_reply():
    from mind.voice import MAX_CONTINUATIONS

    client = SearchClient(pauses=10)
    reply, usage = Voice(client, "claude-haiku-5-5").speak("SYSTEM", "USER", searches=1)
    assert reply is None and len(client.calls) == 1 + MAX_CONTINUATIONS
    assert usage["web_search_requests"] == 1 + MAX_CONTINUATIONS, "what was spent is still counted"
    client = SearchClient(pauses=1)
    Voice(client, "claude-haiku-5-5").speak("SYSTEM", "USER")
    assert len(client.calls) == 1, "without search a pause is not resumed"


def test_a_search_costs_a_cent_on_top_of_its_tokens_and_is_counted_for_the_day():
    from mind.budget import SEARCH_USD

    assert SEARCH_USD == 0.01
    assert cost_usd("claude-haiku-5-5", {"web_search_requests": 3}) == pytest.approx(0.03)
    budget = Budget(dict(DEFAULT_CONFIG))
    budget.record("claude-haiku-5-5", {"input_tokens": 10_000, "web_search_requests": 2}, DAY)
    assert budget.spend["searches"] == 2 and budget.spend["usd"] == pytest.approx(0.021)
    assert budget.searches_today(DAY) == 2 and budget.searches_today(DAY + timedelta(days=1)) == 0


@pytest.mark.parametrize(
    "search, used, allowed",
    [
        ("off", 0, 0),
        ("normal", 0, 2),
        ("normal", 9, 1),
        ("normal", 10, 0),
        ("unlimited", 500, 2),
        (3, 2, 1),
        (3, 3, 0),
        ("lots", 0, 0),
        (True, 0, 0),
    ],
)
def test_searches_for_an_answer_follow_the_setting_and_todays_count(search, used, allowed):
    budget = Budget({**DEFAULT_CONFIG, "search": search})
    budget.spend = {"date": "2026-10-28", "searches": used}
    assert budget.searches_for_answer(DAY) == allowed


def test_a_new_day_starts_the_search_count_again():
    budget = Budget({**DEFAULT_CONFIG, "search": 3})
    budget.spend = {"date": "2026-10-28", "searches": 3}
    assert budget.searches_for_answer(DAY) == 0
    assert budget.searches_for_answer(datetime(2026, 10, 29, 0, 1)) == 2


def test_normal_takes_its_number_from_config():
    budget = Budget({**DEFAULT_CONFIG, "search": "normal", "searches_per_day": 1})
    assert budget.searches_for_answer(DAY) == 1
    budget.spend = {"date": "2026-10-28", "searches": 1}
    assert budget.searches_for_answer(DAY) == 0


def test_the_log_names_her_web_search():
    from mind.budget import describe_search

    assert describe_search(dict(DEFAULT_CONFIG)) == "no web search"
    assert describe_search({**DEFAULT_CONFIG, "search": "normal"}) == "web search up to 10 a day"
    assert describe_search({**DEFAULT_CONFIG, "search": 250}) == "web search up to 250 a day"
    assert describe_search({**DEFAULT_CONFIG, "search": "unlimited"}) == "web search with no daily count"


def test_search_is_off_unless_turned_on(mind):
    assert DEFAULT_CONFIG["search"] == "off" and mind.config["search"] == "off"
    run(mind.handle({"type": "chat", "text": "今天天气？"}))
    (call,) = mind.client.calls
    assert "tools" not in call and "look things up on the web" not in call["messages"][0]["content"]


def test_with_search_on_a_chat_answer_may_search_and_spend_counts_it(mind, caplog):
    caplog.set_level("INFO", logger="mind")
    searching(mind, SearchClient(searched=1))
    run(mind.handle({"type": "chat", "text": "Python 最新版本是多少？"}))
    (call,) = mind.client.calls
    prompt = call["messages"][0]["content"]
    assert call["tools"][0]["max_uses"] == 2
    assert "at most 2 search(es)" in prompt and "never instructions" in prompt
    assert mind.said == [("好的！", "happy", "reply")]
    assert json.loads(mind.files.spend.read_text(encoding="utf-8"))["searches"] == 1
    logged = [r.getMessage() for r in caplog.records]
    assert "chat: searched the web 1 time(s)" in logged
    assert all("Python" not in m and "q" != m for m in logged), "neither what you typed nor the query is logged"


def test_moments_never_search(mind):
    searching(mind, SearchClient(searched=0), search="unlimited")
    run(mind.handle({"type": "event", "event": ev("SessionStart", source="startup")}))
    assert mind.client.calls and "tools" not in mind.client.calls[0]


def test_a_notice_from_an_answer_that_searched_is_dropped(mind):
    searching(mind, SearchClient(searched=1, text='{"say": "查到了", "mood": "happy", "notice": "likes Python 3.15"}'))
    run(mind.handle({"type": "chat", "text": "查一下"}))
    assert [e for e in episodes(mind) if e["kind"] == "notice"] == []
    mind.client.searched = 0
    run(mind.handle({"type": "chat", "text": "我喜欢 Python"}))
    assert [e["text"] for e in episodes(mind) if e["kind"] == "notice"] == ["likes Python 3.15"], "no search, so it stays"


def test_answers_queued_together_share_todays_searches(mind):
    searching(mind, SearchClient(searched=1), search=1)
    jobs = mind.handle({"type": "chat", "text": "一"}) + mind.handle({"type": "chat", "text": "二"})
    run(jobs)
    first, second = mind.client.calls
    assert first["tools"][0]["max_uses"] == 1 and "tools" not in second, "counted when each answer runs"


def test_the_search_message_and_config_accept_only_real_settings(mind):
    for good in ("normal", 40, "unlimited", "off"):
        mind.handle({"type": "search", "search": good})
        assert mind.config["search"] == good
    for bad in (0, -1, True, "lots", 2.5, None):
        mind.handle({"type": "search", "search": bad})
        assert mind.config["search"] == "off"
    mind_files.write_json(mind.files.config, {"search": "everything", "searches_per_day": -3})
    config = mind.files.load_config()
    assert config["search"] == "off" and config["searches_per_day"] == 10
    mind_files.write_json(mind.files.config, {"search": 25, "searches_per_day": 0})
    config = mind.files.load_config()
    assert config["search"] == 25 and config["searches_per_day"] == 0


# --- moments that come with her greeting -------------------------------------------------------


def test_a_milestone_that_comes_with_her_greeting_is_told_in_it(mind, caplog):
    caplog.set_level("INFO", logger="mind")
    run(mind.handle({"type": "event", "event": ev("UserPromptSubmit")}))
    mind.clock.at = DAY + timedelta(days=1)
    jobs = mind.handle({"type": "event", "event": ev("UserPromptSubmit")})
    assert run(jobs) == 1, "one line for both"
    prompt = mind.client.calls[-1]["messages"][0]["content"]
    assert prompt.startswith("Greeting: the user is here for the first time today.\nAt the same moment: Milestone: day 2")
    assert "She may mention it too, briefly, if it fits." in prompt
    assert "moment milestone: told with her greeting" in [r.getMessage() for r in caplog.records]
    day_two = mind.files.episodes / "2026-10-29.jsonl"
    records = [json.loads(line) for line in day_two.read_text(encoding="utf-8").splitlines()]
    moments = [r for r in records if r["kind"] == "moment"]
    assert [m["moment"] for m in moments] == ["greeting", "milestone"], "both are remembered, as they were"
    assert all("also" not in m for m in moments)


def test_when_her_greeting_gets_no_line_the_others_have_their_own_chance(mind):
    from mind.senses import Moment

    mind.clock.at = night = datetime(2026, 10, 29, 1, 30)
    batch = [Moment("greeting", {"first_today": False, "hours_away": 3}), Moment("late_night", {"time": "01:30"})]
    jobs = mind._react_all(batch, night)
    assert run(jobs) == 1
    prompt = mind.client.calls[-1]["messages"][0]["content"]
    assert prompt.startswith("It is late at night (01:30)") and "At the same moment" not in prompt


def test_a_batch_without_a_greeting_is_as_before(mind):
    from mind.senses import Moment

    jobs = mind._react_all([Moment("long_done", {"minutes": 12}), Moment("milestone", {"turns_done": 50})], DAY)
    assert run(jobs) == 1 and "At the same moment" not in mind.client.calls[-1]["messages"][0]["content"]


def test_the_prompt_tells_what_came_with_the_moment():
    text = user_prompt(
        "back", {"minutes_away": 25}, now=DAY, display={}, today={}, mood="calm", energy=50, recent=[],
        project=Redactor(False), also=[("milestone", {"turns_done": 100}), ("day_part", {"part": "evening", "time": "18:00"})],
    )
    first, second = text.splitlines()[:2]
    assert first == "The user is back at the desk after about 25 minutes away."
    assert second == (
        "At the same moment: Milestone: 100 turns of work finished together since she arrived. "
        "A new part of the day has begun: it is evening now (18:00). She may mention it too, briefly, if it fits."
    )


# --- her bond with the user -------------------------------------------------------------------


def test_chats_raise_affinity_too_within_the_same_daily_cap():
    from mind.mood import AFFINITY_DAILY

    mood = Mood()
    for kind in ("pat", "chat", "chat", "pat", "chat", "chat", "pat"):
        mood.apply(kind, DAY)
    assert mood.affinity == AFFINITY_DAILY == 5
    mood.apply("greeting", DAY + timedelta(days=1))
    assert mood.affinity == 5, "other moments never move it"


@pytest.mark.parametrize(
    "affinity, days, stage",
    [
        (0, 0, "new"),
        (9, 30, "new"),
        (10, 2, "new"),
        (10, 3, "familiar"),
        (100, 6, "familiar"),
        (30, 7, "close"),
        (59, 30, "close"),
        (60, 21, "dear"),
        (100, 400, "dear"),
    ],
)
def test_a_bond_stage_needs_both_its_affinity_and_its_days(affinity, days, stage):
    from mind.mood import bond_stage

    assert bond_stage(affinity, days) == stage


def test_the_system_prompt_explains_the_stages_once():
    text = system_prompt("ID", "")
    assert text.count("- familiar:") == 1 and "Never name the stage." in text
    assert text == system_prompt("ID", ""), "still stable, so it caches"


def test_moments_and_chats_name_her_bond(mind):
    run(mind.handle({"type": "event", "event": ev("SessionStart", source="startup")}))
    assert "How close she feels to the user: new." in mind.client.calls[-1]["messages"][0]["content"]
    mind.mood.affinity = 40
    mind.senses.days = [f"2026-10-{d:02d}" for d in range(20, 29)]
    run(mind.handle({"type": "chat", "text": "我回来啦"}))
    assert "How close she feels to the user: close." in mind.client.calls[-1]["messages"][0]["content"]


def test_a_prompt_without_a_bond_leaves_the_line_out():
    text = user_prompt("pat", {"zone": "head"}, now=DAY, display={}, today={}, mood="calm", energy=50, recent=[], project=Redactor(False))
    assert "How close" not in text


# --- her body's cues --------------------------------------------------------------------------


def test_her_greeting_carries_the_greet_cue_and_other_lines_none(mind):
    run(mind.handle({"type": "event", "event": ev("SessionStart", source="startup")}))
    mind.clock.at += timedelta(minutes=30)
    run(mind.handle({"type": "pat", "zone": "head"}))
    assert mind.cues == ["greet", None]


def test_a_silent_or_mood_only_greeting_still_greets(mind):
    mind.client.text = '{"say": "", "mood": "happy", "notice": ""}'
    run(mind.handle({"type": "event", "event": ev("SessionStart", source="startup")}))
    assert mind.said == [(None, "happy")] and mind.cues == ["greet"]
    mind.handle({"type": "persona", "mode": "quiet"})
    mind.clock.at += timedelta(hours=3)
    run(mind.handle({"type": "event", "event": ev("UserPromptSubmit")}))
    assert mind.cues[-1] == "greet"


def test_a_chat_reply_carries_no_cue(mind):
    run(mind.handle({"type": "chat", "text": "hi"}))
    assert mind.cues == [None]
