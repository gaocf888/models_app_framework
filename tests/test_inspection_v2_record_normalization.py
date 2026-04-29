from __future__ import annotations

from app.inspection_v2.record_normalization import normalize_location_row_tube


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
