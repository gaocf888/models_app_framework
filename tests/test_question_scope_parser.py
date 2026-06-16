"""NL2SQL 问句范围解析（Phase 1 程序规则）。"""

from __future__ import annotations

import pytest

from app.core.config import get_app_config
from app.nl2sql.chain import NL2SQLChain
from app.nl2sql.question_intent import resolve_question_intent, scope_literals_from_question
from app.nl2sql.scope_parser_rule import (
    _find_device,
    _strip_device_and_boiler_prefix,
    expand_abbreviations,
    parse_scope_rule,
)
from app.nl2sql.scope_lexicon import get_scope_lexicon


ACCEPTANCE_CASES = [
    (
        "1号锅炉低温过热器第一层第一排第一根",
        "1号锅炉",
        "低温过热器",
        "第一层",
        1,
        1,
    ),
    (
        "二号锅炉水冷壁前墙垂直段炉前向炉后数第一根",
        "2号锅炉",
        "水冷壁前墙垂直段",
        "炉前向炉后数",
        1,
        1,
    ),
    (
        "一号机组屏式过热器第一屏第一排第一根",
        "1号锅炉",
        "屏式过热器",
        "第一屏",
        1,
        1,
    ),
    (
        "2号机组屏式过热器前屏第一排第一根",
        "2号锅炉",
        "屏式过热器",
        "第一屏",
        1,
        1,
    ),
    (
        "1号机组低温过热器第一层炉右向炉左数第一排第一根",
        "1号锅炉",
        "低温过热器",
        "第一层炉右向炉左数",
        1,
        1,
    ),
    (
        "二号机组水冷壁左墙炉后向炉前数第一根",
        "2号锅炉",
        "水冷壁左墙",
        "炉后向炉前数",
        1,
        1,
    ),
]


@pytest.mark.parametrize(
    "question,boiler,device,piperow,row_no,tube_no",
    ACCEPTANCE_CASES,
)
def test_acceptance_scope_cases(
    question: str,
    boiler: str,
    device: str,
    piperow: str,
    row_no: int,
    tube_no: int,
) -> None:
    scope = parse_scope_rule(question)
    assert scope.boiler == boiler
    assert scope.device_name == device
    assert scope.piperow_name == piperow
    assert scope.row_no == row_no
    assert scope.tube_no == tube_no


def test_recent_week_scope_empty_time_tag() -> None:
    intent = resolve_question_intent("请分析近一周超温")
    assert intent.time_window_tag == "recent_7_days"
    assert intent.scope.boiler is None
    assert intent.scope.device_name is None
    assert intent.scope.piperow_name is None
    assert intent.scope.row_no is None
    assert intent.scope.tube_no is None


def test_all_plants_with_device() -> None:
    scope = parse_scope_rule("所有机组低温过热器超温")
    assert scope.boiler is None
    assert scope.device_name == "低温过热器"


def test_abbreviation_device_and_row() -> None:
    scope = parse_scope_rule("低过第一排")
    assert scope.boiler is None
    assert scope.device_name == "低温过热器"
    assert scope.row_no == 1
    assert scope.tube_no is None


def test_scope_literals_backward_compatible_keys() -> None:
    literals = scope_literals_from_question("请分析1号锅炉前天的超温")
    assert literals["unit_keyword"] == "1号锅炉"
    assert literals["boiler"] == "1号锅炉"
    assert literals["device_name"] is None
    assert literals["row_no"] is None
    assert literals["tube_no"] is None


def test_entity_scope_uses_time_intent_not_rag_guide_boiler_example() -> None:
    user_q = "请分析2号机组昨天的超温情况，并出具分析报告"
    long_q = (
        f"{user_q}。统计用户指定锅炉在昨天的超温事件。"
        "若用户未指定机组/区域，则不要在 WHERE 中臆造具体锅炉名或墙别。"
        "。请结合以下规则线索：参考1号锅炉典型超温案例，注意壁温测点配置。"
    )
    intent = resolve_question_intent(long_q, time_intent_source=user_q)
    assert intent.scope.boiler == "2号锅炉"
    assert intent.scope_question == user_q
    assert chain_extract_boiler(long_q) == "1号锅炉"


def test_chain_extract_scope_literals_extended() -> None:
    chain = object.__new__(NL2SQLChain)
    scopes = chain._extract_scope_literals_from_question(
        "1号锅炉低温过热器第一层第一排第一根"
    )
    assert scopes["boiler"] == "1号锅炉"
    assert scopes["device_name"] == "低温过热器"
    assert scopes["piperow_name"] == "第一层"
    assert scopes["row_no"] == 1
    assert scopes["tube_no"] == 1


def test_parse_mode_defaults_to_rule() -> None:
    get_app_config.cache_clear()
    intent = resolve_question_intent("1号锅炉超温")
    assert intent.parse_mode == "rule"


def test_device_strip_leaves_piperow_tail_for_parse() -> None:
    lex = get_scope_lexicon()
    expanded = expand_abbreviations("1号锅炉低温过热器第一层第一排", lex.abbreviations)
    device_name, device_match = _find_device(expanded, lex)
    assert device_name == "低温过热器"
    work = _strip_device_and_boiler_prefix(
        expanded,
        device_match=device_match,
        boiler="1号锅炉",
    )
    assert "低温过热器" not in work
    assert "1号锅炉" not in work
    assert "第一层" in work
    assert "第一排" in work


def test_device_in_name_does_not_false_match_screen_alias() -> None:
    """设备名含「前」时不应把管排误解析为前屏（需先剥离设备）。"""
    scope = parse_scope_rule("2号锅炉水冷壁前墙垂直段炉前向炉后数第一根")
    assert scope.device_name == "水冷壁前墙垂直段"
    assert scope.piperow_name == "炉前向炉后数"
    assert scope.row_no == 1


def chain_extract_boiler(question: str) -> str | None:
    return NL2SQLChain._extract_boiler_scope_label_from_question(question)
