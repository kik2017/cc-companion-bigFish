"""Draw the placeholder skin pack the tests use into tests/fixtures/placeholder-skin/.

    python tools/make_placeholder_skin.py

A colored disc per state with its verb, a tail nub on the right, and short clips for
every reaction and idle slot: small, square and quick to decode, so the window and skin
tests exercise every clip without the bundled character's 3,444 frames. Deterministic:
rerunning rewrites identical frames.
"""

from __future__ import annotations

import json
import math
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPolygonF

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures" / "placeholder-skin"
SIZE = 200

STATE_COLORS = {
    "idle": "#8a8f98",
    "thinking": "#d97757",
    "searching": "#4f8fd9",
    "editing": "#53a35a",
    "running": "#c9a227",
    "delegating": "#8e6bd9",
    "waiting": "#e0457b",
    "done": "#3fb68b",
    "error": "#d64545",
    "compacting": "#5aa7b0",
}
IDLE_COLOR = STATE_COLORS["idle"]

# name: (label, color, frames, frame_ms, loop, motion)
CLIPS = {
    **{
        state: (state, color, 8, 125, state not in ("done", "error"), "pulse" if state == "done" else "shake" if state == "error" else "breathe")
        for state, color in STATE_COLORS.items()
    },
    "pat": ("pat", IDLE_COLOR, 8, 100, False, "squash"),
    "poke": ("poke", IDLE_COLOR, 8, 90, False, "wobble"),
    "tail": ("tail", IDLE_COLOR, 8, 100, False, "wag"),
    "drag": ("drag", IDLE_COLOR, 6, 100, True, "dangle"),
    "land": ("land", IDLE_COLOR, 6, 90, False, "bounce"),
    "idle_blink": ("blink", IDLE_COLOR, 8, 110, False, "blink"),
    "idle_hop": ("hop", IDLE_COLOR, 8, 110, False, "hop"),
}


def shape(motion: str, t: float) -> tuple[float, float, float, float, float]:
    """(dx, dy, scale_x, scale_y, tail_angle) for progress t in [0, 1)."""
    wave = math.sin(t * 2 * math.pi)
    fade = 1 - t  # one-shot motions settle back to rest
    return {
        "breathe": (0, 0, 1 + 0.03 * wave, 1 + 0.03 * wave, 0),
        "pulse": (0, 0, 1 + 0.12 * fade * abs(wave), 1 + 0.12 * fade * abs(wave), 0),
        "shake": (8 * fade * wave, 0, 1, 1, 0),
        "squash": (0, 14 * fade * abs(wave), 1 + 0.10 * fade * abs(wave), 1 - 0.14 * fade * abs(wave), 0),
        "wobble": (10 * fade * wave, 0, 1 - 0.06 * fade * abs(wave), 1, 0),
        "wag": (0, 0, 1, 1, 35 * fade * wave),
        "dangle": (6 * wave, -6, 0.92, 1.08, 20 * wave),
        "bounce": (0, 10 * fade * abs(wave), 1 + 0.12 * fade, 1 - 0.12 * fade, 0),
        "blink": (0, 0, 1, 1 - 0.5 * max(0.0, wave) * (0.4 < t < 0.6), 0),
        "hop": (0, -22 * abs(wave) * fade, 1, 1, 0),
    }[motion]


def draw(label: str, color: str, motion: str, t: float) -> QImage:
    image = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    dx, dy, sx, sy, tail_angle = shape(motion, t)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    fill = QColor(color)

    # Tail nub in the lower right, inside the manifest's tail zone.
    painter.save()
    painter.translate(150 + dx, 140 + dy)
    painter.rotate(tail_angle)
    painter.setBrush(fill.darker(115))
    painter.drawPolygon(QPolygonF([QPointF(-8, -12), QPointF(42, 0), QPointF(-8, 12)]))
    painter.restore()

    radius = 72
    body = QRectF(-radius * sx, -radius * sy, 2 * radius * sx, 2 * radius * sy)
    body.translate(92 + dx, 108 + dy - radius * (sy - 1))
    painter.setBrush(fill)
    painter.drawEllipse(body)

    font = QFont()
    font.setPixelSize(22)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor("white"))
    painter.drawText(body, Qt.AlignmentFlag.AlignCenter, label)
    painter.end()
    return image


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)  # noqa: F841 - fonts need it
    if OUT.exists():
        shutil.rmtree(OUT)
    clips = {}
    for name, (label, color, count, frame_ms, loop, motion) in CLIPS.items():
        folder = OUT / name
        folder.mkdir(parents=True)
        for i in range(count):
            draw(label, color, motion, i / count).save(str(folder / f"{i + 1:04d}.png"))
        clips[name] = {"dir": name, "frameMs": frame_ms, "loop": loop}
    manifest = {
        "format": 1,
        "name": "placeholder",
        "frameSize": [SIZE, SIZE],
        "clips": clips,
        "states": {state: state for state in STATE_COLORS},
        "reactions": {"head": "pat", "body": "poke", "tail": "tail", "drag": "drag", "land": "land"},
        "idle": ["idle_blink", "idle_hop"],
        "zones": {"head": [0, 0, 1, 0.45], "tail": [0.72, 0.45, 1, 1]},
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {sum(c[2] for c in CLIPS.values())} frames in {len(CLIPS)} clips to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
