"""Connect the pet to Claude Code from its tray menu, or disconnect it. No Qt.

Connecting merges the pet's hooks and status line into ~/.claude/settings.json and
disconnecting takes only those out again, exactly as `python hooks/install.py` and
`--uninstall` do, with a timestamped backup before every change. hooks/install.py is
loaded from its file, which the portable build bundles with its settings snippet. From
the portable build the hooks run its cc-companion-cli.exe; from source, this repo's
python and launcher.py.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

from . import frozen
from .server import port_from_env


def settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def load_install() -> ModuleType:
    path = frozen.bundle() / "hooks" / "install.py"
    spec = importlib.util.spec_from_file_location("cc_companion_install", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read(path: Path) -> dict | None:
    """The settings as a dict ({} when the file does not exist yet); None if unreadable or not an object."""
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def is_connected(path: Path | None = None) -> bool:
    """True if any of the pet's hooks are in Claude Code's settings."""
    install = load_install()
    current = _read(path or settings_path()) or {}
    return install.remove_ours(current) != current


def connect(path: Path | None = None, port: int | None = None) -> tuple[bool, list[str]]:
    """Merge the pet's hooks and status line into the settings; (changed or already there, notes for you)."""
    install = load_install()
    path = path or settings_path()
    port = port or port_from_env()
    current = _read(path)
    if current is None:
        return False, [f"could not read {path}; nothing changed"]
    if frozen.is_frozen():
        snippet = install.render_exe_snippet(frozen.cli_exe(), port)
        status = install.exe_statusline_command(frozen.cli_exe())
    else:
        python = install.default_python()
        snippet = install.render_snippet(python, install.ROOT, port)
        status = install.statusline_command(python, install.ROOT)
    notes: list[str] = []
    if install.plugin_enabled(current):
        snippet = {"hooks": {}, "statusLine": snippet["statusLine"]}
        notes.append("the cc-companion plugin is enabled and provides the hooks; added only the status line")
    try:
        updated, merge_notes = install.merge(current, snippet, statusline=status is not None)
    except ValueError as error:
        return False, [f"{error}; nothing changed"]
    notes += [note.replace("; pass --replace-statusline to use the companion's", "") for note in merge_notes]
    if status is None:
        notes.append(install.STATUSLINE_SKIPPED)
    return _write(install, path, current, updated), notes


def disconnect(path: Path | None = None) -> tuple[bool, list[str]]:
    """Take only the pet's entries out of the settings; (done, notes for you)."""
    install = load_install()
    path = path or settings_path()
    current = _read(path)
    if current is None:
        return False, [f"could not read {path}; nothing changed"]
    return _write(install, path, current, install.remove_ours(current)), []


def _write(install: ModuleType, path: Path, current: dict, updated: dict) -> bool:
    if updated == current:
        return True
    try:
        if path.exists():
            install.back_up(path)
        install.write_atomically(path, updated)
    except OSError:
        return False
    return True
