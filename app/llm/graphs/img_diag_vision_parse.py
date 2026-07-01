"""看图诊断视觉臂：Markdown 叙述 + JSON 块（方案 C）解析。"""

from __future__ import annotations

import json
import re
from typing import Any

from app.models.analysis_nl2sql_llm import extract_json_object_from_llm_text

VISION_JSON_MARKER = "---JSON---"


def split_vision_narrative_and_json(raw: str) -> tuple[str, str]:
    """
    从模型回复中分离 Markdown 叙述与 JSON 段。
    优先识别 ``---JSON---`` 分隔符；否则若前缀含 Markdown 且后缀为 JSON 对象则拆分。
    """
    text = (raw or "").strip()
    if not text:
        return "", ""

    marker = re.search(r"---JSON---\s*", text, re.IGNORECASE)
    if marker:
        return text[: marker.start()].strip(), text[marker.end() :].strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return text, text

    prefix = text[:start].strip()
    if prefix:
        return prefix, text[start : end + 1]
    return "", text


def parse_vision_lane_llm_output(raw: str) -> dict[str, Any]:
    """
    解析视觉臂单次 LLM 输出：保留 ``vision_narrative``，并抽取结构化 JSON 供下游 RAG/synthesis。
    兼容仅 JSON 的旧格式。
    """
    text = (raw or "").strip()
    if not text:
        return {"parse_error": "vision_output_empty", "vision_narrative": ""}

    narrative, json_blob = split_vision_narrative_and_json(text)
    parsed = extract_json_object_from_llm_text(json_blob)
    if parsed is None and json_blob != text:
        parsed = extract_json_object_from_llm_text(text)
    if parsed is None:
        try:
            candidate = json.loads(json_blob.strip())
            parsed = candidate if isinstance(candidate, dict) else None
        except json.JSONDecodeError:
            parsed = None

    if not isinstance(parsed, dict):
        out: dict[str, Any] = {
            "raw_text": text[:8000],
            "parse_error": "vision_output_not_json",
        }
        if narrative:
            out["vision_narrative"] = narrative
        return out

    if narrative and not parsed.get("vision_narrative"):
        parsed["vision_narrative"] = narrative
    return parsed
