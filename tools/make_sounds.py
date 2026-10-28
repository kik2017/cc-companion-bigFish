"""Write the done and error cues into companion/assets/sounds/.

    python tools/make_sounds.py

Original procedural tones, stdlib only: a soft rising two-note chime for done and two
falling lower notes for error, each under half a second. Deterministic: rerunning
rewrites identical files.
"""

from __future__ import annotations

import math
import struct
import sys
import wave
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "companion" / "assets" / "sounds"
RATE = 44_100
PEAK = 0.35  # of full scale: a notification, not an alarm

# name: [(frequency Hz, start s, length s, decay per s, third-harmonic share)]
CUES = {
    "done": [(659.25, 0.00, 0.20, 9.0, 0.08), (987.77, 0.11, 0.30, 7.0, 0.08)],
    "error": [(392.00, 0.00, 0.20, 8.0, 0.18), (311.13, 0.15, 0.28, 7.0, 0.18)],
}


def render(notes: list[tuple[float, float, float, float, float]]) -> list[float]:
    length = max(start + dur for _, start, dur, _, _ in notes)
    samples = [0.0] * int(length * RATE)
    for freq, start, dur, decay, third in notes:
        first = int(start * RATE)
        for i in range(int(dur * RATE)):
            t = i / RATE
            attack = min(1.0, t / 0.005)  # 5 ms, no click
            release = min(1.0, (dur - t) / 0.02)  # 20 ms fade at the end
            envelope = attack * release * math.exp(-decay * t)
            wave_ = math.sin(2 * math.pi * freq * t) + third * math.sin(2 * math.pi * 3 * freq * t)
            samples[first + i] += envelope * wave_ / (1 + third)
    top = max(abs(s) for s in samples)
    return [s * PEAK / top for s in samples]


def write(path: Path, samples: list[float]) -> None:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(b"".join(struct.pack("<h", round(s * 32767)) for s in samples))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, notes in CUES.items():
        path = OUT / f"{name}.wav"
        write(path, render(notes))
        print(f"wrote {path} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
