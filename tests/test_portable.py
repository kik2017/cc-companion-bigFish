"""The portable build's plumbing: program paths, hooks for cc-companion-cli.exe, Connect to
Claude Code, the API key and the tray icon.

Nothing here touches the real Claude Code settings, the real registry or the real tray:
settings files are temporary, the registry is the conftest's empty fake, and the tray icon
is built but never shown.
"""

import json
import os
import sys
from pathlib import Path

import pytest

from companion import apikey, connect, frozen

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="window checks are Windows-only")


@pytest.fixture
def portable(tmp_path, monkeypatch):
    """Pretend to be the portable build living in tmp_path/app, with its programs in place."""
    folder = tmp_path / "app"
    (folder / "_internal").mkdir(parents=True)
    for name in (frozen.APP_EXE, frozen.CLI_EXE):
        (folder / name).write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(folder / frozen.APP_EXE))
    monkeypatch.setattr(sys, "_MEIPASS", str(frozen.ROOT), raising=False)  # hooks/ as the build bundles it
    return folder


# --- program paths -------------------------------------------------------------------------


def test_from_source_the_programs_are_python_modules():
    assert not frozen.is_frozen()
    assert frozen.daemon_command()[-2:] == ["-m", "companion"]
    assert frozen.mind_command() == (sys.executable, ["-m", "mind"])
    assert frozen.home() == frozen.ROOT


def test_the_portable_build_runs_its_own_programs(portable):
    assert frozen.daemon_command() == [str(portable / "cc-companion.exe")]
    assert frozen.mind_command() == (str(portable / "cc-companion-cli.exe"), ["mind"])
    assert frozen.home() == portable and frozen.cli_exe() == portable / "cc-companion-cli.exe"


def test_the_launcher_and_the_mind_link_follow_the_build(portable):
    from companion import launcher

    assert launcher.daemon_command() == [str(portable / "cc-companion.exe")]


# --- hooks for the portable build ----------------------------------------------------------


def settings_file(tmp_path, data=None):
    path = tmp_path / "claude" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if data is not None:
        path.write_text(json.dumps(data), encoding="utf-8")
    return path


FOREIGN = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo", "args": ["hi"]}]}


def test_the_exe_snippet_runs_the_cli_for_launch_and_the_status_line():
    install = connect.load_install()
    snippet = install.render_exe_snippet("C:/apps/cc/cc-companion-cli.exe", 47621)
    start = snippet["hooks"]["SessionStart"][0]["hooks"][0]
    revive = snippet["hooks"]["UserPromptSubmit"][0]["hooks"][1]
    assert start["command"] == "C:/apps/cc/cc-companion-cli.exe" and start["args"] == ["launch"]
    assert revive["args"] == ["launch", "--revive"] and revive["async"] is True
    assert snippet["statusLine"]["command"] == "C:/apps/cc/cc-companion-cli.exe statusline"
    assert snippet["hooks"]["Stop"][0]["hooks"][0]["url"].endswith("/hook?from=cc-companion")


def test_exe_entries_are_recognized_as_ours_and_removed_cleanly():
    install = connect.load_install()
    snippet = install.render_exe_snippet("C:/apps/cc/cc-companion-cli.exe", 47621)
    merged, _ = install.merge({"hooks": {"PreToolUse": [FOREIGN]}}, snippet, statusline=True)
    assert install.is_our_statusline(merged["statusLine"])
    assert install.remove_ours(merged) == {"hooks": {"PreToolUse": [FOREIGN]}}
    assert not install.is_our_handler({"type": "command", "command": "C:/x/cc-companion-cli.exe", "args": ["other"]})


def test_install_main_takes_the_exe(tmp_path):
    install = connect.load_install()
    cli = tmp_path / "cc-companion-cli.exe"
    cli.write_bytes(b"")
    path = settings_file(tmp_path, {"hooks": {"PreToolUse": [FOREIGN]}})
    assert install.main(["--settings", str(path), "--exe", str(cli)]) == 0
    settings = json.loads(path.read_text(encoding="utf-8"))
    assert settings["hooks"]["SessionStart"][0]["hooks"][0]["args"] == ["launch"]
    assert settings["statusLine"]["command"].endswith("cc-companion-cli.exe statusline")
    assert FOREIGN in settings["hooks"]["PreToolUse"]
    assert install.main(["--settings", str(path), "--exe", str(tmp_path / "missing.exe")]) == 1


# --- Connect to Claude Code ----------------------------------------------------------------


def test_connect_and_disconnect_from_the_portable_build(portable, tmp_path):
    path = settings_file(tmp_path, {"model": "opus", "hooks": {"PreToolUse": [FOREIGN]}})
    assert not connect.is_connected(path)
    done, notes = connect.connect(path, port=47621)
    assert done and notes == [] and connect.is_connected(path)
    settings = json.loads(path.read_text(encoding="utf-8"))
    assert settings["model"] == "opus" and FOREIGN in settings["hooks"]["PreToolUse"]
    assert settings["hooks"]["SessionStart"][0]["hooks"][0]["command"].endswith("/cc-companion-cli.exe")
    assert list(path.parent.glob("settings.json.bak-*")), "a backup before the change"
    assert connect.connect(path, port=47621)[0], "connecting again is safe"
    assert connect.disconnect(path) == (True, [])
    assert json.loads(path.read_text(encoding="utf-8")) == {"model": "opus", "hooks": {"PreToolUse": [FOREIGN]}}


def test_connect_creates_the_settings_file_and_keeps_an_existing_status_line(portable, tmp_path):
    path = settings_file(tmp_path)
    assert connect.connect(path, port=47621)[0] and path.exists()
    mine = {"type": "command", "command": "my-status"}
    path.write_text(json.dumps({"statusLine": mine}), encoding="utf-8")
    done, notes = connect.connect(path, port=47621)
    assert done and json.loads(path.read_text(encoding="utf-8"))["statusLine"] == mine
    assert notes == ["kept your existing statusLine"]


def test_connect_refuses_settings_it_cannot_read(tmp_path):
    path = settings_file(tmp_path)
    path.write_text("{not json", encoding="utf-8")
    assert connect.connect(path) == (False, [f"could not read {path}; nothing changed"])
    assert path.read_text(encoding="utf-8") == "{not json"


# --- the API key ---------------------------------------------------------------------------


def test_the_key_is_found_in_the_environment_or_the_registry(monkeypatch):
    assert not apikey.has_api_key()
    assert apikey.has_api_key(read=lambda name: "sk-ant-x" if name == "ANTHROPIC_API_KEY" else None)
    monkeypatch.setenv("CC_COMPANION_API_KEY", "sk-ant-y")
    assert apikey.has_api_key()


def test_saving_a_key_writes_only_the_user_variable():
    written = []
    assert apikey.save_api_key("  sk-ant-abc  ", write=lambda name, value: written.append((name, value)) or True)
    assert written == [("CC_COMPANION_API_KEY", "sk-ant-abc")] and os.environ["CC_COMPANION_API_KEY"] == "sk-ant-abc"
    for bad in ("", "   ", "sk ant"):
        assert not apikey.save_api_key(bad, write=lambda name, value: True)
    assert not apikey.save_api_key("sk-ant-z", write=lambda name, value: False)


# --- the tray ------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def pet(app):
    from PySide6.QtCore import QPoint, QRect

    from companion import settings as prefs
    from companion import skin as skins
    from companion.window import CompanionWindow

    created = []

    def make(persona="off"):
        w = CompanionWindow(prefs.Settings(persona=persona), skin=skins.load_skin(Path(__file__).parent / "fixtures" / "placeholder-skin"))
        w.bubble.area_for = lambda rect: QRect(-32400, -32400, 3000, 2000)
        w.move(QPoint(-32000, -32000))
        created.append(w)
        return w

    yield make
    for w in created:
        w.close()
    app.processEvents()


@pytest.fixture
def tray(pet, tmp_path):
    from companion.tray import Tray

    calls = {"api_key": 0, "show": 0}
    built = Tray(
        pet(),
        on_api_key=lambda: calls.__setitem__("api_key", calls["api_key"] + 1),
        on_show=lambda: calls.__setitem__("show", calls["show"] + 1),
    )
    built.settings_path = settings_file(tmp_path, {})
    built.icon.showMessage = lambda *args: None  # no balloon on the desktop
    built.calls = calls
    return built


def names(menu):
    from PySide6.QtGui import QAction

    return {a.objectName() for a in menu.findChildren(QAction) if a.objectName()}


@windows_only
def test_the_tray_icon_is_her_face(pet):
    from companion.tray import face

    icon = face(pet().skin)
    assert not icon.isNull() and icon.availableSizes()


@windows_only
def test_the_tray_menu_has_its_own_items_then_the_pets(tray):
    tray.fill()
    found = names(tray.menu)
    assert {"tray:toggle", "tray:apikey", "tray:connect", "persona:talk", "size:M", "quit"} <= found
    texts = [a.text() for a in tray.menu.actions()[:3]]
    assert texts == ["Show her", "Set API key…", "Connect to Claude Code…"]
    tray.fill()
    assert len([a for a in tray.menu.actions() if a.objectName() == "tray:toggle"]) == 1, "rebuilt, not doubled"


@windows_only
def test_show_and_hide_her_from_the_tray(tray, app):
    pet = tray.window
    pet.hide()
    tray.toggle()
    assert pet.isVisible() and tray.calls["show"] == 1
    tray.fill()
    assert tray.menu.actions()[0].text() == "Hide her"
    tray.toggle()
    assert not pet.isVisible()


@windows_only
def test_set_api_key_saves_then_restarts_the_mind(tray):
    saved = []
    tray.ask_api_key = lambda parent: None
    assert not tray.set_api_key() and tray.calls["api_key"] == 0, "cancelled"
    tray.ask_api_key = lambda parent: "sk-ant-new"
    tray.save_api_key = lambda key: saved.append(key) or True
    assert tray.set_api_key() and saved == ["sk-ant-new"] and tray.calls["api_key"] == 1
    tray.has_api_key = lambda: True
    tray.fill()
    assert tray.menu.actions()[1].text() == "Change API key…"


@windows_only
def test_connect_asks_first_then_changes_the_settings(tray, portable):
    tray.confirm_connect = lambda parent: False
    assert not tray.connect() and not connect.is_connected(tray.settings_path)
    tray.confirm_connect = lambda parent: True
    assert tray.connect() and connect.is_connected(tray.settings_path)
    tray.fill()
    item = next(a for a in tray.menu.actions() if a.objectName() == "tray:connect")
    assert item.isChecked() and item.text() == "Connected to Claude Code"
    item.trigger()
    assert not connect.is_connected(tray.settings_path), "a checked item disconnects"


@windows_only
def test_turning_her_on_without_a_key_asks_for_one(pet):
    w = pet()
    asked = []
    w.api_key_needed.connect(lambda: asked.append(1))
    w.apply_setting("persona", "on")
    assert asked == [1]
    w.has_api_key = lambda: True
    w.apply_setting("persona", "quiet")
    assert asked == [1]
