import pytest

from companion.placement import GAP, MARGIN, TAIL_MIN, Placement, Rect, clamp_into, place, resize_keep_feet

# The design's measured setup: canvas 322x128 (Segoe UI at 1x), screen 0 at 1x and
# screen 1 at 1.5x to its right and 339 px higher. Areas are availableGeometry().
CANVAS = (322, 128)
SCREEN0 = Rect(0, 0, 3440, 1392)
SCREEN1 = Rect(3440, -339, 2560, 1392)
# Pet windows at S, M and L for the 412x344 frame.
PET_SIZES = ((247, 206), (329, 275), (412, 344))
M_W, M_H = 329, 275


def pet_at(x, y, size=(M_W, M_H)):
    return Rect(x, y, *size)


def area_for(pet):
    """The screen holding the pet's centre, as bubble.screen_area picks it (screen 0 off both)."""
    return SCREEN1 if pet.cx >= SCREEN0.right else SCREEN0


# --- Rect ---------------------------------------------------------------------------------


def test_rect_edges_are_exclusive():
    rect = Rect(10, 20, 30, 40)
    assert (rect.right, rect.bottom, rect.cx) == (40, 60, 25)


def test_rect_contains():
    assert SCREEN0.contains(SCREEN0)
    assert SCREEN0.contains(Rect(0, 0, 322, 128))
    assert SCREEN0.contains(Rect(3440 - 322, 1392 - 128, 322, 128))  # touching the far edges
    assert not SCREEN0.contains(Rect(3440 - 321, 0, 322, 128))
    assert not SCREEN0.contains(Rect(-1, 0, 322, 128))
    assert not SCREEN0.contains(Rect(0, 1392 - 127, 322, 128))
    assert not Rect(0, 0, 10, 10).contains(SCREEN0)


# --- the worked numbers --------------------------------------------------------------------


def test_saved_spot_at_m_goes_below():
    assert place(pet_at(3111, 26), CANVAS, SCREEN0) == Placement(Rect(3110, 305, 322, 128), True, 165)


def test_mid_screen_goes_above():
    assert place(pet_at(1500, 600), CANVAS, SCREEN0) == Placement(Rect(1503, 468, 322, 128), False, 161)


def test_left_edge_clamps_x_and_moves_the_tail():
    result = place(pet_at(0, 600), CANVAS, SCREEN0)
    assert result.rect == Rect(8, 468, 322, 128)
    assert result.tail_x == 156
    assert not result.below


def test_top_of_the_raised_screen_goes_below():
    result = place(pet_at(4000, -330), CANVAS, SCREEN1)
    assert result.below
    assert (result.rect.x, result.rect.y) == (4003, -51)
    assert result.tail_x == 161


def test_flip_threshold_is_pet_y_140():
    at_140 = place(pet_at(1500, 140), CANVAS, SCREEN0)
    assert not at_140.below
    assert at_140.rect.y == MARGIN
    at_139 = place(pet_at(1500, 139), CANVAS, SCREEN0)
    assert at_139.below
    assert at_139.rect.y == 139 + M_H + GAP


def test_side_depends_on_position_only():
    # No hysteresis: the same pet position always gives the same side, whatever came before.
    for y in (139, 140, 139, 600, 139):
        assert place(pet_at(1500, y), CANVAS, SCREEN0).below is (y < 140)


# --- side and clamps -------------------------------------------------------------------------


def test_neither_side_fits_takes_the_one_with_more_room():
    area = Rect(0, 0, 1000, 400)
    more_below = place(pet_at(100, 50), CANVAS, area)  # 50 px above, 75 px below
    assert more_below.below
    assert more_below.rect.y == area.bottom - MARGIN - CANVAS[1]
    more_above = place(pet_at(100, 100), CANVAS, area)  # 100 px above, 25 px below
    assert not more_above.below
    assert more_above.rect.y == area.y + MARGIN


def test_bottom_of_the_screen_goes_above():
    pet = pet_at(1500, SCREEN0.bottom - M_H)
    result = place(pet, CANVAS, SCREEN0)
    assert not result.below
    assert result.rect.bottom == pet.y - GAP


def test_anchor_x_moves_the_bubble_with_the_head():
    centred = place(pet_at(1500, 600), CANVAS, SCREEN0)
    left_head = place(pet_at(1500, 600), CANVAS, SCREEN0, anchor_x=0.25)
    assert left_head.rect.x == 1500 + round(0.25 * M_W) - CANVAS[0] // 2
    assert left_head.rect.x < centred.rect.x
    assert left_head.tail_x == centred.tail_x == CANVAS[0] // 2


def test_tail_stays_clear_of_the_corners_off_the_left_edge():
    result = place(pet_at(-300, 600), CANVAS, SCREEN0)
    assert result.rect.x == MARGIN
    assert result.tail_x == TAIL_MIN


def test_tail_stays_clear_of_the_corners_off_the_right_edge():
    result = place(pet_at(SCREEN0.right - 20, 600), CANVAS, SCREEN0)
    assert result.rect.right == SCREEN0.right - MARGIN
    assert result.tail_x == CANVAS[0] - TAIL_MIN


def test_area_smaller_than_the_canvas_pins_to_the_margin():
    area = Rect(100, 100, 200, 100)
    for pet in (pet_at(0, 0), pet_at(150, 120), pet_at(900, 900)):
        result = place(pet, CANVAS, area)
        assert (result.rect.x, result.rect.y) == (area.x + MARGIN, area.y + MARGIN)
        assert (result.rect.w, result.rect.h) == CANVAS


@pytest.mark.parametrize("size", PET_SIZES)
def test_grid_across_both_screens_stays_inside_the_area(size):
    w, h = CANVAS
    for x in range(-500, SCREEN1.right + 500, 97):
        for y in range(-800, SCREEN0.bottom + 400, 61):
            pet = pet_at(x, y, size)
            area = area_for(pet)
            result = place(pet, CANVAS, area)
            assert area.contains(result.rect), (pet, result)
            assert (result.rect.w, result.rect.h) == CANVAS
            assert TAIL_MIN <= result.tail_x <= w - TAIL_MIN, (pet, result)
            fits_above = pet.y - GAP - h >= area.y + MARGIN
            fits_below = pet.bottom + GAP + h <= area.bottom - MARGIN
            if area.contains(pet) and (fits_above or fits_below):
                # A pet fully on screen is never covered by its bubble.
                if result.below:
                    assert result.rect.y == pet.bottom + GAP, (pet, result)
                else:
                    assert result.rect.bottom == pet.y - GAP, (pet, result)


def test_placement_is_stable_across_screens_at_the_boundary():
    # The pet's centre picks the area, so the bubble jumps screens with it and stays inside.
    left = pet_at(SCREEN0.right - M_W // 2 - 1, 600)
    right = pet_at(SCREEN0.right - M_W // 2 + 1, 600)
    assert area_for(left) == SCREEN0
    assert area_for(right) == SCREEN1
    assert SCREEN0.contains(place(left, CANVAS, SCREEN0).rect)
    assert SCREEN1.contains(place(right, CANVAS, SCREEN1).rect)


# --- clamp_into ----------------------------------------------------------------------------------


def test_clamp_into_leaves_a_rect_inside_alone():
    rect = Rect(100, 100, M_W, M_H)
    assert clamp_into(rect, SCREEN0) == rect


def test_clamp_into_pulls_an_overhang_back():
    # A saved spot that fit the old window size overhangs onto screen 1 at 329 px wide.
    assert clamp_into(Rect(3168, 26, M_W, M_H), SCREEN0) == Rect(3111, 26, M_W, M_H)
    assert clamp_into(Rect(-50, -20, M_W, M_H), SCREEN0) == Rect(0, 0, M_W, M_H)
    assert clamp_into(Rect(100, 1300, M_W, M_H), SCREEN0) == Rect(100, 1392 - M_H, M_W, M_H)
    assert clamp_into(Rect(4000, -400, M_W, M_H), SCREEN1) == Rect(4000, -339, M_W, M_H)


def test_clamp_into_pins_an_oversized_rect_to_the_area_origin():
    assert clamp_into(Rect(500, 500, 4000, 2000), SCREEN0) == Rect(0, 0, 4000, 2000)
    assert clamp_into(Rect(0, 0, 3000, 1500), SCREEN1) == Rect(3440, -339, 3000, 1500)


# --- resize_keep_feet ------------------------------------------------------------------------------


@pytest.mark.parametrize(("w", "h"), PET_SIZES)
def test_resize_keeps_the_bottom_centre(w, h):
    rect = Rect(1000, 500, M_W, M_H)
    resized = resize_keep_feet(rect, w, h, SCREEN0)
    assert (resized.w, resized.h) == (w, h)
    assert resized.bottom == rect.bottom
    assert abs(resized.cx - rect.cx) <= 1


def test_resize_round_trip_does_not_drift():
    rect = Rect(1001, 500, M_W, M_H)
    for w, h in (PET_SIZES[0], PET_SIZES[2], PET_SIZES[1], PET_SIZES[2], PET_SIZES[0], PET_SIZES[1]):
        rect = resize_keep_feet(rect, w, h, SCREEN0)
        assert rect.bottom == 775
        assert abs(rect.cx - 1165) <= 1
    assert rect == Rect(1001, 500, M_W, M_H)


def test_resize_clamps_into_the_area():
    # At the bottom right corner, growing to L would overhang the right edge.
    rect = Rect(SCREEN0.right - M_W, SCREEN0.bottom - M_H, M_W, M_H)
    resized = resize_keep_feet(rect, 412, 344, SCREEN0)
    assert SCREEN0.contains(resized)
    assert resized.bottom == SCREEN0.bottom
    assert resized.right == SCREEN0.right


def test_resize_near_the_top_keeps_the_feet_when_it_fits():
    rect = Rect(1000, 0, 247, 206)
    resized = resize_keep_feet(rect, 412, 344, SCREEN0)
    assert resized.y == 0  # 344 tall from feet at 206 would start above the area
    assert resized.bottom == 344
