"""The desk: how long since the user last touched the keyboard or mouse. No network.

Only the time comes back, never which key or where the mouse went: Windows'
GetLastInputInfo gives the tick count of the last input in this session, and the idle
time is now minus that. Anywhere else, or if the call fails, it is None (unknown), and
the senses go by the user's actions alone.
"""

from __future__ import annotations

import ctypes
import sys


class _LastInputInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint32)]


def input_idle_s() -> float | None:
    """Seconds since the last keyboard or mouse input, or None if it cannot be read here."""
    if sys.platform != "win32":
        return None
    try:
        info = _LastInputInfo(ctypes.sizeof(_LastInputInfo), 0)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        tick = ctypes.windll.kernel32.GetTickCount
        tick.restype = ctypes.c_uint32
        return ((tick() - info.dwTime) & 0xFFFFFFFF) / 1000  # both wrap at 2**32 ms together
    except (AttributeError, OSError):
        return None
