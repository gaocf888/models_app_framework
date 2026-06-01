from __future__ import annotations

from app.inspection_v2.record_normalization import (
    normalize_device_row_tube_by_location,
    normalize_location_row_tube,
)


def test_tube_negative_when_up_marker_in_row() -> None:
    loc, row, tube, w = normalize_location_row_tube("右墙", "向上第1根", "5", evidence="")
    assert tube == "-5"
    assert any("上" in x for x in w)


def test_tube_strip_negative_when_down_marker() -> None:
    loc, row, tube, w = normalize_location_row_tube("左墙", "向下", "-3", evidence="")
    assert tube == "3"
    assert w


def test_tube_unchanged_for_non_integer() -> None:
    loc, row, tube, w = normalize_location_row_tube("右墙", "2-6", "5-1", evidence="向上")
    assert tube == "5-1"
    assert not w


def test_wall_type_forces_row_one_and_migrates_number_to_tube() -> None:
    loc, row, tube, w = normalize_location_row_tube("水冷壁右墙B02吹灰器", "8", "1", evidence="")
    assert row == "1"
    assert tube == "8"
    assert any("row_fix" in x or "row_tube" in x for x in w)


def test_wall_type_row_one_when_already_correct() -> None:
    loc, row, tube, w = normalize_location_row_tube("包墙左墙", "1", "-5", evidence="向上")
    assert row == "1"
    assert tube == "-5"


def test_reheater_type_forces_tube_one_and_migrates_number_to_row() -> None:
    loc, row, tube, w = normalize_location_row_tube("屏过再热器D4", "1", "12", evidence="")
    assert row == "12"
    assert tube == "1"
    assert any("tube_fix" in x for x in w)


def test_reheater_type_strips_letters_from_row() -> None:
    loc, row, tube, w = normalize_device_row_tube_by_location("高温过热器", "A12", "1")
    assert row == "12"
    assert tube == "1"


def test_neutral_location_unchanged() -> None:
    loc, row, tube, w = normalize_location_row_tube("右墙B02吹灰器", "2", "8", evidence="")
    assert row == "2"
    assert tube == "8"
    assert not any("row_fix" in x or "tube_fix" in x for x in w)
