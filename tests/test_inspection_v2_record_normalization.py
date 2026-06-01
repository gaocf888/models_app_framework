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


def test_tube_unchanged_for_non_integer_non_combo() -> None:
    """非纯整数且非「数字-数字」组合形态时，跳过管号正负号校正。"""
    loc, row, tube, w = normalize_location_row_tube("右墙", "向上第1根", "12B", evidence="")
    assert tube == "12B"
    assert not any("tube_sign" in x for x in w)


def test_combo_index_splits_and_skips_wall_rules() -> None:
    loc, row, tube, w = normalize_location_row_tube("水冷壁右墙B02吹灰器", "2-1", "1", evidence="")
    assert row == "2"
    assert tube == "1"
    assert any("combo_index" in x for x in w)
    assert not any("row_fix" in x for x in w)


def test_combo_index_in_tube_field() -> None:
    loc, row, tube, w = normalize_location_row_tube("水冷壁左墙", "1", "3-5", evidence="")
    assert row == "3"
    assert tube == "5"
    assert any("combo_index" in x for x in w)


def test_combo_index_skips_reheater_corruption() -> None:
    loc, row, tube, w = normalize_device_row_tube_by_location("高温过热器", "2-1", "1")
    assert row == "2"
    assert tube == "1"
    assert any("combo_index" in x for x in w)
    assert not any("row_digits" in x for x in w)


def test_combo_index_skips_tube_sign() -> None:
    loc, row, tube, w = normalize_location_row_tube("水冷壁右墙", "2-1", "1", evidence="向上")
    assert row == "2"
    assert tube == "1"
    assert not any("tube_sign" in x for x in w)


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


def test_wall_type_row_forced_one_when_tube_already_has_index() -> None:
    """LLM 将编号同时写入行号与管号时，行号仍应强制为 1。"""
    loc, row, tube, w = normalize_location_row_tube(
        "水冷壁右墙第1层第1贴壁风孔", "2", "-2", evidence=""
    )
    assert row == "1"
    assert tube == "-2"
    assert any("row_fix" in x for x in w)


def test_wall_type_left_wall_row5_tube6() -> None:
    loc, row, tube, w = normalize_location_row_tube("水冷壁左墙第1层第1贴壁风孔", "5", "6", evidence="")
    assert row == "1"
    assert tube == "6"


def test_neutral_location_unchanged() -> None:
    loc, row, tube, w = normalize_location_row_tube("右墙B02吹灰器", "2", "8", evidence="")
    assert row == "2"
    assert tube == "8"
    assert not any("row_fix" in x or "tube_fix" in x for x in w)


def test_apply_deterministic_rules_on_dict() -> None:
    from app.inspection_v2.record_normalization import apply_deterministic_rules_to_record

    out = apply_deterministic_rules_to_record(
        {"检测位置": "水冷壁右墙第1层第1贴壁风孔", "行号": "3", "管号": "3", "壁厚": 7.3}
    )
    assert out["行号"] == "1"
    assert out["管号"] == "3"


def test_apply_deterministic_rules_english_keys_get_chinese_fields() -> None:
    from app.inspection_v2.record_normalization import apply_deterministic_rules_to_record

    out = apply_deterministic_rules_to_record(
        {"location": "水冷壁右墙", "row_no": "2", "tube_no": "-2", "thickness": 7.4}
    )
    assert out["row_no"] == "1"
    assert out["行号"] == "1"
    assert out["tube_no"] == "-2"
    assert out["管号"] == "-2"
