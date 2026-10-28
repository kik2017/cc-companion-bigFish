import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cc_install", ROOT / "hooks" / "install.py")
install = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install)

PYTHON = "C:\\dev\\cc-companion\\.venv\\Scripts\\python.exe"
PYTHON_POSIX = "C:/dev/cc-companion/.venv/Scripts/python.exe"
LAUNCHER = "C:/dev/cc-companion/companion/launcher.py"
STATUSLINE = "C:/dev/cc-companion/.venv/Scripts/python.exe -I -S C:/dev/cc-companion/companion/statusline.py"
OLD_QUOTED_STATUSLINE = {
    "type": "command",
    "command": '"C:/dev/cc-companion/.venv/Scripts/python.exe" "C:/dev/cc-companion/companion/statusline.py"',
    "refreshInterval": 60,
}
WINDOWS_SAFE_PATH = re.compile(r"[A-Za-z]:/[A-Za-z0-9_./~-]*")
EXPECTED_EVENTS = {
    "SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure", "PostToolBatch",
    "PermissionRequest", "PermissionDenied", "Notification", "SubagentStart", "SubagentStop", "TaskCreated",
    "TaskCompleted", "Stop", "StopFailure", "PreCompact", "PostCompact", "PostModelSwitch", "SessionEnd",
}  # fmt: skip

FOREIGN_HOOK = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo", "args": ["hi"]}]}


@pytest.fixture
def windows_rule(monkeypatch):
    """The pinned paths are Windows paths, so judge them by the Windows rule on any OS."""
    if sys.platform != "win32":
        monkeypatch.setattr(install, "SAFE_PATH", WINDOWS_SAFE_PATH)


@pytest.fixture
def snippet(windows_rule):
    return install.render_snippet(PYTHON, "C:\\dev\\cc-companion", 47621)


def our_handlers(settings):
    return [
        h
        for groups in settings.get("hooks", {}).values()
        for group in groups
        for h in group.get("hooks", [])
        if install.is_our_handler(h)
    ]


def test_snippet_has_one_group_per_event(snippet):
    assert set(snippet["hooks"]) == EXPECTED_EVENTS
    assert all(len(groups) == 1 for groups in snippet["hooks"].values())
    counts = {event: len(groups[0]["hooks"]) for event, groups in snippet["hooks"].items()}
    assert counts.pop("UserPromptSubmit") == 2
    assert set(counts.values()) == {1}


def test_launcher_hooks_are_exec_form_and_the_rest_are_http(snippet):
    http = {"type": "http", "url": "http://127.0.0.1:47621/hook?from=cc-companion", "timeout": 1}
    (session_start,) = snippet["hooks"]["SessionStart"][0]["hooks"]
    assert session_start["type"] == "command"
    assert session_start["command"] == PYTHON_POSIX
    assert session_start["args"] == [LAUNCHER]
    assert "async" not in session_start, "SessionStart must deliver before the session goes on"

    prompt_http, revive = snippet["hooks"]["UserPromptSubmit"][0]["hooks"]
    assert prompt_http == http
    # No timeout: Claude Code does not enforce one on async hooks; the launcher stops itself.
    assert revive == {"type": "command", "command": PYTHON_POSIX, "args": [LAUNCHER, "--revive"], "async": True}

    for event, groups in snippet["hooks"].items():
        if event not in ("SessionStart", "UserPromptSubmit"):
            assert groups[0]["hooks"] == [http], event


def test_rendered_snippet_has_no_backslashes_or_placeholders(snippet):
    text = json.dumps(snippet)
    assert "\\\\" not in text and "{{" not in text


def test_no_matchers_and_async_only_on_commands(snippet):
    for groups in snippet["hooks"].values():
        assert "matcher" not in groups[0]
        for handler in groups[0]["hooks"]:
            assert "async" not in handler or handler["type"] == "command"


def test_merge_into_empty_settings(snippet):
    merged, notes = install.merge({}, snippet, statusline=False)
    assert len(our_handlers(merged)) == len(EXPECTED_EVENTS) + 1  # UserPromptSubmit also revives
    assert "statusLine" not in merged
    assert notes == []


def test_merge_keeps_existing_settings_and_hooks(snippet):
    current = {"theme": "dark", "hooks": {"PreToolUse": [FOREIGN_HOOK]}}
    merged, _ = install.merge(current, snippet, statusline=False)
    assert merged["theme"] == "dark"
    assert merged["hooks"]["PreToolUse"][0] == FOREIGN_HOOK
    assert len(merged["hooks"]["PreToolUse"]) == 2
    assert current == {"theme": "dark", "hooks": {"PreToolUse": [FOREIGN_HOOK]}}, "input was mutated"


def test_merge_is_idempotent(snippet):
    once, _ = install.merge({"hooks": {"Stop": [FOREIGN_HOOK]}}, snippet, statusline=True)
    twice, _ = install.merge(once, snippet, statusline=True)
    assert once == twice


def test_reinstall_with_new_port_replaces_old_entries(snippet):
    once, _ = install.merge({}, snippet, statusline=False)
    other = install.render_snippet(PYTHON, "C:/dev/cc-companion", 50000)
    twice, _ = install.merge(once, other, statusline=False)
    urls = {h.get("url") for h in our_handlers(twice)} - {None}
    assert urls == {"http://127.0.0.1:50000/hook?from=cc-companion"}


def test_statusline_added_when_none_exists(snippet):
    merged, notes = install.merge({}, snippet, statusline=True)
    assert merged["statusLine"] == {"type": "command", "command": STATUSLINE, "refreshInterval": 60}
    assert notes == []


def test_old_quoted_statusline_of_ours_is_replaced(snippet):
    assert install.is_our_statusline(OLD_QUOTED_STATUSLINE)
    merged, notes = install.merge({"statusLine": OLD_QUOTED_STATUSLINE}, snippet, statusline=True)
    assert merged["statusLine"]["command"] == STATUSLINE
    assert notes == [], "our own earlier entry is not a user statusLine to keep"


def test_existing_statusline_is_kept_unless_replace(snippet):
    mine = {"type": "command", "command": "my-status"}
    merged, notes = install.merge({"statusLine": mine}, snippet, statusline=True)
    assert merged["statusLine"] == mine
    assert notes
    replaced, _ = install.merge({"statusLine": mine}, snippet, statusline=True, replace_statusline=True)
    assert install.is_our_statusline(replaced["statusLine"])


def test_uninstall_restores_original(snippet):
    original = {"theme": "dark", "hooks": {"PreToolUse": [FOREIGN_HOOK], "Stop": [FOREIGN_HOOK]}}
    merged, _ = install.merge(original, snippet, statusline=True)
    assert install.remove_ours(merged) == original


def test_uninstall_from_clean_settings_drops_empty_hooks_key(snippet):
    merged, _ = install.merge({"theme": "dark"}, snippet, statusline=True)
    assert install.remove_ours(merged) == {"theme": "dark"}


def test_uninstall_keeps_foreign_handlers_sharing_a_group():
    shared = {"hooks": {"Stop": [{"hooks": [
        {"type": "http", "url": "http://127.0.0.1:47621/hook?from=cc-companion"},
        {"type": "command", "command": "notify"},
    ]}]}}  # fmt: skip
    assert install.remove_ours(shared) == {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "notify"}]}]}}


def test_foreign_localhost_hook_is_not_ours():
    assert not install.is_our_handler({"type": "http", "url": "http://127.0.0.1:47621/hook"})


def test_merge_refuses_malformed_hooks(snippet):
    with pytest.raises(ValueError):
        install.merge({"hooks": []}, snippet, statusline=False)
    with pytest.raises(ValueError):
        install.merge({"hooks": {"Stop": {}}}, snippet, statusline=False)


# --- statusLine command form: unquoted, forward slashes, 8.3 fallback ---------------------


def no_short_path(path):
    return None


def test_shell_safe_returns_a_safe_path_as_is(windows_rule, monkeypatch):
    monkeypatch.setattr(install, "_short_path", lambda path: pytest.fail("short path looked up for a safe path"))
    assert install.shell_safe(PYTHON) == PYTHON_POSIX
    assert install.shell_safe(PYTHON_POSIX) == PYTHON_POSIX
    tilde = "C:/Users/x/AppData/Local/PROGRA~1/py_3.13/python.exe"
    assert install.shell_safe(tilde) == tilde


def test_shell_safe_falls_back_to_the_short_path(windows_rule, monkeypatch):
    seen = []
    monkeypatch.setattr(install, "_short_path", lambda path: seen.append(path) or "C:/PROGRA~1/PYTHON~1/python.exe")
    assert install.shell_safe("C:\\Program Files\\Python313\\python.exe") == "C:/PROGRA~1/PYTHON~1/python.exe"
    assert seen == ["C:\\Program Files\\Python313\\python.exe"]


def test_shell_safe_is_none_without_a_short_path(windows_rule, monkeypatch):
    monkeypatch.setattr(install, "_short_path", no_short_path)
    assert install.shell_safe("C:/Program Files/Python313/python.exe") is None


def test_shell_safe_rejects_a_short_path_that_is_still_unsafe(windows_rule, monkeypatch):
    # With 8.3 names turned off, GetShortPathNameW hands back the long path unchanged.
    monkeypatch.setattr(install, "_short_path", lambda path: install.posix(path))
    assert install.shell_safe("C:/Program Files/Python313/python.exe") is None


@pytest.mark.parametrize("char", ["&", "(", ")", "'", '"', "$", ";", " ", "`", "%", "!", "é"])
def test_shell_safe_rejects_shell_metacharacters(windows_rule, monkeypatch, char):
    monkeypatch.setattr(install, "_short_path", no_short_path)
    assert install.shell_safe(f"C:/dev/a{char}b/python.exe") is None


def test_shell_safe_rejects_relative_and_unc_paths(windows_rule, monkeypatch):
    monkeypatch.setattr(install, "_short_path", no_short_path)
    assert install.shell_safe("python.exe") is None
    assert install.shell_safe("\\\\server\\share\\python.exe") is None


def test_statusline_command_needs_both_paths_safe(windows_rule, monkeypatch):
    monkeypatch.setattr(install, "_short_path", no_short_path)
    assert install.statusline_command(PYTHON, "C:\\dev\\cc-companion") == STATUSLINE
    assert install.statusline_command(Path(PYTHON), Path("C:/dev/cc-companion")) == STATUSLINE
    assert install.statusline_command("C:/Program Files/Python313/python.exe", "C:/dev/cc-companion") is None
    assert install.statusline_command(PYTHON, "C:/dev/my repo") is None


def test_statusline_command_uses_short_paths(windows_rule, monkeypatch):
    short = {"C:/Program Files/Python313/python.exe": "C:/PROGRA~1/PYTHON~1/python.exe", "C:/my repo": "C:/MYREPO~1"}
    monkeypatch.setattr(install, "_short_path", lambda path: short.get(install.posix(path)))
    assert install.statusline_command("C:/Program Files/Python313/python.exe", "C:/my repo") == (
        "C:/PROGRA~1/PYTHON~1/python.exe -I -S C:/MYREPO~1/companion/statusline.py"
    )


def test_unsafe_paths_leave_an_empty_statusline_command_and_exec_form_hooks(windows_rule, monkeypatch):
    monkeypatch.setattr(install, "_short_path", no_short_path)
    rendered = install.render_snippet("C:/Program Files/Python313/python.exe", "C:/dev/cc-companion", 47621)
    assert rendered["statusLine"]["command"] == ""
    (session_start,) = rendered["hooks"]["SessionStart"][0]["hooks"]
    assert session_start["command"] == "C:/Program Files/Python313/python.exe", "exec form copes with spaces"


def test_short_path_of_a_real_directory(tmp_path):
    spaced = tmp_path / "with space"
    spaced.mkdir()
    short = install._short_path(spaced)
    if sys.platform != "win32":
        assert short is None
        return
    assert short is not None and "\\" not in short
    assert Path(short).samefile(spaced)
    # The volume may have 8.3 names turned off; either way shell_safe agrees with SAFE_PATH.
    assert install.shell_safe(spaced) == (short if install.SAFE_PATH.fullmatch(short) else None)
    assert install._short_path(tmp_path / "missing") is None
    assert install._short_path("C:/bad\0path") is None


# --- main() against a temp settings file -----------------------------------------------


def run_main(tmp_path, *extra):
    settings = tmp_path / "settings.json"
    python = tmp_path / "python.exe"
    python.write_text("")
    return settings, install.main(["--settings", str(settings), "--python", str(python), *extra])


def test_main_installs_with_backup_then_uninstalls(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"agentPushNotifEnabled": True}), encoding="utf-8")
    _, code = run_main(tmp_path)
    assert code == 0
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert set(data["hooks"]) == EXPECTED_EVENTS
    assert len(our_handlers(data)) == len(EXPECTED_EVENTS) + 1
    assert data["agentPushNotifEnabled"] is True
    assert len(list(tmp_path.glob("settings.json.bak-*"))) == 1

    _, code = run_main(tmp_path, "--uninstall")
    assert code == 0
    assert json.loads(settings.read_text(encoding="utf-8")) == {"agentPushNotifEnabled": True}


def test_main_never_overwrites_a_backup_made_in_the_same_second(tmp_path, repo, monkeypatch):
    """Install then uninstall within one second must keep the backup holding the user's statusLine."""
    monkeypatch.setattr(install.time, "strftime", lambda fmt, *args: "20261027-120000")
    monkeypatch.setattr(install, "statusline_command", lambda python, root: STATUSLINE)
    mine = {"type": "command", "command": "my-own-line"}
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"statusLine": mine}), encoding="utf-8")
    assert run_main(tmp_path, "--replace-statusline")[1] == 0
    assert json.loads(settings.read_text(encoding="utf-8"))["statusLine"]["command"] == STATUSLINE
    assert run_main(tmp_path, "--uninstall")[1] == 0
    first = tmp_path / "settings.json.bak-20261027-120000"
    second = tmp_path / "settings.json.bak-20261027-120000-1"
    assert sorted(tmp_path.glob("settings.json.bak-*")) == [first, second]
    assert json.loads(first.read_text(encoding="utf-8")) == {"statusLine": mine}
    assert json.loads(second.read_text(encoding="utf-8"))["statusLine"]["command"] == STATUSLINE


def test_main_refuses_invalid_json(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{oops", encoding="utf-8")
    _, code = run_main(tmp_path)
    assert code == 1
    assert settings.read_text(encoding="utf-8") == "{oops"


def test_main_dry_run_writes_nothing(tmp_path, capsys):
    settings, code = run_main(tmp_path, "--dry-run")
    assert code == 0
    assert not settings.exists()
    assert "from=cc-companion" in capsys.readouterr().out


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A stand-in repo root holding companion/statusline.py, so main() decides about a statusLine."""
    root = tmp_path / "repo"
    (root / "companion").mkdir(parents=True)
    (root / "companion" / "statusline.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(install, "ROOT", root)
    return root


def test_main_installs_the_unquoted_statusline(tmp_path, repo, capsys):
    if not install.SAFE_PATH.fullmatch(install.posix(tmp_path)):
        pytest.skip("the temp dir itself needs a short path here")
    settings, code = run_main(tmp_path)
    assert code == 0
    data = json.loads(settings.read_text(encoding="utf-8"))
    command = f"{install.posix(tmp_path)}/python.exe -I -S {install.posix(repo)}/companion/statusline.py"
    assert data["statusLine"] == {"type": "command", "command": command, "refreshInterval": 60}
    assert "note:" not in capsys.readouterr().out


def test_main_skips_an_unsafe_statusline_with_a_note(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(install, "statusline_command", lambda python, root: None)
    settings, code = run_main(tmp_path)
    assert code == 0
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert set(data["hooks"]) == EXPECTED_EVENTS
    assert len(our_handlers(data)) == len(EXPECTED_EVENTS) + 1
    assert "statusLine" not in data
    assert f"note: {install.STATUSLINE_SKIPPED}\n" in capsys.readouterr().out
    assert install.STATUSLINE_SKIPPED.startswith("statusLine skipped: interpreter or repo path has characters")

    # Re-running changes nothing but still says why the statusLine is missing.
    _, code = run_main(tmp_path)
    assert code == 0
    out = capsys.readouterr().out
    assert "already up to date" in out and install.STATUSLINE_SKIPPED in out


def test_main_skip_keeps_a_user_statusline(tmp_path, repo, monkeypatch):
    monkeypatch.setattr(install, "statusline_command", lambda python, root: None)
    mine = {"type": "command", "command": "my-status"}
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"statusLine": mine}), encoding="utf-8")
    _, code = run_main(tmp_path, "--replace-statusline")
    assert code == 0
    assert json.loads(settings.read_text(encoding="utf-8"))["statusLine"] == mine


def test_main_dry_run_notes_go_to_stderr(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(install, "statusline_command", lambda python, root: None)
    _, code = run_main(tmp_path, "--dry-run")
    assert code == 0
    captured = capsys.readouterr()
    assert "statusLine" not in json.loads(captured.out)
    assert install.STATUSLINE_SKIPPED in captured.err


def test_main_without_the_script_writes_no_statusline_and_no_note(tmp_path, repo, capsys):
    (repo / "companion" / "statusline.py").unlink()
    settings, code = run_main(tmp_path)
    assert code == 0
    assert "statusLine" not in json.loads(settings.read_text(encoding="utf-8"))
    assert "note:" not in capsys.readouterr().out


# --- shell round trip: the command must run in Git Bash and in PowerShell --------------

PAYLOAD = b'{"session_id": "abc"}'
ROUND_TRIP_SCRIPT = """\
import sys
data = sys.stdin.read()
print("ok" if data == '{"session_id": "abc"}' and sys.flags.isolated and sys.flags.no_site else "bad")
"""


def git_bash() -> str | None:
    """Git for Windows' bash, or the bash on PATH unless it is WSL's (System32 or WindowsApps)."""
    bundled = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git" / "bin" / "bash.exe"
    if bundled.exists():
        return str(bundled)
    found = shutil.which("bash")
    if found and not any(part in found.lower() for part in ("system32", "windowsapps")):
        return found
    return None


@pytest.fixture(params=["repo", "my repo"], ids=["plain", "short-path"])
def round_trip_command(tmp_path, request):
    root = tmp_path / request.param
    (root / "companion").mkdir(parents=True)
    (root / "companion" / "statusline.py").write_text(ROUND_TRIP_SCRIPT, encoding="utf-8")
    command = install.statusline_command(sys.executable, root)
    if command is None:
        pytest.skip("no shell-safe form of the interpreter or temp path here")
    assert " " not in command.removesuffix("/companion/statusline.py").replace(" -I -S ", "")
    return command


@pytest.mark.parametrize("shell", ["git-bash", "powershell"])
def test_statusline_command_runs_in_the_shell(round_trip_command, shell):
    if shell == "git-bash":
        exe = git_bash()
        argv = [exe, "-c", round_trip_command]
    else:
        exe = shutil.which("powershell")
        argv = [exe, "-NoProfile", "-NonInteractive", "-Command", round_trip_command]
    if exe is None:
        pytest.skip(f"{shell} is not installed")
    result = subprocess.run(
        argv,
        input=PAYLOAD,
        capture_output=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert result.stdout.strip() == b"ok", (result.stdout, result.stderr)


# --- plugin ---------------------------------------------------------------------------------


def test_plugin_hooks_file_is_generated_from_the_snippet():
    committed = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    assert committed == install.render_plugin_hooks(), "run: python hooks/install.py --write-plugin-hooks"


def test_plugin_hooks_run_the_clone_venv_in_exec_form_and_ship_no_statusline():
    hooks = install.render_plugin_hooks()
    assert set(hooks) == {"hooks"}, "a plugin cannot ship a statusLine"
    assert set(hooks["hooks"]) == EXPECTED_EVENTS
    (start,) = hooks["hooks"]["SessionStart"][0]["hooks"]
    assert start["command"] == "${CLAUDE_PLUGIN_ROOT}/.venv/Scripts/python.exe"
    assert start["args"] == ["${CLAUDE_PLUGIN_ROOT}/companion/launcher.py"]
    urls = {h["url"] for groups in hooks["hooks"].values() for h in groups[0]["hooks"] if h["type"] == "http"}
    assert urls == {"http://127.0.0.1:47621/hook?from=cc-companion"}


def test_plugin_and_marketplace_manifests_agree():
    import tomllib

    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    (entry,) = market["plugins"]
    assert entry == {**entry, "name": plugin["name"], "source": "./"}  # relative: loads in place from the clone
    assert plugin["name"] == install.PLUGIN_ID.rstrip("@") == project["name"]
    assert plugin["version"] == project["version"]
    assert plugin["license"] == project["license"] == "MIT AND CC-BY-NC-SA-4.0"  # code MIT, character art NC
    assert "LICENSE" in project["license-files"] and "companion/assets/character/LICENSE.txt" in project["license-files"]
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert license_text.startswith("MIT License") and "Copyright (c) 2026 kik2017" in license_text


@pytest.mark.parametrize(
    "enabled, expected",
    [
        ({"cc-companion@cc-companion": True}, True),
        ({"cc-companion@elsewhere": True}, True),
        ({"cc-companion@cc-companion": False}, False),
        ({"other@cc-companion": True}, False),
        ([], False),
        (None, False),
    ],
)
def test_plugin_enabled(enabled, expected):
    assert install.plugin_enabled({} if enabled is None else {"enabledPlugins": enabled}) is expected


def test_plugin_mode_installs_only_the_statusline_and_removes_our_hooks(tmp_path, capsys):
    settings = tmp_path / "settings.json"
    _, code = run_main(tmp_path)
    data = json.loads(settings.read_text(encoding="utf-8"))
    data["hooks"]["Stop"].append(FOREIGN_HOOK)
    settings.write_text(json.dumps(data), encoding="utf-8")

    _, code = run_main(tmp_path, "--plugin")
    assert code == 0
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert our_handlers(data) == []
    assert data["hooks"] == {"Stop": [FOREIGN_HOOK]}
    assert install.is_our_statusline(data["statusLine"])
    assert "merged the cc-companion statusLine into" in capsys.readouterr().out


def test_plugin_mode_on_clean_settings_leaves_no_hooks_key(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
    _, code = run_main(tmp_path, "--plugin")
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert code == 0 and "hooks" not in data and data["theme"] == "dark"


def test_an_enabled_plugin_makes_a_plain_install_statusline_only(tmp_path, capsys):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"enabledPlugins": {"cc-companion@cc-companion": True}}), encoding="utf-8")
    _, code = run_main(tmp_path)
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert code == 0 and our_handlers(data) == [] and install.is_our_statusline(data["statusLine"])
    assert "plugin is enabled and provides the hooks" in capsys.readouterr().out
