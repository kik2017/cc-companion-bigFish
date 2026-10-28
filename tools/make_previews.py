"""Render the README's animated previews from the bundled character's frames.

    python tools/make_previews.py

Needs Pillow (`pip install -e .[docs]`). Writes docs/images/anim/<name>.webp: each preview
at 0.4 size and 8 fps, looping, with transparency, plus a one-shot clip's last frame held
for a moment before it starts again; about 5 MB in all, which a README can carry. GitHub shows animated WebP inline. The previews are
made from the character art, so they carry its license: CC BY-NC-SA 4.0, non-commercial,
with its credits in companion/assets/character/LICENSE.txt.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
CHARACTER = ROOT / "companion" / "assets" / "character"
OUT = ROOT / "docs" / "images" / "anim"
SCALE = 0.4
STEP = 3  # every third frame: 24 fps becomes 8
HOLD_MS = 900  # a one-shot clip's last frame, before the preview loops
QUALITY = 60
METHOD = 4  # 6 packs a little tighter but takes minutes per preview

# preview name: the clips it plays, in order (the drag preview is a drag, then the landing).
# Searching plays the same frames as waiting, so the waiting preview stands for both.
PREVIEWS = {
    "idle": ["idle"],
    "thinking": ["thinking"],
    "editing": ["working"],
    "running": ["working_command"],
    "waiting": ["waiting"],
    "done": ["success"],
    "error": ["error"],
    "head_pat": ["head_pat"],
    "poke": ["poke"],
    "tail": ["tail"],
    "drag": ["dragging", "dragging_release"],
    "stretch": ["idle_stretch"],
    "yawn": ["idle_yawn"],
    "snack": ["eat_token"],
}


def frames(clip: str, spec: dict) -> tuple[list[Image.Image], list[int]]:
    paths = sorted(p for p in (CHARACTER / spec["dir"]).iterdir() if p.suffix.lower() in (".webp", ".png"))[::STEP]
    images = []
    for path in paths:
        with Image.open(path) as image:
            rgba = image.convert("RGBA")
            size = (round(rgba.width * SCALE), round(rgba.height * SCALE))
            images.append(rgba.resize(size, Image.Resampling.LANCZOS))
    durations = [spec["frameMs"] * STEP] * len(images)
    if not spec.get("loop") and durations:
        durations[-1] = HOLD_MS
    return images, durations


def main() -> int:
    clips = json.loads((CHARACTER / "manifest.json").read_text(encoding="utf-8"))["clips"]
    OUT.mkdir(parents=True, exist_ok=True)
    total = 0
    for name, sequence in PREVIEWS.items():
        images: list[Image.Image] = []
        durations: list[int] = []
        for clip in sequence:
            more, times = frames(clip, clips[clip])
            images += more
            durations += times
        path = OUT / f"{name}.webp"
        images[0].save(
            path, save_all=True, append_images=images[1:], duration=durations, loop=0,
            quality=QUALITY, method=METHOD, lossless=False,
        )  # fmt: skip
        size = path.stat().st_size
        total += size
        print(f"{path.relative_to(ROOT)}: {len(images)} frames, {size / 1024:.0f} KB", flush=True)
    print(f"{len(PREVIEWS)} previews, {total / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
