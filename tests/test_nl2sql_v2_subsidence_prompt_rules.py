"""NL2SQL 基座 v2_subsidence 提示词含跨场景域规则。"""

from __future__ import annotations

from app.llm.prompt_registry import PromptTemplateRegistry


def test_v2_subsidence_prompt_has_cross_scene_sql_rules() -> None:
    reg = PromptTemplateRegistry()
    tpl = reg.get_template("nl2sql", version="v2_subsidence")
    assert tpl is not None
    content = tpl.content or ""
    assert "跨场景 SQL 生成硬约束" in content
    assert "t_data_wash_fcb" in content
    assert "suggested_filters" in content
    assert "IN (SELECT" in content
    assert "DISTINCT ON" in content
    assert "MAX(data_time)" in content
    assert "只查 t_station" in content
    assert "站点覆盖 / 区县 / 层位口径" in content
    assert "device_station_map" in content
    assert "t_station.area" in content
    assert "监测层位=0" in content
    assert "清单问句未用层位0" in content
