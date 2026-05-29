"""将 configs/analysis_agent_slots/*.json 追加为 prompts.yaml 中 analysis_agent_slots_* scene。"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SLOTS_DIR = ROOT / "configs" / "analysis_agent_slots"
PROMPTS = ROOT / "configs" / "prompts.yaml"

MARKER_START = "# ========== analysis_agent 槽位配置（analysis_agent_slots_*）· 开始"
MARKER_END = "# ========== analysis_agent 槽位配置（analysis_agent_slots_*）· 结束"


def main() -> None:
    raw = PROMPTS.read_text(encoding="utf-8")
    if MARKER_START in raw:
        raw = raw.split(MARKER_START)[0].rstrip() + "\n"
    parts = [
        "\n" + MARKER_START + "\n",
        "# 由 scripts/merge_analysis_agent_slots_to_prompts.py 生成；亦可仅维护 JSON 文件由 loader 读取。\n",
    ]
    for path in sorted(SLOTS_DIR.glob("*.json")):
        name = path.stem
        if "." not in name:
            continue
        analysis_type, version = name.rsplit(".", 1)
        scene = f"analysis_agent_slots_{analysis_type}"
        body = path.read_text(encoding="utf-8")
        json.loads(body)
        parts.append(f"\n{scene}:\n")
        parts.append(f"  - version: {version}\n")
        parts.append("    weight: 1.0\n")
        parts.append(f"    description: 槽位流水线配置（{analysis_type} {version}）\n")
        parts.append("    content: |\n")
        for line in body.splitlines():
            parts.append(f"      {line}\n")
        print("queued", scene, version)
    parts.append("\n" + MARKER_END + "\n")
    PROMPTS.write_text(raw + "".join(parts), encoding="utf-8")
    print("wrote", PROMPTS)


if __name__ == "__main__":
    main()
