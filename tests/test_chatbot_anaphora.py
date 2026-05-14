"""指代清单 §3.2：规则检测、检索融合、yaml 加载。"""

from pathlib import Path

import pytest
import yaml

from app.llm.graphs.chatbot_anaphora_config import get_anaphora_runtime_config, load_anaphora_config_from_path
from app.llm.graphs.chatbot_anaphora_detect import classify_anaphora_rules
from app.llm.graphs.chatbot_retrieval_query import build_retrieval_query_with_anaphora


def _hist():
    return [
        {"role": "user", "content": "A 方案与 B 方案各有什么要点"},
        {"role": "assistant", "content": "1. A 侧重成本\n2. B 侧重可靠性"},
    ]


def test_pair_compare_fusion_contains_type_line():
    q = "这两者的区别是什么"
    rag, rule, eff = build_retrieval_query_with_anaphora(q, _hist(), enable_context=True)
    assert "【指代类型】" in rag
    assert "pair_compare" in rag
    assert eff == "pair_compare"
    assert rule.anaphora_type == "pair_compare"


def test_ordinal_fusion():
    q = "第二点详细说明"
    rag, rule, _eff = build_retrieval_query_with_anaphora(q, _hist(), enable_context=True)
    assert "【指代类型】" in rag
    assert "ordinal" in rag
    assert rule.anaphora_type == "ordinal"


def test_meta_confirm_still_fuses():
    hist = [
        {"role": "user", "content": "泄漏怎么处理"},
        {"role": "assistant", "content": "先降压隔离。"},
    ]
    rag, rule, _ = build_retrieval_query_with_anaphora("你确定吗？", hist, enable_context=True)
    assert "【指代类型】" in rag
    assert "meta_confirm" in rag


def test_fixtures_expectations_match_rules():
    path = Path(__file__).resolve().parent / "fixtures" / "chatbot_anaphora_cases.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    hist = _hist()
    for c in data.get("cases") or []:
        q = str(c.get("query") or "")
        exp = str(c.get("expect_anaphora_type") or "")
        r = classify_anaphora_rules(q, hist, enable_context=True, config_path=None)
        assert r.anaphora_type == exp, (c.get("id"), r.anaphora_type, exp)


def test_yaml_fail_fast_unknown_code(tmp_path):
    root = Path(__file__).resolve().parents[1]
    good = root / "configs" / "chatbot_anaphora.yaml"
    data = yaml.safe_load(good.read_text(encoding="utf-8")) or {}
    types = list(data.get("types") or [])
    types.append(
        {
            "code": "not_a_valid_type",
            "display_name": "bad",
            "keywords": [],
            "regex": [],
            "p0_retrieval_fusion": False,
            "p1_anchor_block": False,
        }
    )
    data["types"] = types
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown codes"):
        load_anaphora_config_from_path(p)


def test_bundled_config_loads():
    root = Path(__file__).resolve().parents[1]
    cfg_path = root / "configs" / "chatbot_anaphora.yaml"
    c = load_anaphora_config_from_path(cfg_path)
    assert "pair_compare" in c.types
    get_anaphora_runtime_config.cache_clear()
    c2 = get_anaphora_runtime_config(str(cfg_path))
    assert c2.schema_version >= 1
