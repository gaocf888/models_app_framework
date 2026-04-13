from app.nl2sql.prompt_builder import PromptBuilder


def test_prompt_builder_includes_schema_catalog() -> None:
    builder = PromptBuilder()
    prompt = builder.build(
        question="查询一号锅炉超温记录",
        schema_snippets=["[ns=nl2sql_schema] 锅炉信息表(account_boiler)"],
        schema_catalog="- account_boiler(boiler_id, boiler_name)\n- base_temp_device(device_id, temperature)",
    )
    assert "Schema catalog (authoritative identifiers)" in prompt
    assert "account_boiler(boiler_id, boiler_name)" in prompt
    assert "表名/字段名以 Schema catalog（若有）为准" in prompt


def test_prompt_builder_omits_catalog_section_when_none() -> None:
    builder = PromptBuilder()
    prompt = builder.build(
        question="test",
        schema_snippets=["snippet"],
        schema_catalog=None,
    )
    assert "Schema catalog (authoritative identifiers)" not in prompt

