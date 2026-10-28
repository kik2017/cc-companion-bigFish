"""Where the app's programs are: from source (python -m ...) or as the portable build. No Qt.

The portable build (packaging/) is one folder holding two programs that share a runtime:

    cc-companion.exe       windowed: the pet itself (the daemon, its tray icon and menus)
    cc-companion-cli.exe   console: `launch` (the hook launcher), `statusline`, `mind`

The console one runs everything that talks over stdin and stdout. Claude Code starts it
for the hooks and the status line with its window hidden, and the daemon starts the mind
through QProcess, which hides a console child's window when the daemon has no console.
From source the same jobs run as python -m companion, launcher.py, statusline.py and
python -m mind.
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_EXE = "cc-companion.exe"
CLI_EXE = "cc-companion-cli.exe"
ROOT = Path(__file__).resolve().parents[1]  # the repo, from source


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def home() -> Path:
    """The folder holding the programs: the build's folder, or the repo."""
    return Path(sys.executable).parent if is_frozen() else ROOT


def bundle() -> Path:
    """Where bundled files (hooks/, assets) are unpacked: the build's _internal folder, or the repo."""
    return Path(getattr(sys, "_MEIPASS", ROOT)) if is_frozen() else ROOT


def cli_exe() -> Path:
    return home() / CLI_EXE


def daemon_command() -> list[str]:
    """The pet, as the launcher starts it: windowless, so no console appears."""
    if is_frozen():
        return [str(home() / APP_EXE)]
    exe = Path(sys.executable)
    if sys.platform == "win32" and exe.with_name("pythonw.exe").exists():
        exe = exe.with_name("pythonw.exe")
    return [str(exe), "-m", "companion"]


def mind_command() -> tuple[str, list[str]]:
    """(program, args) for the persona's mind, which reads its messages on stdin."""
    if is_frozen():
        return str(cli_exe()), ["mind"]
    return sys.executable, ["-m", "mind"]
