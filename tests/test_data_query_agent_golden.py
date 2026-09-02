"""查询台黄金问句：锁表 + 区/站/粒度（不连 PG）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.data_query_agent.catalog import get_library_catalog
from app.data_query_agent.library_intent import resolve_library_intent
from app.data_query_agent.scope_intent import resolve_scope_intent

_GOLDEN = Path("configs/data_query_agent/golden.json")


def _cases() -> list[dict]:
    return json.loads(_GOLDEN.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["id"])
def test_data_query_golden_library_and_scope(case: dict) -> None:
    cat = get_library_catalog()
    intent = resolve_library_intent(case["query"], case.get("library_id"), catalog=cat)
    if case.get("expect_hitl"):
        assert not intent.ok
        assert intent.interrupt_reason == case["expect_hitl"]
        return
    assert intent.ok
    assert intent.library is not None
    assert intent.library.id == case["expect_library"]
    assert intent.library.table == case["expect_table"]
    scope = resolve_scope_intent(case["query"], intent.library)
    assert scope.grain == case["expect_grain"]
    if case.get("expect_district"):
        assert scope.confirmed_scope.get("district") == case["expect_district"]
    assert scope.confirmed_scope.get("device_type") == intent.library.device_type
