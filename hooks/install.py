"""Merge cc-companion's hooks into Claude Code settings, or take them out again.

    python hooks/install.py                # merge into ~/.claude/settings.json
    python hooks/install.py --plugin       # statusLine only; the plugin provides the hooks
    python hooks/install.py --dry-run      # print the merged settings, write nothing
    python hooks/install.py --uninstall    # remove only cc-companion's entries
    python hooks/install.py --exe PATH     # hooks for the portable build's cc-companion-cli.exe
    python hooks/install.py --write-plugin-hooks   # regenerate hooks/hooks.json (development)

The portable build does the same from its tray menu (Connect to Claude Code), through
companion/connect.py; its command hooks run `cc-companion-cli.exe launch` and its
statusLine `cc-companion-cli.exe statusline`.

A plugin cannot ship a statusLine, so with the plugin installed this script still adds the
statusLine, and --plugin also takes cc-companion's hooks out of settings.json so no event
fires twice. Without the flag it does the same on its own when settings.json has the
plugin enabled.

Existing hooks are kept: our matcher groups are appended per event and never replace
anything, and re-running swaps our own entries for fresh ones, so it is safe to repeat.
A timestamped backup is written next to the settings file before every change. The
statusLine entry is added once companion/statusline.py exists, and never over a
statusLine you already have unless --replace-statusline is given.

The statusLine is the one shell-form command: Claude Code runs it in Git Bash or in
PowerShell, so it is written unquoted, in forward slashes, as `python -I -S script`. A
path with spaces or symbols falls back to its 8.3 short form; when there is none the
statusLine is skipped with a note and the hooks are installed without it.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNIPPET = Path(__file__).with_name("settings.snippet.json")
PLUGIN_HOOKS = Path(__file__).with_name("hooks.json")
PLUGIN_ID = "cc-companion@"
# The plugin loads in place from the clone (a relative-path plugin in a local marketplace),
# so its hooks run the clone's own venv. Windows layout; elsewhere use this script instead.
PLUGIN_PYTHON = "${CLAUDE_PLUGIN_ROOT}/.venv/Scripts/python.exe"
MARKER = "from=cc-companion"
DEFAULT_PORT = 47621
CLI_NAME = "cc-companion-cli.exe"  # companion/frozen.py: the portable build's console program
# A path that parses as one plain word in both Git Bash and PowerShell. Quoting is no way
# out: a quoted leading path is a parse error in PowerShell, and & ( ) ' $ ; or a space
# would be split or interpreted by one shell or the other.
SAFE_PATH = re.compile(r"[A-Za-z]:/[A-Za-z0-9_./~-]*" if sys.platform == "win32" else r"/[A-Za-z0-9_./~-]*")
STATUSLINE_SKIPPED = (
    "statusLine skipped: interpreter or repo path has characters that break Git Bash or PowerShell"
    " and no short path is available; install to a path without spaces or symbols"
)


def posix(path: Path | str) -> str:
    return str(path).replace("\\", "/")


def default_python() -> Path:
    venv = ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    return venv if venv.exists() else Path(sys.executable)


def _short_path(path: Path | str) -> str | None:
    """The 8.3 short form of an existing path, in forward slashes; None off Windows or on failure."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    get_short = ctypes.WinDLL("kernel32").GetShortPathNameW  # own instance; windll stays untouched
    get_short.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD)
    get_short.restype = wintypes.DWORD
    long_path = str(path).replace("/", "\\")
    try:
        size = get_short(long_path, None, 0)  # buffer length needed, counting the terminator
        if not size:
            return None  # missing path or no access
        buffer = ctypes.create_unicode_buffer(size)
        written = get_short(long_path, buffer, size)
    except (OSError, ValueError):  # ValueError: an embedded NUL
        return None
    return posix(buffer.value) if 0 < written < size else None


def shell_safe(path: Path | str) -> str | None:
    """`path` in forward slashes if SAFE_PATH allows it, else its short form if that is allowed, else None.

    On a volume with 8.3 names turned off the short form is the long path again, so it fails
    the same check and the answer is None.
    """
    plain = posix(path)
    if SAFE_PATH.fullmatch(plain):
        return plain
    short = _short_path(path)
    return short if short is not None and SAFE_PATH.fullmatch(short) else None


def statusline_command(python: Path | str, root: Path | str) -> str | None:
    """The statusLine command line, or None when the interpreter or the repo path is unsafe."""
    exe, repo = shell_safe(python), shell_safe(root)
    if exe is None or repo is None:
        return None
    return f"{exe} -I -S {repo}/companion/statusline.py"


def render_snippet(python: Path | str, root: Path | str, port: int) -> dict:
    """The snippet with every placeholder filled; the statusLine command is "" when unsafe."""
    text = SNIPPET.read_text(encoding="utf-8")
    replacements = {
        "{{PYTHON}}": posix(python),
        "{{ROOT}}": posix(root),
        "{{HOOK_URL}}": f"http://127.0.0.1:{port}/hook?{MARKER}",
        "{{STATUSLINE_COMMAND}}": statusline_command(python, root) or "",
    }
    for placeholder, value in replacements.items():
        if placeholder not in text:
            raise RuntimeError(f"{SNIPPET.name} lost its {placeholder} placeholder")
        text = text.replace(placeholder, json.dumps(value)[1:-1])
    return json.loads(text)


def exe_statusline_command(cli: Path | str) -> str | None:
    """The portable build's statusLine command line, or None when its path is unsafe."""
    exe = shell_safe(cli)
    return None if exe is None else f"{exe} statusline"


def render_exe_snippet(cli: Path | str, port: int) -> dict:
    """The snippet for the portable build: its command hooks and statusLine run cc-companion-cli.exe."""
    snippet = render_snippet(cli, Path(cli).parent, port)
    for groups in snippet["hooks"].values():
        for group in groups:
            for handler in group["hooks"]:
                if handler.get("type") == "command":
                    handler["command"] = posix(cli)
                    handler["args"] = ["launch"] + handler.get("args", [])[1:]
    snippet["statusLine"]["command"] = exe_statusline_command(cli) or ""
    return snippet


def render_plugin_hooks(port: int = DEFAULT_PORT) -> dict:
    """hooks/hooks.json for the plugin: the snippet's hooks with ${CLAUDE_PLUGIN_ROOT} paths, no statusLine."""
    return {"hooks": render_snippet(PLUGIN_PYTHON, "${CLAUDE_PLUGIN_ROOT}", port)["hooks"]}


def plugin_enabled(settings: dict) -> bool:
    enabled = settings.get("enabledPlugins")
    return isinstance(enabled, dict) and any(
        isinstance(key, str) and key.startswith(PLUGIN_ID) and value is True for key, value in enabled.items()
    )


def is_our_handler(handler: object) -> bool:
    if not isinstance(handler, dict):
        return False
    if handler.get("type") == "http":
        return MARKER in str(handler.get("url", ""))
    if handler.get("type") == "command":
        args = handler.get("args") if isinstance(handler.get("args"), list) else []
        if posix(str(handler.get("command", ""))).endswith("/" + CLI_NAME) and args[:1] == ["launch"]:
            return True
        return any(isinstance(a, str) and posix(a).endswith("companion/launcher.py") for a in args)
    return False


def is_our_statusline(status_line: object) -> bool:
    if not isinstance(status_line, dict):
        return False
    command = posix(str(status_line.get("command", "")))
    return "companion/statusline.py" in command or command.endswith(f"/{CLI_NAME} statusline")


def remove_ours(settings: dict) -> dict:
    settings = copy.deepcopy(settings)
    hooks = settings.get("hooks")
    if isinstance(hooks, dict):
        for event in list(hooks):
            groups = hooks[event]
            if not isinstance(groups, list):
                continue
            kept = []
            for group in groups:
                handlers = group.get("hooks") if isinstance(group, dict) else None
                if isinstance(handlers, list) and handlers:
                    remaining = [h for h in handlers if not is_our_handler(h)]
                    if not remaining:
                        continue
                    group = {**group, "hooks": remaining}
                kept.append(group)
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]
        if not hooks:
            del settings["hooks"]
    if is_our_statusline(settings.get("statusLine")):
        del settings["statusLine"]
    return settings


def merge(settings: dict, snippet: dict, *, statusline: bool, replace_statusline: bool = False) -> tuple[dict, list[str]]:
    """Return the merged settings and notes for the user."""
    notes: list[str] = []
    merged = remove_ours(settings)
    hooks = merged.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError('"hooks" in settings is not an object; fix it by hand first')
    for event, groups in snippet["hooks"].items():
        existing = hooks.setdefault(event, [])
        if not isinstance(existing, list):
            raise ValueError(f'"hooks.{event}" in settings is not a list; fix it by hand first')
        existing.extend(copy.deepcopy(groups))
    if not hooks:
        del merged["hooks"]
    if statusline:
        current = merged.get("statusLine")
        if current is None or replace_statusline:
            merged["statusLine"] = copy.deepcopy(snippet["statusLine"])
        else:
            notes.append("kept your existing statusLine; pass --replace-statusline to use the companion's")
    return merged, notes


def write_atomically(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def back_up(path: Path) -> Path:
    """Copy `path` to a new `.bak-<stamp>[-n]` file next to it; an existing backup is never overwritten."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    data = path.read_bytes()
    n = 0
    while True:
        backup = path.with_name(f"{path.name}.bak-{stamp}" + (f"-{n}" if n else ""))
        try:
            with open(backup, "xb") as file:
                file.write(data)
        except FileExistsError:
            n += 1
            continue
        shutil.copystat(path, backup)
        return backup


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--settings", type=Path, default=Path.home() / ".claude" / "settings.json")
    parser.add_argument("--python", type=Path, default=default_python(), help="interpreter for the command hooks")
    parser.add_argument("--port", type=int, default=int(os.environ.get("CC_COMPANION_PORT", DEFAULT_PORT)))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--plugin", action="store_true", help="statusLine only; the cc-companion plugin provides the hooks")
    parser.add_argument("--replace-statusline", action="store_true")
    parser.add_argument("--exe", type=Path, help="install for the portable build: the path of its cc-companion-cli.exe")
    parser.add_argument("--write-plugin-hooks", action="store_true", help="regenerate hooks/hooks.json and exit")
    args = parser.parse_args(argv)

    if args.write_plugin_hooks:
        PLUGIN_HOOKS.write_text(json.dumps(render_plugin_hooks(args.port), indent=2) + "\n", encoding="utf-8")
        print(f"wrote {PLUGIN_HOOKS}")
        return 0

    path: Path = args.settings.expanduser()
    try:
        current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError) as error:
        print(f"could not read {path} ({error}); nothing changed", file=sys.stderr)
        return 1
    if not isinstance(current, dict):
        print(f"{path} does not hold a JSON object; nothing changed", file=sys.stderr)
        return 1

    notes: list[str] = []
    if args.uninstall:
        updated = remove_ours(current)
    else:
        if args.exe is not None:
            if not args.exe.exists():
                print(f"{CLI_NAME} not found at {args.exe}", file=sys.stderr)
                return 1
            snippet = render_exe_snippet(args.exe, args.port)
            status_cmd = exe_statusline_command(args.exe)
        else:
            if not args.python.exists():
                print(f"python not found at {args.python}; pass --python", file=sys.stderr)
                return 1
            snippet = render_snippet(args.python, ROOT, args.port)
            status_cmd = statusline_command(args.python, ROOT)
        if args.plugin or plugin_enabled(current):
            snippet = {"hooks": {}, "statusLine": snippet["statusLine"]}
            if not args.plugin:
                notes.append("the cc-companion plugin is enabled and provides the hooks; installed only the statusLine")
        has_script = args.exe is not None or (ROOT / "companion" / "statusline.py").exists()
        try:
            updated, merge_notes = merge(
                current,
                snippet,
                statusline=has_script and status_cmd is not None,
                replace_statusline=args.replace_statusline,
            )
            notes.extend(merge_notes)
        except ValueError as error:
            print(f"{error}; nothing changed", file=sys.stderr)
            return 1
        if has_script and status_cmd is None:
            notes.append(STATUSLINE_SKIPPED)

    if args.dry_run:
        print(json.dumps(updated, indent=2, ensure_ascii=False))
        for note in notes:
            print(f"note: {note}", file=sys.stderr)  # stdout stays valid JSON
        return 0
    if updated == current:
        print(f"{path} already up to date")
    else:
        if path.exists():
            print(f"backup: {back_up(path)}")
        write_atomically(path, updated)
        verb = (
            "removed cc-companion entries from" if args.uninstall
            else "merged the cc-companion statusLine into" if not snippet["hooks"]
            else "merged cc-companion hooks into"
        )  # fmt: skip
        print(f"{verb} {path}")
    for note in notes:
        print(f"note: {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
