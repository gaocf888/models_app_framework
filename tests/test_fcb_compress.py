"""V2-P3：层间压缩公式（无 IO）。"""

from __future__ import annotations

from app.analysis_agent.deterministics.fcb_compress import layer_compress, slice_layer0
from app.analysis_agent.deterministics.formulas import consecutive_layer_pairs, period_delta
import pytest


def test_delta_start_minus_end() -> None:
    assert period_delta(10, 7) == 3
    assert period_delta(7, 10) == -3


def test_consecutive_pairs_skip_gap() -> None:
    pairs = consecutive_layer_pairs({0: 10.0, 1: 6.0, 3: 1.0})
    assert [(a, b) for a, b, *_ in pairs] == [(0, 1)]
    assert pairs[0][4] == 4.0


def test_three_layers_two_pairs() -> None:
    pairs = consecutive_layer_pairs({0: 5.0, 1: 3.0, 2: 1.0})
    assert len(pairs) == 2
    assert pairs[0][4] == 2.0
    assert pairs[1][4] == 2.0


def test_layer_compress_from_endpoints() -> None:
    gathered = {
        "q_fcb_endpoints": [
            {
                "area": "平谷区",
                "project_name": "F8(周村)",
                "station_name": "F8-10",
                "settle_start": 10,
                "settle_end": 8,
            },
            {
                "area": "平谷区",
                "project_name": "F8(周村)",
                "station_name": "F8-7",
                "settle_start": 6,
                "settle_end": 5,
            },
            {
                "area": "平谷区",
                "project_name": "F8(周村)",
                "station_name": "F8-4",
                "settle_start": 4,
                "settle_end": 4,
            },
        ]
    }
    rows = layer_compress(gathered)
    pairs = {(r["layer_from"], r["layer_to"]) for r in rows if r["project_name"] == "F8(周村)"}
    assert (0, 1) in pairs
    assert (1, 2) in pairs


def test_f11_no_third_fourth_from_dict() -> None:
    """F11 字典仅 0/1/2，不出 2→3 / 3→4。"""
    gathered = {
        "q_fcb_endpoints": [
            {"project_name": "F11(南宅)", "station_name": "F11-4", "settle_start": 3, "settle_end": 1},
            {"project_name": "F11(南宅)", "station_name": "F11-2", "settle_start": 2, "settle_end": 1},
            {"project_name": "F11(南宅)", "station_name": "F11-1", "settle_start": 1, "settle_end": 0},
        ]
    }
    rows = [r for r in layer_compress(gathered) if r["project_name"] == "F11(南宅)"]
    pairs = {(r["layer_from"], r["layer_to"]) for r in rows}
    assert (0, 1) in pairs
    assert (1, 2) in pairs
    assert (2, 3) not in pairs
    assert (3, 4) not in pairs


def test_slice_layer0_drops_out_of_coverage(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.analysis_agent.deterministics import fcb_compress as mod

    monkeypatch.setattr(mod, "layer0_mark_keys", lambda: frozenset({("F8(周村)", "F8-10")}))
    gathered = {
        "q_fcb_endpoints": [
            {
                "project_name": "F8(周村)",
                "station_name": "F8-10",
                "settle_start": 10,
                "settle_end": 7,
                "area": "平谷区",
            },
            {
                "project_name": "NOT_IN_MAP",
                "station_name": "XX-0",
                "settle_start": 1,
                "settle_end": 0,
            },
        ]
    }
    rows = slice_layer0(gathered)
    assert len(rows) == 1
    assert rows[0]["delta_mm"] == 3
    assert rows[0]["project_name"] == "F8(周村)"
