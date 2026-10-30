import json
import shutil
import sys
from pathlib import Path

import pytest

from companion import settings as prefs


def test_defaults_when_file_missing():
    assert prefs.load() == prefs.Settings()


def test_round_trip():
    original = prefs.Settings(x=120, y=-40, size="L", bubble_mode="attention", sounds=True)
    prefs.save(original)
    assert prefs.load() == original


def test_corrupt_file_falls_back_to_defaults():
    prefs.settings_path().write_text("{not json", encoding="utf-8")
    assert prefs.load() == prefs.Settings()


def test_non_object_json_falls_back_to_defaults():
    prefs.settings_path().write_text("[1, 2]", encoding="utf-8")
    assert prefs.load() == prefs.Settings()


def test_invalid_fields_fall_back_one_at_a_time():
    prefs.settings_path().write_text(
        json.dumps({"x": "left", "y": True, "size": "XXL", "sounds": "yes", "activity": "lively", "extra": 1}),
        encoding="utf-8",
    )
    loaded = prefs.load()
    assert loaded.x is None and loaded.y is None
    assert loaded.size == "M"
    assert loaded.sounds is False
    assert loaded.activity == "lively"


def test_skin_must_be_a_plain_folder_name():
    for good in ("", "my-skin", "Pack_2.1"):
        prefs.settings_path().write_text(json.dumps({"skin": good}), encoding="utf-8")
        assert prefs.load().skin == good
    for bad in ("../escape", "a/b", "a\\b", ".hidden", 5):
        prefs.settings_path().write_text(json.dumps({"skin": bad}), encoding="utf-8")
        assert prefs.load().skin == "", bad


def test_size_px_follows_size():
    assert prefs.Settings(size="S").size_px < prefs.Settings().size_px < prefs.Settings(size="L").size_px
    assert prefs.Settings().size_px == 275
    assert prefs.SIZES["S"] < prefs.SIZES["M"] < prefs.SIZES["L"]


def test_update_accepts_valid_values():
    settings = prefs.Settings(x=10)
    for name, value in (("size", "L"), ("bubble_mode", "attention"), ("reduced_motion", True), ("x", None)):
        assert prefs.update(settings, name, value), name
        assert getattr(settings, name) == value
    assert settings == prefs.Settings(size="L", bubble_mode="attention", reduced_motion=True)


def test_update_rejects_bad_values_and_leaves_the_field():
    settings = prefs.Settings()
    for name, value in (("size", "XL"), ("reduced_motion", "yes"), ("activity", 3), ("x", True), ("skin", "../up")):
        assert not prefs.update(settings, name, value), name
    assert not prefs.update(settings, "volume", 5)
    assert not prefs.update(settings, "size_px", 344), "a property is not a field"
    assert not hasattr(settings, "volume")
    assert settings == prefs.Settings()


def test_update_then_save_round_trips():
    settings = prefs.load()
    assert prefs.update(settings, "activity", "lively")
    prefs.save(settings)
    assert prefs.load() == prefs.Settings(activity="lively")


def test_app_dir_honours_override(isolated_app_dir):
    assert prefs.app_dir() == isolated_app_dir
    assert isolated_app_dir.is_dir()


def test_rotate_log_if_large(isolated_app_dir):
    log = prefs.log_path()
    log.write_bytes(b"x" * (prefs.LOG_MAX_BYTES + 1))
    prefs.rotate_log_if_large()
    assert not log.exists()
    assert log.with_suffix(".log.1").stat().st_size == prefs.LOG_MAX_BYTES + 1


def test_small_log_is_left_alone():
    log = prefs.log_path()
    log.write_text("hello", encoding="utf-8")
    prefs.rotate_log_if_large()
    assert log.read_text(encoding="utf-8") == "hello"

def test_persona_is_off_by_default_and_takes_only_its_modes():
    settings = prefs.Settings()
    assert settings.persona == "off"
    for mode in prefs.PERSONA_MODES:
        assert prefs.update(settings, "persona", mode)
    for bad in ("loud", "", None, True):
        assert not prefs.update(settings, "persona", bad)
    assert settings.persona == prefs.PERSONA_MODES[-1]


def test_persona_folder_and_token_live_in_the_app_folder(isolated_app_dir):
    assert prefs.persona_dir() == isolated_app_dir / "persona"
    assert prefs.say_token_path() == isolated_app_dir / "say-token"

def test_chattiness_lives_in_her_config_and_keeps_her_other_keys(isolated_app_dir):
    assert prefs.read_chattiness() == "normal"
    assert prefs.write_chattiness("chatty") and prefs.read_chattiness() == "chatty"
    path = prefs.persona_config_path()
    path.write_text('{"daily_usd": 1.0, "chattiness": "quiet"}', encoding="utf-8")
    assert prefs.write_chattiness("normal")
    assert json.loads(path.read_text(encoding="utf-8")) == {"daily_usd": 1.0, "chattiness": "normal"}
    assert not prefs.write_chattiness("LOUD")
    path.write_text('{"chattiness": "LOUD"}', encoding="utf-8")
    assert prefs.read_chattiness() == "normal"
    path.write_text("not json", encoding="utf-8")
    assert prefs.read_chattiness() == "normal" and prefs.write_chattiness("quiet")


# --- the app folder in Documents --------------------------------------------------------------

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="the Documents folder is the Windows default")


def old_folder(root):
    (root / "persona" / "diary").mkdir(parents=True)
    (root / "persona" / "identity.md").write_text("我是小汐。", encoding="utf-8")
    (root / "persona" / "diary" / "2026-10-28.md").write_text("# 2026-10-28", encoding="utf-8")
    (root / "settings.json").write_text('{"size": "L"}', encoding="utf-8")
    return root


def test_the_old_folder_is_copied_once_and_left_as_it_was(tmp_path):
    old, new = old_folder(tmp_path / "AppData" / "cc-companion"), tmp_path / "Documents" / "cc-companion"
    assert prefs.copy_old_folder(old, new)
    assert (new / "persona" / "identity.md").read_text(encoding="utf-8") == "我是小汐。"
    assert (new / "persona" / "diary" / "2026-10-28.md").exists() and (new / "settings.json").exists()
    assert (old / "persona" / "identity.md").exists(), "the old folder stays as a backup"
    (new / "settings.json").write_text('{"size": "S"}', encoding="utf-8")
    assert not prefs.copy_old_folder(old, new), "never over a folder that is already there"
    assert (new / "settings.json").read_text(encoding="utf-8") == '{"size": "S"}'
    assert not prefs.copy_old_folder(tmp_path / "missing", tmp_path / "elsewhere")
    assert not (tmp_path / "elsewhere").exists()


def test_a_copy_that_loses_the_race_is_thrown_away(tmp_path, monkeypatch):
    old, new = old_folder(tmp_path / "old"), tmp_path / "new"
    real_copytree = shutil.copytree

    def copy_while_another_process_wins(src, dst, *args, **kwargs):
        copied = real_copytree(src, dst, *args, **kwargs)  # it recurses through here for subfolders
        if Path(src) == old:
            new.mkdir()
            (new / "settings.json").write_text('{"size": "M"}', encoding="utf-8")
        return copied

    monkeypatch.setattr(shutil, "copytree", copy_while_another_process_wins)
    assert not prefs.copy_old_folder(old, new)
    assert (new / "settings.json").read_text(encoding="utf-8") == '{"size": "M"}', "the winner's copy stays"
    assert [p.name for p in tmp_path.iterdir() if "copying" in p.name] == [], "ours is cleaned up"


@windows_only
def test_on_windows_the_app_folder_is_in_documents_and_starts_as_a_copy(tmp_path, monkeypatch):
    old = old_folder(tmp_path / "AppData" / "cc-companion")
    monkeypatch.delenv("CC_COMPANION_HOME")
    monkeypatch.setattr(prefs, "documents_dir", lambda: tmp_path / "Documents")
    monkeypatch.setattr(prefs, "legacy_dir", lambda: old)
    monkeypatch.setattr(prefs, "copied_from", None)
    assert prefs.app_dir() == tmp_path / "Documents" / "cc-companion"
    assert prefs.copied_from == old and prefs.persona_dir() == tmp_path / "Documents" / "cc-companion" / "persona"
    assert (prefs.persona_dir() / "identity.md").read_text(encoding="utf-8") == "我是小汐。"
    assert prefs.load().size == "L", "her settings came along"


@windows_only
def test_a_failed_copy_keeps_using_the_old_folder_for_now(tmp_path, monkeypatch):
    old = old_folder(tmp_path / "AppData" / "cc-companion")
    monkeypatch.delenv("CC_COMPANION_HOME")
    monkeypatch.setattr(prefs, "documents_dir", lambda: tmp_path / "Documents")
    monkeypatch.setattr(prefs, "legacy_dir", lambda: old)

    def disk_full(src, dst, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copytree", disk_full)
    assert prefs.app_dir() == old
    assert not (tmp_path / "Documents" / "cc-companion").exists(), "so the copy is tried again next time"


@windows_only
def test_a_fresh_install_starts_an_empty_folder_in_documents(tmp_path, monkeypatch):
    monkeypatch.delenv("CC_COMPANION_HOME")
    monkeypatch.setattr(prefs, "documents_dir", lambda: tmp_path / "Documents")
    monkeypatch.setattr(prefs, "legacy_dir", lambda: tmp_path / "AppData" / "cc-companion")
    assert prefs.app_dir() == tmp_path / "Documents" / "cc-companion" and prefs.app_dir().is_dir()


@windows_only
def test_documents_is_found_through_windows():
    found = prefs.documents_dir()
    assert found.is_absolute() and found.is_dir()
