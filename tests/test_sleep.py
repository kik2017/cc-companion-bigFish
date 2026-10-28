"""Sleep: the day boundary, the timeline, facts, the four passes and the trigger. Never calls the network."""

import json
from datetime import date, datetime, timedelta

import pytest

from mind import facts as facts_mod
from mind.budget import Budget
from mind.core import Mind
from mind.files import DEFAULT_CONFIG, Files, read_json
from mind.senses import Senses, day_of, tool_label
from mind.sleep import PASSES, Sleep, grounded, timeline

DAY = date(2026, 10, 27)


def at(hhmm, day=DAY, next_day=False):
    h, m = map(int, hhmm.split(":"))
    return datetime(day.year, day.month, day.day, h, m) + (timedelta(days=1) if next_day else timedelta(0))


def write_log(files, calendar, records):
    files.episodes.mkdir(parents=True, exist_ok=True)
    with (files.episodes / f"{calendar}.jsonl").open("a", encoding="utf-8") as out:
        for record in records:
            out.write(json.dumps(record, ensure_ascii=False) + "\n")


def rec(when, kind, **fields):
    return {"t": when.isoformat(timespec="seconds"), "kind": kind, **fields}


def a_day(files):
    """Her 27 October: an evening session and one past midnight, filed by calendar date as older logs were."""
    write_log(files, "2026-10-27", [
        rec(at("21:05"), "event", event="SessionStart", source="startup", project="cc-companion", session="aaaa"),
        rec(at("21:05"), "moment", moment="greeting"),
        rec(at("21:06"), "said", text="晚上好～", mood="happy", moment="greeting"),
        rec(at("21:10"), "event", event="PreToolUse", tool_name="Edit", project="cc-companion"),
        rec(at("21:40"), "event", event="Stop", project="cc-companion"),
    ])
    write_log(files, "2026-10-28", [
        rec(at("02:45", next_day=True), "event", event="PreToolUse", tool_name="Bash", project="cc-companion"),
        rec(at("02:45", next_day=True), "moment", moment="late_night", time="02:45"),
        rec(at("02:45", next_day=True), "notice", text="works late at night", moment="late_night"),
        rec(at("13:08", next_day=True), "event", event="SessionStart", source="startup", project="side-project"),
    ])


@pytest.fixture
def files(isolated_app_dir):
    built = Files()
    built.setup()
    return built


# --- the day boundary ----------------------------------------------------------------------


def test_her_day_turns_over_at_five():
    assert day_of(datetime(2026, 10, 28, 2, 45)) == date(2026, 10, 27)
    assert day_of(datetime(2026, 10, 28, 5, 0)) == date(2026, 10, 28)


def test_back_after_sleeping_is_a_new_day_with_a_greeting():
    senses = Senses()
    senses.on_event({"hook_event_name": "UserPromptSubmit", "session_id": "s"}, datetime(2026, 10, 28, 2, 45))
    moments = senses.on_event({"hook_event_name": "UserPromptSubmit", "session_id": "s"}, datetime(2026, 10, 28, 13, 8))
    assert moments[0].kind == "greeting" and moments[0].detail["first_today"] is True
    assert senses.summary()["days_together"] == 2


def test_a_morning_greeting_is_allowed_in_quiet_hours():
    budget = Budget(dict(DEFAULT_CONFIG))
    assert budget.why_not("greeting", datetime(2026, 10, 28, 6, 30), waiting=False) is None
    assert budget.why_not("greeting", datetime(2026, 10, 28, 3, 0), waiting=False) == "quiet hours"


def test_day_records_join_calendar_files_and_episode_days_find_them(files):
    a_day(files)
    records = files.day_records(DAY)
    assert [r["t"][11:16] for r in records] == ["21:05", "21:05", "21:06", "21:10", "21:40", "02:45", "02:45", "02:45"]
    assert files.episode_days() == [DAY, date(2026, 10, 28)]


def test_new_episodes_are_filed_by_her_day(files):
    files.append_episode(datetime(2026, 10, 28, 2, 45), "moment", moment="late_night")
    assert (files.episodes / "2026-10-27.jsonl").exists()


# --- the timeline ----------------------------------------------------------------------------


def test_timeline_counts_events_and_lists_her_lines(files):
    a_day(files)
    text, names = timeline(DAY, files.day_records(DAY))
    assert "Active from 21:05 to 02:45." in text
    assert "1 session(s) started; 1 turn(s) of work done; 0 error(s)." in text
    assert "21:00-21:30 working in cc-companion: session started, Edit" in text
    assert '21:06 she said: "晚上好～" (happy)' in text
    assert "02:45 she noted: works late at night" in text
    assert names == {"cc-companion", "Edit", "Bash"}
    assert "side-project" not in text  # 13:08 belongs to the next day


@pytest.mark.parametrize(
    "name, label",
    [
        ("Edit", "Edit"),
        ("mcp__ccd_turn__wrap_up", None),
        ("mcp__ccd_session__start_session", None),
        ("mcp__visualize__show_widget", None),
        ("mcp__Claude_Browser__navigate", "browser"),
        ("mcp__claude-in-chrome__computer", "browser"),
        ("mcp__1a59c906-04da-521d-bda7-7f71b9f9e01c__batch", "a connector"),
        ("mcp__slack__post_message", "slack"),
        ("", None),
        (None, None),
    ],
)
def test_tool_labels(name, label):
    assert tool_label(name) == label


def test_the_timeline_groups_mcp_tools_and_drops_app_plumbing(files):
    write_log(files, "2026-10-27", [
        rec(at("21:00"), "event", event="PreToolUse", tool_name="mcp__ccd_turn__wrap_up", project="x"),
        rec(at("21:01"), "event", event="PreToolUse", tool_name="mcp__Claude_Browser__navigate", project="x"),
        rec(at("21:02"), "event", event="PreToolUse", tool_name="mcp__Claude_Browser__find", project="x"),
        rec(at("21:03"), "event", event="PreToolUse", tool_name="Edit", project="x"),
    ])
    text, names = timeline(DAY, files.day_records(DAY))
    assert "mcp__" not in text and "ccd" not in text
    assert "Tools Claude used: browser x2, Edit x1" in text
    assert names == {"x", "browser", "Edit"}


def test_her_line_prompt_never_sees_mcp_names():
    senses = Senses()
    for tool in ("mcp__ccd_turn__update_plan",) * 5 + ("Bash", "mcp__claude-in-chrome__navigate"):
        senses.on_event({"hook_event_name": "PreToolUse", "session_id": "s", "tool_name": tool}, datetime(2026, 10, 28, 10, 0))
    assert senses.summary()["tools"] == ["Bash", "browser"]
    senses.today["tools"]["mcp__ccd_turn__wrap_up"] = 9  # a raw name from older state
    assert "mcp__ccd_turn__wrap_up" not in senses.summary()["tools"]


def test_grounded_rejects_times_and_names_the_timeline_lacks(files):
    a_day(files)
    text, names = timeline(DAY, files.day_records(DAY))
    assert grounded("21:05 我们开始了 cc-companion，凌晨 02:45 还在。", text, names) is None
    assert grounded("十点 22:15 才睡。", text, names) == "time 22:15"
    assert grounded("今天在 secret-project 里忙。", text, names) == "name secret-project"


# --- facts -----------------------------------------------------------------------------------


def test_facts_are_added_reinforced_once_a_day_and_known_after_two_days():
    doc, changes = facts_mod.apply_ops(facts_mod.blank(), [{"op": "add", "text": "works late", "ids": [], "reason": ""}], "2026-10-27")
    assert changes == ["learned f1: works late"] and facts_mod.known(doc) == []
    reinforce = [{"op": "reinforce", "text": "", "ids": ["f1"], "reason": ""}]
    doc, _ = facts_mod.apply_ops(doc, reinforce, "2026-10-27")
    assert doc["facts"][0]["seen"] == 1, "once per day"
    doc, _ = facts_mod.apply_ops(doc, reinforce, "2026-10-28")
    assert facts_mod.known(doc) == ["works late"]


def test_merge_and_archive_never_delete():
    doc, _ = facts_mod.apply_ops(facts_mod.blank(), [
        {"op": "add", "text": "works late", "ids": [], "reason": ""},
        {"op": "add", "text": "up past midnight", "ids": [], "reason": ""},
        {"op": "add", "text": "uses Bash a lot", "ids": [], "reason": ""},
    ], "2026-10-27")
    doc, changes = facts_mod.apply_ops(doc, [
        {"op": "merge", "text": "often works past midnight", "ids": ["f1", "f2"], "reason": ""},
        {"op": "archive", "text": "", "ids": ["f3", "nope"], "reason": "one day only"},
    ], "2026-10-28")
    assert len(doc["facts"]) == 4
    assert [f["text"] for f in facts_mod.active(doc)] == ["often works past midnight"]
    merged = facts_mod.active(doc)[0]
    assert merged["days"] == ["2026-10-27", "2026-10-28"] and merged["seen"] == 2
    assert {f["id"]: f["reason"] for f in doc["facts"] if f["archived"]} == {"f1": "merged into f4", "f2": "merged into f4", "f3": "one day only"}


def test_the_least_reinforced_go_first_past_the_cap(monkeypatch):
    doc, _ = facts_mod.apply_ops(facts_mod.blank(), [{"op": "add", "text": t, "ids": [], "reason": ""} for t in "abc"], "2026-10-27")
    monkeypatch.setattr(facts_mod, "MAX_ACTIVE", 2)
    doc, _ = facts_mod.apply_ops(doc, [{"op": "reinforce", "text": "", "ids": ["f1", "f3"], "reason": ""}], "2026-10-28")
    assert [f["text"] for f in facts_mod.active(doc)] == ["a", "c"]


def test_a_fact_the_user_told_her_is_known_after_one_night():
    told = {"op": "add", "text": "has a cat called Mochi", "ids": [], "reason": "", "told": True}
    doc, changes = facts_mod.apply_ops(facts_mod.blank(), [told], "2026-10-28", user_spoke=True)
    assert changes == ["learned f1 (you told her): has a cat called Mochi"]
    assert facts_mod.known(doc) == ["has a cat called Mochi"]


def test_told_counts_only_on_a_day_the_user_typed_to_her():
    told = {"op": "add", "text": "has a cat called Mochi", "ids": [], "reason": "", "told": True}
    doc, changes = facts_mod.apply_ops(facts_mod.blank(), [told], "2026-10-28")
    assert changes == ["learned f1: has a cat called Mochi"] and facts_mod.known(doc) == []
    doc, _ = facts_mod.apply_ops(facts_mod.blank(), [{**told, "told": "yes"}], "2026-10-28", user_spoke=True)
    assert facts_mod.known(doc) == [], "only a real true counts"


def test_reinforcing_with_told_and_merging_keep_her_word():
    doc, _ = facts_mod.apply_ops(facts_mod.blank(), [
        {"op": "add", "text": "works late", "ids": [], "reason": ""},
        {"op": "add", "text": "likes cats", "ids": [], "reason": ""},
    ], "2026-10-27")
    doc, changes = facts_mod.apply_ops(
        doc, [{"op": "reinforce", "text": "", "ids": ["f2"], "reason": "", "told": True}], "2026-10-28", user_spoke=True
    )
    assert "you told her f2: likes cats" in changes and facts_mod.active(doc)[1]["told"]
    doc, _ = facts_mod.apply_ops(
        doc, [{"op": "merge", "text": "loves cats, works late", "ids": ["f1", "f2"], "reason": "", "told": False}], "2026-10-29"
    )
    assert facts_mod.active(doc)[0]["told"], "a merge keeps what the user told her"


def test_known_facts_put_what_the_user_said_first():
    doc = facts_mod.clean({"facts": [
        {"id": "f1", "text": "often works past midnight", "days": ["2026-10-26", "2026-10-27", "2026-10-28"], "seen": 3},
        {"id": "f2", "text": "has a cat called Mochi", "days": ["2026-10-28"], "seen": 1, "told": True},
        {"id": "f3", "text": "uses Bash", "days": ["2026-10-28"], "seen": 1},
    ]})
    assert facts_mod.known(doc) == ["has a cat called Mochi", "often works past midnight"]
    assert doc["facts"][0]["told"] is False, "old facts without the flag were not told"


def test_malformed_ops_and_documents_are_survived():
    doc, changes = facts_mod.apply_ops({"facts": "x"}, [None, {"op": "explode"}, {"op": "add", "text": "  "}], "2026-10-27")
    assert doc == facts_mod.blank() and changes == []


# --- the four passes -------------------------------------------------------------------------


class FakeVoice:
    """Answers each pass by its schema; `fail` names passes that get nothing back."""

    def __init__(self, diary="晚上 21:05 开始，凌晨 02:45 还在 cc-companion 里。我有点困，但很开心。", fail=()):
        self.diary, self.fail, self.calls = diary, set(fail), []

    def ask(self, system, user, schema, *, effort, max_tokens):
        name = next(iter(schema["properties"]))
        self.calls.append((name, system, user, effort))
        if {"entry": "diary", "ops": "consolidate", "self": "self", "dream": "dream"}[name] in self.fail:
            return None, {}
        reply = {
            "entry": {"entry": self.diary},
            "ops": {"ops": [{"op": "add", "text": "often works past midnight", "ids": [], "reason": ""}]},
            "self": {"self": "我是小汐。我发现你总是熬夜。", "change": "noticed the late nights"},
            "dream": {"dream": "我梦见尾巴变成了 Bash 的光标。"},
        }[name]
        return json.dumps(reply, ensure_ascii=False), {"input_tokens": 100, "output_tokens": 50}


def test_a_night_writes_diary_facts_self_and_dream(files):
    a_day(files)
    files.self_model.write_text("我是小汐。", encoding="utf-8")
    usages = []
    sleep = Sleep(files, FakeVoice(), usages.append)
    assert sleep.due_days(at("13:10", next_day=True)) == [DAY]
    result = sleep.run(DAY)
    assert result == {"diary": "ok", "consolidate": "ok", "self": "ok", "dream": "ok", "done": True}
    diary = (files.diary / "2026-10-27.md").read_text(encoding="utf-8")
    assert diary.startswith("# 2026-10-27\n\n晚上 21:05") and "## Dream\n\n我梦见尾巴" in diary
    assert facts_mod.active(read_json(files.facts))[0]["text"] == "often works past midnight"
    assert files.self_model.read_text(encoding="utf-8") == "我是小汐。我发现你总是熬夜。\n"
    assert "我是小汐。" in (files.self_history / "2026-10-27.md").read_text(encoding="utf-8")
    assert (files.backups / "2026-10-27" / "self.md").read_text(encoding="utf-8") == "我是小汐。"
    assert len(usages) == 4
    assert sleep.due_days(at("13:10", next_day=True)) == []
    assert read_json(files.sleep)["dream_to_tell"]["text"].startswith("我梦见")


class ToldVoice(FakeVoice):
    """Consolidate answers with one fact marked told; the other passes as FakeVoice."""

    def ask(self, system, user, schema, *, effort, max_tokens):
        if "ops" in schema["properties"]:
            self.calls.append(("ops", system, user, effort))
            ops = [{"op": "add", "text": "has a cat called Mochi", "ids": [], "reason": "", "told": True}]
            return json.dumps({"ops": ops}), {"input_tokens": 100, "output_tokens": 50}
        return super().ask(system, user, schema, effort=effort, max_tokens=max_tokens)


def test_a_night_after_a_chat_knows_what_the_user_said(files):
    a_day(files)
    write_log(files, "2026-10-27", [
        rec(at("21:20"), "chat", **{"from": "user", "text": "我家猫叫 Mochi"}),
        rec(at("21:20"), "chat", **{"from": "her", "text": "好可爱～", "mood": "happy"}),
    ])
    voice = ToldVoice()
    Sleep(files, voice).run(DAY)
    consolidate = next(user for name, _, user, _ in voice.calls if name == "ops")
    assert 'the user said to her: "我家猫叫 Mochi"' in consolidate and 'Set "told" to true only' in consolidate
    assert facts_mod.known(facts_mod.clean(read_json(files.facts))) == ["has a cat called Mochi"]


def test_an_archive_made_while_she_consolidates_is_not_undone(files):
    a_day(files)
    files.facts.write_text(json.dumps({"next_id": 2, "facts": [
        {"id": "f1", "text": "uses Bash a lot", "days": ["2026-10-26"], "seen": 1},
    ]}), encoding="utf-8")

    class ArchivingVoice(FakeVoice):
        def ask(self, system, user, schema, *, effort, max_tokens):
            if "ops" in schema["properties"]:  # you archive f1 in What she knows while the model thinks
                doc = read_json(files.facts)
                doc["facts"][0].update(archived=True, reason="archived by you")
                files.facts.write_text(json.dumps(doc), encoding="utf-8")
            return super().ask(system, user, schema, effort=effort, max_tokens=max_tokens)

    Sleep(files, ArchivingVoice()).run(DAY)
    facts = {f["id"]: f for f in read_json(files.facts)["facts"]}
    assert facts["f1"]["archived"] and facts["f1"]["reason"] == "archived by you"
    assert facts["f2"]["text"] == "often works past midnight", "tonight's own fact still lands"


def test_a_night_with_no_chat_never_marks_a_fact_told(files):
    a_day(files)
    Sleep(files, ToldVoice()).run(DAY)
    assert facts_mod.known(facts_mod.clean(read_json(files.facts))) == []


def test_every_pass_shares_one_cacheable_system_prompt_with_her_identity(files):
    a_day(files)
    voice = FakeVoice()
    Sleep(files, voice).run(DAY)
    systems = {system for _, system, _, _ in voice.calls}
    assert len(systems) == 1 and "汐" in systems.pop()
    assert all(effort == "medium" for *_, effort in voice.calls)


def test_an_ungrounded_diary_is_retried_then_given_up(files):
    a_day(files)
    voice = FakeVoice(diary="23:59 我们在 secret-project 里。")
    result = Sleep(files, voice).run(DAY)
    assert result["diary"] == "failed" and result["self"] == "ok"
    assert [name for name, *_ in voice.calls].count("entry") == 2
    assert "rejected (time 23:59)" in voice.calls[1][2]


def test_an_interrupted_night_resumes_where_it_stopped(files):
    a_day(files)
    Sleep(files, FakeVoice(fail={"self", "dream"})).run(DAY)
    assert read_json(files.sleep)["days"]["2026-10-27"]["self"] == "failed"
    files.sleep.write_text(json.dumps({"days": {"2026-10-27": {"diary": "ok", "consolidate": "ok"}}}), encoding="utf-8")
    voice = FakeVoice()
    Sleep(files, voice).run(DAY)
    assert [name for name, *_ in voice.calls] == ["self", "dream"]


def test_a_dry_run_writes_nothing_and_prints_what_it_would(files):
    a_day(files)
    before = sorted(p.relative_to(files.root) for p in files.root.rglob("*"))
    printed = []
    Sleep(files, FakeVoice(), dry_run=True, out=printed.append).run(DAY)
    assert sorted(p.relative_to(files.root) for p in files.root.rglob("*")) == before
    text = "\n".join(printed)
    assert "would write diary" in text and "would write facts.json" in text and "would write self.md" in text


def test_backups_keep_the_newest_fourteen(files):
    a_day(files)
    for n in range(20):
        (files.backups / f"2026-09-{n + 1:02d}").mkdir(parents=True)
    Sleep(files, FakeVoice()).run(DAY)
    kept = sorted(p.name for p in files.backups.iterdir())
    assert len(kept) == 14 and kept[-1] == "2026-10-27"


def test_today_is_never_slept(files):
    a_day(files)
    assert date(2026, 10, 28) not in Sleep(files, FakeVoice()).due_days(at("23:00", next_day=True))


# --- the trigger in the mind -------------------------------------------------------------------


class Clock:
    def __init__(self, when):
        self.when = when

    def __call__(self):
        return self.when


def test_the_mind_sleeps_when_it_starts_and_tells_the_dream_in_the_morning(files):
    a_day(files)
    said, clock = [], Clock(at("13:10", next_day=True))
    voice = FakeVoice()

    def speak(system, user):
        voice.calls.append(("say", system, user, "low"))
        return None, {}

    voice.speak = speak
    mind = Mind(files, files.load_config(), voice, lambda text, mood: said.append((text, mood)) or 204, now=clock)
    jobs = mind.tick()
    assert len(jobs) == 1 and mind.busy
    jobs[0]()
    assert not mind.busy and said[0] == (None, "sleepy")
    assert read_json(files.sleep)["days"]["2026-10-27"]["done"] is True
    assert mind.tick() == [], "nothing left to sleep on"
    for job in mind.handle({"type": "event", "event": {"hook_event_name": "SessionStart", "session_id": "s", "source": "startup"}}):
        job()
    greeting = voice.calls[-1]
    assert greeting[0] == "say" and "Last night she dreamt this" in greeting[2] and "我梦见尾巴" in greeting[2]
    assert "<self>" in greeting[1]
    assert "dream_to_tell" not in read_json(files.sleep)


def test_the_mind_waits_for_you_to_be_away_before_sleeping(files):
    a_day(files)
    clock = Clock(at("13:10", next_day=True))
    mind = Mind(files, files.load_config(), FakeVoice(), lambda text, mood: 204, now=clock)
    mind.just_started = False
    mind.senses.last_activity = clock.when - timedelta(minutes=5)
    assert mind.tick() == []
    clock.when += timedelta(minutes=40)
    assert len(mind.tick()) == 1


def test_known_facts_reach_her_lines(files):
    facts_mod_doc = {"next_id": 2, "facts": [{"id": "f1", "text": "often works past midnight", "days": ["2026-10-26", "2026-10-27"], "seen": 2}]}
    files.facts.write_text(json.dumps(facts_mod_doc), encoding="utf-8")
    seen = []
    voice = FakeVoice()
    voice.speak = lambda system, user: seen.append(system) or (None, {})
    mind = Mind(files, files.load_config(), voice, lambda text, mood: 204, now=Clock(at("13:10", next_day=True)))
    for job in mind.handle({"type": "pat", "zone": "head"}):
        job()
    assert "<known>" in seen[0] and "- often works past midnight" in seen[0]


def test_passes_are_in_order():
    assert PASSES == ("diary", "consolidate", "self", "dream")
