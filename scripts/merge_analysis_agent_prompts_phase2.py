"""将现网模板块复制为 analysis_agent_* 并追加到 prompts.yaml 末尾。

禁止对整文件 yaml.safe_dump，以免破坏多行 content 与注释。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "configs" / "prompts.yaml"

MAPPINGS = [
    ("analysis_plan_overheat_guidance", "analysis_agent_plan_overheat_guidance"),
    ("analysis_plan_maintenance_strategy", "analysis_agent_plan_maintenance_strategy"),
    ("analysis_plan_four_tube_health_interpretation", "analysis_agent_plan_four_tube_health_interpretation"),
    ("analysis_plan_leakage_burst_analysis", "analysis_agent_plan_leakage_burst_analysis"),
    ("analysis_synthesis_overheat_narrative", "analysis_agent_synthesis_overheat_guidance"),
    ("analysis_synthesis_maintenance_strategy", "analysis_agent_synthesis_maintenance_strategy"),
    ("analysis_synthesis_four_tube_health_interpretation", "analysis_agent_synthesis_four_tube_health_interpretation"),
    ("analysis_synthesis_leakage_burst_analysis", "analysis_agent_synthesis_leakage_burst_analysis"),
]

_TOP_KEY = re.compile(r"^[A-Za-z_][\w]*:\s*")


def _extract_block(text: str, key: str) -> str | None:
    lines = text.splitlines(keepends=True)
    start: int | None = None
    for i, line in enumerate(lines):
        if line.startswith(f"{key}:"):
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if _TOP_KEY.match(lines[j]) and not lines[j].startswith(" "):
            end = j
            break
    block = "".join(lines[start:end])
    # 去掉块尾部的节标记注释（nl2sql 段末常见）
    block = re.sub(
        r"\n# =+NL2SQL主链路.*\[结束\]=+.*\n?$",
        "\n",
        block,
        flags=re.DOTALL,
    )
    return block


def _patch_description(block: str, dst_key: str) -> str:
    """在 description 中标注 analysis_agent 专用（若存在）。"""
    marker = "[analysis_agent 专用，与现网 /analysis 模板独立]"
    if marker in block:
        return block
    return re.sub(
        r"(^\s+description:\s*)(.+)$",
        rf"\1\2 {marker}",
        block,
        count=1,
        flags=re.MULTILINE,
    )


def main() -> None:
    raw = PROMPTS.read_text(encoding="utf-8")
    # 若已存在整块区域则先去掉旧追加（从标记行到文件尾前的重复键）
    marker = "# ========== 综合分析智能体 analysis_agent 专用提示词"
    if marker in raw:
        raw = raw.split(marker)[0].rstrip() + "\n"

    append_parts: list[str] = []
    header = (
        "\n"
        "# =============================================================================\n"
        f"{marker} [开始]\n"
        "# 仅由 app/analysis_agent/* 加载；禁止回退 analysis_plan_* / analysis_synthesis_* / nl2sql。\n"
        "# 由 scripts/merge_analysis_agent_prompts_phase2.py 生成，手工修改后勿整文件 yaml.dump。\n"
        "# =============================================================================\n"
    )
    append_parts.append(header)

    missing: list[str] = []
    for src, dst in MAPPINGS:
        block = _extract_block(raw, src)
        if not block:
            missing.append(src)
            continue
        new_block = _patch_description(block.replace(f"{src}:", f"{dst}:", 1), dst)
        append_parts.append(new_block.rstrip() + "\n")
        print("queued", dst, "<-", src)

    if missing:
        print("ERROR missing sources:", missing, file=sys.stderr)
        sys.exit(1)

    if not raw.endswith("\n"):
        raw += "\n"
    raw += "\n".join(append_parts)
    raw += (
        "\n# =============================================================================\n"
        f"{marker} [结束]\n"
        "# =============================================================================\n"
    )
    PROMPTS.write_text(raw, encoding="utf-8")
    print("wrote", PROMPTS, "lines", len(raw.splitlines()))


if __name__ == "__main__":
    main()
