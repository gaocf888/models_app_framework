"""chatbot_retrieval_query 单测。"""

from app.llm.graphs.chatbot_retrieval_query import (
    build_retrieval_query_for_chatbot,
    format_empty_rag_system_block,
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
    assert "片段引用标注" in block
    assert "宽泛问题" in block
    assert "分析/通用经验" in block
    assert "禁止输出「知识库本轮未检索到相关条文」" in block
    assert "仅依据下列片段" not in block


def test_format_empty_rag_system_block_forbids_retrieval_meta():
    block = format_empty_rag_system_block()
    assert "未向你注入检索知识片段" in block
    assert "通用经验，非本厂知识库条文" in block
    assert "没有检索到" in block  # 作为禁止输出的话术出现
    assert format_rag_snippets_system_block([]) == block
    assert format_rag_snippets_system_block(["", "  "]) == block
