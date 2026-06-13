"""chatbot_retrieval_query 单测。"""

from app.llm.graphs.chatbot_retrieval_query import (
    build_retrieval_query_for_chatbot,
    format_rag_snippets_system_block,
    is_confirmation_short_query,
)


def test_is_confirmation_short():
    assert is_confirmation_short_query("你确定吗？")
    assert is_confirmation_short_query("真的吗")
    assert not is_confirmation_short_query("过热器爆管常见原因有哪些")


def test_build_retrieval_fusion():
    hist = [
        {"role": "user", "content": "1号炉过热器泄漏怎么处理"},
        {"role": "assistant", "content": "建议先隔离泄漏点并降压，再按规程查漏与补焊。"},
    ]
    out = build_retrieval_query_for_chatbot("你确定吗？", hist)
    assert "【检索会话衔接】" in out
    assert "【指代类型】" in out
    assert "meta_confirm" in out
    assert "上轮用户" in out
    assert "上轮助手" in out
    assert "你确定吗？" in out
    assert "泄漏" in out or "隔离" in out


def test_build_retrieval_no_hist():
    assert build_retrieval_query_for_chatbot("你确定吗？", []) == "你确定吗？"


def test_format_rag_block_contains_short_confirm_rule():
    block = format_rag_snippets_system_block(["[1] 《手册》\n片段A", "[2] 《标准》\n片段B"])
    assert "确定吗" in block
    assert "片段A" in block
    assert "禁止编造" in block
