"""PromptTemplateRegistry 默认从 configs/prompts.yaml 加载 nl2sql_scope_parse。"""

from __future__ import annotations

from app.llm.prompt_registry import PromptTemplateRegistry


def test_default_registry_loads_nl2sql_scope_parse_from_prompts_yaml() -> None:
    reg = PromptTemplateRegistry()
    assert reg._config_path.replace("\\", "/").endswith("configs/prompts.yaml")
    tpl = reg.get_template("nl2sql_scope_parse", version="v1")
    assert tpl is not None
    content = tpl.content or ""
    assert "【示例】" in content
    assert "低过第一排" in content
    assert "所有机组低温过热器超温" in content
    assert "{{QUESTION}}" in content
