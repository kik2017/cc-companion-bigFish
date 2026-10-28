import json

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
