"""Her API key, set from the tray: kept only as your Windows user environment variable. No Qt.

The mind reads CC_COMPANION_API_KEY (then ANTHROPIC_API_KEY) from its environment, else
from HKCU\\Environment (mind/voice.py: find_api_key). Saving writes HKCU\\Environment, the
place `setx` writes, tells Windows the environment changed, and sets it in this process
too, so a mind started now inherits it. The key is never logged, printed, or written to
any file of the app's.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

KEY_NAME = "CC_COMPANION_API_KEY"
KEY_NAMES = (KEY_NAME, "ANTHROPIC_API_KEY")  # the order mind/voice.py reads them in


def _read_user_env(name: str) -> str | None:
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    return value if isinstance(value, str) and value else None


def _write_user_env(name: str, value: str) -> bool:
    if sys.platform != "win32":
        return False
    import ctypes
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
    except OSError:
        return False
    # WM_SETTINGCHANGE "Environment", so programs started later (a new terminal) see it.
    result = ctypes.c_size_t()
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 2000, ctypes.byref(result))
    return True


def has_api_key(read: Callable[[str], str | None] | None = None) -> bool:
    """Whether the mind will find a key, without handing it to anyone."""
    read = read or _read_user_env
    return any(os.environ.get(name) or read(name) for name in KEY_NAMES)


def save_api_key(key: str, write: Callable[[str, str], bool] | None = None) -> bool:
    """Save `key` as CC_COMPANION_API_KEY; False if it is empty, has spaces inside, or could not be saved."""
    key = key.strip()
    if not key or any(ch.isspace() for ch in key):
        return False
    if not (write or _write_user_env)(KEY_NAME, key):
        return False
    os.environ[KEY_NAME] = key
    return True
