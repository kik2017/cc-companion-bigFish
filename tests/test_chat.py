"""Talking to her: the chat box, its menu items, where it sits, Private today, and chat in her diary.

Window checks park every window at (-32000, -32000) and inject a screen area far off
screen, so the chat box never shows on the desktop, and never take the keyboard focus.
"""

import json
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

from companion import settings as prefs
from companion.placement import MARGIN, Rect, place_beside
from mind.sleep import timeline

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="window checks are Windows-only")

AREA = Rect(0, 0, 1920, 1040)


# --- where the box sits ------------------------------------------------------------------


def test_the_box_goes_left_of_the_pet_level_with_its_lower_half():
    pet = Rect(1500, 700, 200, 300)
    box = place_beside(pet, (280, 60), AREA)
    assert box.right <= pet.x and box.y == 700 + 225 - 30


def test_the_box_goes_right_when_the_pet_is_at_the_left_edge():
    box = place_beside(Rect(20, 700, 200, 300), (280, 60), AREA)
    assert box.x >= 220


def test_the_box_stays_inside_the_area():
    box = place_beside(Rect(100, 990, 1800, 300), (280, 60), AREA)
    assert AREA.x + MARGIN <= box.x and box.right <= AREA.right - MARGIN
    assert box.bottom <= AREA.bottom - MARGIN


# --- Private today in settings -------------------------------------------------------------


def test_her_day_turns_over_at_five():
    assert prefs.her_day(datetime(2026, 10, 29, 4, 59)) == "2026-10-28"
    assert prefs.her_day(datetime(2026, 10, 29, 5, 0)) == "2026-10-29"


def test_private_day_is_saved_beside_her_other_knobs(isolated_app_dir):
    assert prefs.read_private_day() is None
    prefs.persona_dir().mkdir(parents=True)
    prefs.persona_config_path().write_text('{"model": "claude-haiku-5-5", "chattiness": "chatty"}', encoding="utf-8")
    assert prefs.write_private_day("2026-10-28")
    raw = json.loads(prefs.persona_config_path().read_text(encoding="utf-8"))
    assert raw == {"model": "claude-haiku-5-5", "chattiness": "chatty", "private_day": "2026-10-28"}
    assert prefs.read_private_day() == "2026-10-28"
    assert prefs.write_private_day(None) and prefs.read_private_day() is None
    assert prefs.read_chattiness() == "chatty"


# --- chat in her diary ---------------------------------------------------------------------


def test_the_chat_is_in_her_days_timeline():
    records = [
        {"t": "2026-10-28T14:00:00", "kind": "chat", "from": "user", "text": "我家猫叫 Mochi"},
        {"t": "2026-10-28T14:00:03", "kind": "chat", "from": "her", "text": "好可爱的名字～", "mood": "happy"},
    ]
    text, _ = timeline(date(2026, 10, 28), records)
    assert '14:00 the user said to her: "我家猫叫 Mochi"' in text
    assert '14:00 she answered: "好可爱的名字～" (happy)' in text


# --- the box and its menu items -------------------------------------------------------------


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app):
    from PySide6.QtCore import QPoint, QRect

    from companion import skin as skins
    from companion.window import CompanionWindow

    created = []

    def make(persona="on"):
        w = CompanionWindow(prefs.Settings(persona=persona), skin=skins.load_skin(Path(__file__).parent / "fixtures" / "placeholder-skin"))
        w.bubble.area_for = lambda rect: QRect(-32400, -32400, 3000, 2000)
        w.chat.activateWindow = lambda: None  # never take the keyboard from the desktop
        w.move(QPoint(-32000, -32000))
        created.append(w)
        return w

    yield make
    for w in created:
        w.close()
    app.processEvents()


def action(menu, name):
    from PySide6.QtGui import QAction

    found = menu.findChild(QAction, name)
    assert found is not None, name
    return found


@windows_only
def test_talk_to_her_is_offered_only_while_she_is_on(window):
    for persona, enabled in (("on", True), ("quiet", False), ("off", False)):
        w = window(persona)
        menu = w.build_menu()
        assert action(menu, "persona:talk").isEnabled() is enabled
        menu.deleteLater()


@windows_only
def test_the_box_opens_off_screen_beside_the_pet_and_sends_a_line(window):
    w = window()
    sent = []
    w.chat_sent.connect(sent.append)
    menu = w.build_menu()
    action(menu, "persona:talk").trigger()
    menu.deleteLater()
    assert w.chat.isVisible() and w.chat.x() < -30000 and w.chat.y() < -30000
    w.chat.edit.setText("  你好   呀 ")
    w.chat.edit.returnPressed.emit()
    assert sent == ["你好 呀"]
    assert w.chat.waiting and w.chat.edit.isReadOnly() and w.chat.edit.text() == ""
    w.chat.edit.setText("again")
    w.chat.edit.returnPressed.emit()
    assert sent == ["你好 呀"], "nothing more is sent while she is answering"


@windows_only
def test_her_reply_ends_the_wait(window):
    from companion.chat import HINT, WORDLESS

    w = window()
    w.open_chat()
    w.chat.edit.setText("hi")
    w.chat.edit.returnPressed.emit()
    w.say("你好～", "happy", 15.0, reply=True)
    assert not w.chat.waiting and w.chat.status.text() == HINT
    assert w.bubble.speech == "你好～"
    w.chat.edit.setText("嗯")
    w.chat.edit.returnPressed.emit()
    w.say(None, "curious", 8.0, reply=True)
    assert not w.chat.waiting and w.chat.status.text() == WORDLESS


@windows_only
def test_the_box_keeps_todays_conversation(window):
    w = window()
    w.open_chat()
    assert w.chat.log.isHidden(), "no history yet"
    small = w.chat.height()
    w.chat.edit.setText("我家猫叫 Mochi")
    w.chat.edit.returnPressed.emit()
    w.say("好可爱～", "happy", 15.0, reply=True)
    assert w.chat.shown_history() == [("you", "我家猫叫 Mochi"), ("her", "好可爱～")]
    assert not w.chat.log.isHidden() and w.chat.height() > small
    assert w.chat.x() < -30000 and w.chat.y() < -30000, "it grows in place, still off screen"


@windows_only
def test_a_reply_after_the_box_closed_still_reaches_the_history(window):
    w = window()
    w.open_chat()
    w.chat.edit.setText("hi")
    w.chat.edit.returnPressed.emit()
    w.chat.hide()
    w.say("你好～", "happy", 15.0, reply=True)
    assert w.chat.shown_history()[-1] == ("her", "你好～")


@windows_only
def test_history_text_is_shown_as_text_not_markup(window):
    w = window()
    w.open_chat()
    w.chat.edit.setText('<b>bold</b> & <a href="x">link</a>')
    w.chat.edit.returnPressed.emit()
    assert w.chat.log.toPlainText() == '<b>bold</b> & <a href="x">link</a>'


@windows_only
def test_history_starts_over_when_her_day_turns(app, window):
    from companion.chat import HISTORY_KEPT, ChatBox

    w = window()
    day = ["2026-10-28"]
    box = ChatBox(w, lambda rect: w.bubble.area_for(rect), today=lambda: day[0])
    for n in range(HISTORY_KEPT + 5):
        box.answered(f"line {n}")
    assert len(box.shown_history()) == HISTORY_KEPT and box.shown_history()[-1] == ("her", f"line {HISTORY_KEPT + 4}")
    day[0] = "2026-10-29"
    assert box.shown_history() == []
    box.answered("早")
    assert box.history == [("2026-10-29", "her", "早")]
    box.close()


@windows_only
def test_a_line_that_is_not_a_reply_does_not_end_the_wait(window):
    w = window()
    w.open_chat()
    w.chat.edit.setText("hi")
    w.chat.edit.returnPressed.emit()
    w.say("a moment line", "calm", 8.0)
    assert w.chat.waiting


@windows_only
def test_no_reply_times_out_and_says_so(window):
    from companion.chat import NO_ANSWER

    w = window()
    w.open_chat()
    w.chat.edit.setText("hi")
    w.chat.edit.returnPressed.emit()
    w.chat.reply_timer.timeout.emit()
    assert not w.chat.waiting and w.chat.status.text() == NO_ANSWER and not w.chat.edit.isReadOnly()


@windows_only
def test_escape_idle_and_persona_off_close_the_box(app, window):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    w = window()
    w.open_chat()
    app.sendEvent(w.chat, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    assert not w.chat.isVisible()
    w.open_chat()
    w.chat.idle_timer.timeout.emit()
    assert not w.chat.isVisible()
    w.open_chat()
    w.apply_setting("persona", "quiet")
    assert not w.chat.isVisible()
    w.open_chat()
    assert not w.chat.isVisible(), "no box while she is quiet"


@windows_only
def test_the_box_takes_at_most_300_characters(window):
    from companion.chat import CHAT_MAX_CHARS
    from mind.core import CHAT_MAX_CHARS as MIND_MAX

    w = window()
    assert w.chat.edit.maxLength() == CHAT_MAX_CHARS == MIND_MAX


@windows_only
def test_private_today_is_checked_from_her_config_and_toggles(window):
    w = window()
    changes = []
    w.private_changed.connect(changes.append)
    menu = w.build_menu()
    assert not action(menu, "persona:private").isChecked()
    action(menu, "persona:private").trigger()
    menu.deleteLater()
    assert changes == [prefs.her_day()] and prefs.read_private_day() == prefs.her_day()
    menu = w.build_menu()
    assert action(menu, "persona:private").isChecked()
    action(menu, "persona:private").trigger()
    menu.deleteLater()
    assert changes[-1] is None and prefs.read_private_day() is None


def test_no_pause_is_saved_beside_her_other_knobs(isolated_app_dir):
    assert prefs.read_no_gap() is False
    prefs.persona_dir().mkdir(parents=True)
    prefs.persona_config_path().write_text('{"chattiness": "chatty"}', encoding="utf-8")
    assert prefs.write_no_gap(True) and prefs.read_no_gap() is True
    assert json.loads(prefs.persona_config_path().read_text(encoding="utf-8")) == {"chattiness": "chatty", "no_gap": True}
    assert prefs.write_no_gap(False) and prefs.read_no_gap() is False


@windows_only
def test_no_pause_is_a_check_item_with_a_cost_warning(window):
    w = window()
    changes = []
    w.no_gap_changed.connect(changes.append)
    menu = w.build_menu()
    item = action(menu, "persona:no_gap")
    assert item.isCheckable() and not item.isChecked()
    assert "might incur high API cost" in item.toolTip()
    assert item.parent().toolTipsVisible(), "the Persona menu shows its tooltips"
    item.trigger()
    menu.deleteLater()
    assert changes == [True] and prefs.read_no_gap() is True
    menu = w.build_menu()
    assert action(menu, "persona:no_gap").isChecked()
    action(menu, "persona:no_gap").trigger()
    menu.deleteLater()
    assert changes == [True, False] and prefs.read_no_gap() is False


def test_the_limit_is_saved_beside_her_other_knobs(isolated_app_dir):
    assert prefs.read_limit() == "normal" and prefs.read_normal_limit() == (30, 0.5)
    prefs.persona_dir().mkdir(parents=True)
    prefs.persona_config_path().write_text('{"lines_per_hour": 40, "daily_usd": 1.25}', encoding="utf-8")
    assert prefs.read_normal_limit() == (40, 1.25)
    for good in ("unlimited", 1, prefs.MAX_DAILY_LINES, "normal"):
        assert prefs.write_limit(good) and prefs.read_limit() == good
    for bad in (0, prefs.MAX_DAILY_LINES + 1, True, "lots", 2.5):
        assert not prefs.write_limit(bad)
    assert json.loads(prefs.persona_config_path().read_text(encoding="utf-8"))["lines_per_hour"] == 40


@windows_only
def test_no_pause_is_named_no_pause_and_mentions_pats(window):
    from companion.window import NO_GAP_LABEL

    w = window()
    menu = w.build_menu()
    item = action(menu, "persona:no_gap")
    assert item.text() == NO_GAP_LABEL == "No pause"
    assert "no cooldown on pats" in item.toolTip()
    menu.deleteLater()


@windows_only
def test_the_limit_menu_offers_normal_unlimited_and_custom_with_cost_warnings(window):
    w = window()
    changes = []
    w.limit_changed.connect(changes.append)
    menu = w.build_menu()
    normal, unlimited, custom = (action(menu, f"limit:{k}") for k in ("normal", "unlimited", "custom"))
    assert normal.isChecked() and normal.text() == "Normal: 30 lines an hour, $0.50 a day"
    assert "API cost" not in normal.toolTip()
    for item in (unlimited, custom):
        assert "might incur high API cost" in item.toolTip()
    assert unlimited.parent().toolTipsVisible()
    unlimited.trigger()
    menu.deleteLater()
    assert changes == ["unlimited"] and prefs.read_limit() == "unlimited"
    menu = w.build_menu()
    assert action(menu, "limit:unlimited").isChecked()
    menu.deleteLater()


@windows_only
def test_custom_asks_for_lines_a_day_and_cancel_changes_nothing(window):
    w = window()
    asked = []
    w.ask_daily_lines = lambda start: asked.append(start) or None
    menu = w.build_menu()
    action(menu, "limit:custom").trigger()
    menu.deleteLater()
    assert asked == [300] and prefs.read_limit() == "normal"
    w.ask_daily_lines = lambda start: asked.append(start) or 1200
    menu = w.build_menu()
    action(menu, "limit:custom").trigger()
    menu.deleteLater()
    assert prefs.read_limit() == 1200
    menu = w.build_menu()
    custom = action(menu, "limit:custom")
    assert custom.isChecked() and custom.text() == "Custom: 1,200 lines a day…"
    custom.trigger()
    menu.deleteLater()
    assert asked[-1] == 1200, "the dialog starts from the current number"


# --- spend in the menu and What she knows ------------------------------------------------------

FACTS = {"next_id": 5, "facts": [
    {"id": "f1", "text": "often works past midnight", "days": ["2026-10-26", "2026-10-27"], "seen": 2},
    {"id": "f2", "text": "uses Bash a lot", "days": ["2026-10-28"], "seen": 1},
    {"id": "f3", "text": "has a cat called <b>Mochi</b>", "days": ["2026-10-28"], "seen": 1, "told": True},
    {"id": "f4", "text": "old guess", "days": ["2026-10-20"], "seen": 1, "archived": True, "reason": "stale"},
    {"text": "no id"},
]}


def write_persona(name, data):
    prefs.persona_dir().mkdir(parents=True, exist_ok=True)
    (prefs.persona_dir() / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_spend_today_is_read_from_her_spend_file(isolated_app_dir):
    assert prefs.read_spend_today() == (0, 0.0)
    write_persona("spend.json", {"date": "2026-10-28", "usd": 0.0123, "lines": 23, "calls": 31})
    assert prefs.read_spend_today(date(2026, 10, 28)) == (23, 0.0123)
    assert prefs.read_spend_today(date(2026, 10, 29)) == (0, 0.0), "another day's spend is not today's"
    write_persona("spend.json", {"date": "2026-10-28", "usd": 0.01})
    assert prefs.read_spend_today(date(2026, 10, 28)) == (0, 0.01), "spend from before lines were counted"


def test_facts_are_read_without_archived_or_malformed_ones(isolated_app_dir):
    write_persona("facts.json", FACTS)
    assert [f["id"] for f in prefs.read_facts()] == ["f1", "f2", "f3"]
    assert prefs.read_facts()[2]["told"] is True and prefs.read_facts()[0]["told"] is False


def test_archive_keeps_the_fact_and_undo_restores_only_your_own(isolated_app_dir):
    write_persona("facts.json", FACTS)
    assert prefs.set_fact_archived("f2", True)
    stored = {f.get("id"): f for f in json.loads((prefs.persona_dir() / "facts.json").read_text(encoding="utf-8"))["facts"]}
    assert stored["f2"]["archived"] is True and stored["f2"]["reason"] == prefs.ARCHIVED_BY_YOU and len(stored) == 5
    assert not prefs.set_fact_archived("f2", True), "already archived"
    assert not prefs.set_fact_archived("f4", False), "sleep archived it, not you"
    assert not prefs.set_fact_archived("f9", True)
    assert prefs.set_fact_archived("f2", False) and [f["id"] for f in prefs.read_facts()] == ["f1", "f2", "f3"]


def test_the_knows_order_and_status():
    from companion.knows import ordered, status

    facts = [
        {"id": "f1", "text": "a", "days": ["d1", "d2"], "seen": 2, "told": False},
        {"id": "f2", "text": "b", "days": ["d2"], "seen": 1, "told": False},
        {"id": "f3", "text": "c", "days": ["d2"], "seen": 1, "told": True},
    ]
    assert [f["id"] for f in ordered(facts)] == ["f3", "f1", "f2"]
    assert [status(f) for f in ordered(facts)] == ["you told her · known", "known · seen on 2 days", "not sure yet · seen on 1 day"]


@windows_only
def test_the_menu_shows_todays_spend_against_her_limit(window):
    from companion.window import spend_text

    write_persona("spend.json", {"date": date.today().isoformat(), "usd": 0.0071, "lines": 23})
    w = window()
    menu = w.build_menu()
    item = action(menu, "persona:spend")
    assert item.text() == "Today: 23 lines · $0.007 of $0.50" and not item.isEnabled()
    menu.deleteLater()
    prefs.write_limit(300)
    assert spend_text() == "Today: 23 lines of 300 · $0.007"
    prefs.write_limit("unlimited")
    assert spend_text() == "Today: 23 lines · $0.007"


@windows_only
def test_what_she_knows_lists_archives_and_undoes(window):
    from PySide6.QtWidgets import QLabel, QPushButton

    write_persona("facts.json", FACTS)
    w = window()
    knows = w.knows
    knows.show = lambda: None  # never on the desktop
    knows.activateWindow = lambda: None
    menu = w.build_menu()
    action(menu, "persona:knows").trigger()
    menu.deleteLater()
    assert [f["id"] for f in knows.facts] == ["f3", "f1", "f2"]
    texts = [label.text() for label in knows.scroll.widget().findChildren(QLabel)]
    assert "has a cat called <b>Mochi</b>" in texts, "shown as plain text"
    assert all(label.textFormat().name == "PlainText" for label in knows.scroll.widget().findChildren(QLabel))
    knows.scroll.widget().findChild(QPushButton, "archive:f2").click()
    assert [f["id"] for f in knows.facts] == ["f3", "f1"] and knows.undo_label.text() == "Archived: uses Bash a lot"
    assert not knows.undo_button.isHidden()
    knows.undo_button.click()
    assert [f["id"] for f in knows.facts] == ["f3", "f1", "f2"] and knows.undo_button.isHidden()


@windows_only
def test_what_she_knows_with_no_facts_says_so(window):
    from PySide6.QtWidgets import QLabel

    from companion.knows import EMPTY

    w = window()
    w.knows.refresh()
    assert EMPTY in [label.text() for label in w.knows.scroll.widget().findChildren(QLabel)]


@windows_only
def test_an_old_private_day_is_not_checked(window):
    prefs.write_private_day("2020-01-01")
    w = window()
    menu = w.build_menu()
    assert not action(menu, "persona:private").isChecked()
    menu.deleteLater()
