"""V2-P2：监测方式覆盖名单。"""

from __future__ import annotations

from app.analysis_agent.deterministics.coverage import (
    canonical_project,
    layer0_mark_keys,
    load_device_projects_by_type,
    load_layered_marks,
    project_names,
)


def test_fcb_allowlist_non_empty() -> None:
    names = project_names("fcb")
    assert len(names) >= 40
    assert "F8(周村)" in names
    assert "F42(牌楼站)" in names


def test_gnss_coverage_empty() -> None:
    assert project_names("gnss") == ()


def test_f42_alias_maps_to_coverage_canonical() -> None:
    """层位字典旧稿 F42(牌楼）须落到覆盖名单 F42(牌楼站)，禁止 silently 丢站。"""
    assert canonical_project("F42(牌楼）", "fcb") == "F42(牌楼站)"
    assert canonical_project("F42(牌楼)", "fcb") == "F42(牌楼站)"
    assert canonical_project("F42(牌楼站)", "fcb") == "F42(牌楼站)"


def test_slice_layer0_keys_only_fcb_layer0() -> None:
    keys = layer0_mark_keys()
    fcb = set(project_names("fcb"))
    projects = {project for project, _mark in keys}
    for project, _mark in keys:
        assert project in fcb
    assert "F42(牌楼站)" in projects
    assert len(keys) == 42


def test_layered_marks_jyb_and_fcb_split() -> None:
    marks = load_layered_marks()
    assert any(m.table == "jyb" and m.station_name.startswith("J") for m in marks)
    assert any(m.table == "fcb" and m.monitor_layer == 0 for m in marks)
    dsm = load_device_projects_by_type()
    assert "fcb" in dsm
