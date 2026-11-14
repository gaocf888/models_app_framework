"""季报 chapter_start 的 title_level 白名单。"""

from __future__ import annotations

from app.analysis_agent.chapter_levels import chapter_start_payload, title_level_for_slot


def test_title_level_whitelist() -> None:
    assert title_level_for_slot("quarterly_preface") == 1
    assert title_level_for_slot("regional_results") == 1
    assert title_level_for_slot("city_plain") == 2
    assert title_level_for_slot("district_intro") == 2
    assert title_level_for_slot("layer_intro") == 1
    assert title_level_for_slot("layer_results__l01") == 2
    assert title_level_for_slot("layer_results__l34") == 2
    assert title_level_for_slot("key_area") == 1
    assert title_level_for_slot("subsidence_forecast") == 1
    assert title_level_for_slot("conclusion") == 1
    assert title_level_for_slot("annex_intro") == 1


def test_title_level_omitted_for_other_slots() -> None:
    assert title_level_for_slot("cover") is None
    assert title_level_for_slot("district_results__chaoyang") is None
    assert title_level_for_slot("annex_ground") is None
    assert title_level_for_slot("annex_layer__l01") is None
    assert title_level_for_slot("station_ops") is None


def test_chapter_start_payload_includes_level_only_when_set() -> None:
    with_level = chapter_start_payload(
        slot_id="annex_intro",
        title="附表",
        kind="static_markdown",
        slot_index=10,
    )
    assert with_level["event"] == "analysis_agent_chapter_start"
    assert with_level["title"] == "附表"
    assert with_level["title_level"] == 1

    no_level = chapter_start_payload(
        slot_id="annex_ground",
        title="附表1 …",
        kind="static_markdown",
        slot_index=11,
    )
    assert "title_level" not in no_level
