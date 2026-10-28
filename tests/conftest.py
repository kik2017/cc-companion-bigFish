import pytest


@pytest.fixture(autouse=True)
def isolated_app_dir(tmp_path, monkeypatch):
    """Every test gets its own app data dir so the real settings and log are never touched."""
    home = tmp_path / "app"
    monkeypatch.setenv("CC_COMPANION_HOME", str(home))
    return home


@pytest.fixture(autouse=True)
def no_real_api_key(monkeypatch):
    """No test reads or writes the user's real API key: an empty registry that refuses writes."""
    from companion import apikey

    for name in apikey.KEY_NAMES:
        monkeypatch.setenv(name, "")  # recorded, so a key a test saves is gone again afterwards
        monkeypatch.delenv(name)
    monkeypatch.setattr(apikey, "_read_user_env", lambda name: None)
    monkeypatch.setattr(apikey, "_write_user_env", lambda name, value: False)
