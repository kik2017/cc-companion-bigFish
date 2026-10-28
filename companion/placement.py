"""Where the bubble and the chat box sit next to the pet, and keeping rects on screen. Pure, no Qt.

Rects are (x, y, w, h) in logical pixels with exclusive right and bottom edges. Convert
from Qt with the width and height; QRect.right() and bottom() are inclusive and are
never used.

The bubble canvas has a fixed size, so its side depends only on where the pet is: above
the frame top when that fits inside the area with MARGIN to spare, else below the feet
when that fits, else the side with more room. There is no hysteresis. The bubble is then
clamped fully inside the area, and the tail points at the anchor, the centre of the
skin's head zone.
"""

from __future__ import annotations

from dataclasses import dataclass

GAP = 4  # between the pet's frame and the bubble canvas
MARGIN = 8  # between the bubble canvas and the edge of the area
TAIL_MIN = 20  # RADIUS 12 + TAIL_W/2 8: the tail tip stays clear of the rounded corners


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    @property
    def cx(self) -> int:
        return self.x + self.w // 2

    def contains(self, other: Rect) -> bool:
        return (
            self.x <= other.x
            and self.y <= other.y
            and other.right <= self.right
            and other.bottom <= self.bottom
        )


@dataclass(frozen=True)
class Placement:
    rect: Rect  # the bubble canvas
    below: bool  # True: under the pet, tail pointing up
    tail_x: int  # tail tip, from the canvas's left edge


def _clamp(value: int, low: int, high: int) -> int:
    """`value` limited to [low, high]; `low` wins when the range is empty."""
    return max(low, min(value, high))


def place(pet: Rect, canvas: tuple[int, int], area: Rect, anchor_x: float = 0.5) -> Placement:
    """The bubble canvas of size `canvas` (w, h) for a pet frame at `pet` inside `area`.

    `anchor_x` is the head zone's centre as a fraction of the frame width. `area` is the
    available geometry of the screen holding the pet's centre, so the bubble always
    shares the pet's DPI. When the area is smaller than the canvas, the canvas pins to
    the area's top left corner plus MARGIN.
    """
    w, h = canvas
    ax = pet.x + round(anchor_x * pet.w)
    above_y = pet.y - GAP - h  # the frame top: the only anchor that never overlaps the head
    below_y = pet.bottom + GAP
    if above_y >= area.y + MARGIN:
        below = False
    elif below_y + h <= area.bottom - MARGIN:
        below = True
    else:
        below = area.bottom - pet.bottom > pet.y - area.y
    y = _clamp(below_y if below else above_y, area.y + MARGIN, area.bottom - MARGIN - h)
    x = _clamp(ax - w // 2, area.x + MARGIN, area.right - MARGIN - w)
    tail_x = _clamp(ax - x, TAIL_MIN, w - TAIL_MIN)
    return Placement(Rect(x, y, w, h), below, tail_x)


def clamp_into(rect: Rect, area: Rect) -> Rect:
    """`rect` moved the least distance to lie fully inside `area`; an oversized rect pins to the area's origin."""
    x = _clamp(rect.x, area.x, area.right - rect.w)
    y = _clamp(rect.y, area.y, area.bottom - rect.h)
    return Rect(x, y, rect.w, rect.h)


def resize_keep_feet(rect: Rect, w: int, h: int, area: Rect) -> Rect:
    """`rect` resized to w x h around the same bottom centre, then clamped into `area`."""
    return clamp_into(Rect(rect.cx - w // 2, rect.bottom - h, w, h), area)


def place_beside(pet: Rect, size: tuple[int, int], area: Rect) -> Rect:
    """The chat box of `size` (w, h) beside the pet, level with its lower half, inside `area`.

    The bubble already owns the space above or below the pet, so the box goes to the left
    of the frame when that fits with MARGIN to spare, else to the right, else the side with
    more room; then it is clamped fully inside the area.
    """
    w, h = size
    left_x = pet.x - GAP - w
    right_x = pet.right + GAP
    if left_x >= area.x + MARGIN:
        x = left_x
    elif right_x + w <= area.right - MARGIN:
        x = right_x
    else:
        x = left_x if pet.x - area.x > area.right - pet.right else right_x
    y = pet.y + pet.h * 3 // 4 - h // 2
    x = _clamp(x, area.x + MARGIN, area.right - MARGIN - w)
    y = _clamp(y, area.y + MARGIN, area.bottom - MARGIN - h)
    return Rect(x, y, w, h)
