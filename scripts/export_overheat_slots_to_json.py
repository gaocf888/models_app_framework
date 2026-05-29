"""将 overheat_guidance 槽位定义导出为 configs/analysis_agent_slots/overheat_guidance.v1.json。"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _slot_to_dict(slot: object) -> dict:
    d = asdict(slot)  # type: ignore[arg-type]
    for key in ("source_item_ids", "outline", "constraints", "allowed_outputs"):
        if key in d and isinstance(d[key], tuple):
            d[key] = list(d[key])
    fh = d.get("field_hints")
    if isinstance(fh, tuple):
        d["field_hints"] = dict(fh)
    return d


def main() -> None:
    # 若 overheat_guidance.py 已仅为 loader 包装，应从 JSON 读入再写回（幂等）
    from app.analysis_agent.slots.loader import load_agent_slots

    slots = load_agent_slots("overheat_guidance", version="v1")
    spec = {
        "schema_version": 1,
        "description": "超温 v2 九段报告槽位（配置源）",
        "slots": [_slot_to_dict(s) for s in slots],
    }
    out = ROOT / "configs" / "analysis_agent_slots" / "overheat_guidance.v1.json"
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", out, "slots", len(slots))


if __name__ == "__main__":
    main()
