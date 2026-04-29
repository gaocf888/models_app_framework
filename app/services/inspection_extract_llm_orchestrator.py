"""
检修报告 LLM 编排：parse / classify / repair 分阶段调用与分块。

与 `InspectionExtractService` 解耦，仅负责从已解析文本到原始 records 列表。
"""

from __future__ import annotations

import json
import re
import hashlib
from typing import Any

from app.core.config import InspectionExtractConfig
from app.core.logging import get_logger
from app.inspection_v2.orchestrator import split_parse_chunks
from app.llm.client import VLLMHttpClient
from app.llm.prompt_registry import PromptTemplateRegistry
from app.models.inspection_extract import InspectionExtractRequest

logger = get_logger(__name__)


class InspectionExtractLlmOrchestrator:
    def __init__(
        self,
        *,
        llm: VLLMHttpClient,
        prompts: PromptTemplateRegistry,
        cfg: InspectionExtractConfig,
    ) -> None:
        self._llm = llm
        self._prompts = prompts
        self._cfg = cfg

    async def run_llm_extraction(
        self,
        req: InspectionExtractRequest,
        parsed_text: str,
        *,
        parse_route: str,
        prompt_version: str,
        model: str,
    ) -> list[dict[str, Any]]:
        snippets = parsed_text[:20000]
        llm_timeout_s = float(getattr(self._cfg, "llm_timeout_seconds", 180.0))
        parse_chunk_retry = 1
        logger.info(
            "【检修提取】开始LLM抽取流程，模型=%s，超时=%.1fs，log_llm_raw=%s，log_parse_chunk_full=%s",
            model,
            llm_timeout_s,
            bool(getattr(self._cfg, "log_llm_raw_response", False)),
            bool(getattr(self._cfg, "log_parse_chunk_full", False)),
        )
        parse_tpl = self._get_prompt_content(scene="inspection_extract_parse", user_id=req.user_id, version=prompt_version)
        classify_tpl = self._get_prompt_content(
            scene="inspection_extract_classify", user_id=req.user_id, version=prompt_version
        )
        repair_tpl = self._get_prompt_content(scene="inspection_extract_repair", user_id=req.user_id, version=prompt_version)

        max_chars = 6000
        pr = (parse_route or "text").strip().lower()
        if pr == "docx_v2":
            max_chars = max(2000, int(getattr(self._cfg, "v2_parse_unit_max_chars", 6000)))
        chunks = split_parse_chunks(parsed_text, parse_route=parse_route, max_chunk_chars=max_chars)
        logger.info(
            "inspection_extract parse chunk_count=%s parse_route=%s max_chunk_chars=%s",
            len(chunks),
            parse_route,
            max_chars,
        )
        logger.info("【检修提取】Parse阶段分块数量=%s", len(chunks))
        stage1_records: list[dict[str, Any]] = []
        for idx, chunk in enumerate(chunks, start=1):
            records_i: list[dict[str, Any]] = []
            chunk_meta = _summarize_chunk(chunk)
            self._log_parse_chunk_full(idx=idx, total=len(chunks), chunk=chunk)
            logger.info(
                "【检修提取】开始解析分块 %s/%s，长度=%s heading_path=%s text_lines=%s table_lines=%s table_blocks=%s table_idx_range=%s row_idx_range=%s chunk_sha1=%s preview=%s",
                idx,
                len(chunks),
                len(chunk),
                chunk_meta["heading_path"],
                chunk_meta["text_lines"],
                chunk_meta["table_lines"],
                chunk_meta["table_blocks"],
                chunk_meta["table_idx_range"],
                chunk_meta["row_idx_range"],
                chunk_meta["chunk_sha1"],
                chunk_meta["preview"],
            )
            for attempt in range(parse_chunk_retry + 1):
                parse_prompt = (
                    f"{parse_tpl}\n\n"
                    "优先输出 NDJSON（每行一个 JSON 对象，不加 markdown 代码块）。\n"
                    "每个对象应包含：检测位置、行号、管号、壁厚、检测类型、缺陷类型、是否换管。\n"
                    "可选补充字段：evidence、warnings。\n"
                    "若你无法输出 NDJSON，则回退输出 JSON：{\"records\":[...]}。\n"
                    f"文档分块如下（第{idx}/{len(chunks)}块）：\n{chunk}"
                )
                logger.info(
                    "inspection_extract llm stage=parse chunk=%s/%s model=%s prompt_chars=%s timeout_s=%.1f",
                    idx,
                    len(chunks),
                    model,
                    len(parse_prompt),
                    llm_timeout_s,
                )
                parse_result = await self._llm.generate(
                    model=model,
                    prompt=parse_prompt,
                    timeout=llm_timeout_s,
                    max_tokens=int(getattr(self._cfg, "llm_max_tokens_parse", 1024)),
                )
                self._log_llm_raw(stage=f"parse[{idx}/{len(chunks)}]-try{attempt+1}", raw=parse_result)
                stage1 = _extract_json_like(parse_result)
                logger.info("inspection_extract parse payload_type=%s", type(stage1).__name__ if stage1 is not None else "None")
                records_i = _extract_records(stage1)
                parse_format = "json"
                if not records_i:
                    records_i = _extract_records_from_ndjson(parse_result)
                    if records_i:
                        parse_format = "ndjson"
                if not records_i:
                    records_i = _salvage_records_from_truncated_json(parse_result)
                    if records_i:
                        parse_format = "json_salvage"
                logger.info("inspection_extract llm stage=parse chunk=%s result_records=%s", idx, len(records_i))
                logger.info("inspection_extract parse chunk=%s parse_format=%s", idx, parse_format)
                if records_i and isinstance(records_i[0], dict):
                    logger.info("inspection_extract parse records sample_keys=%s", sorted(records_i[0].keys()))
                if records_i:
                    logger.info("【检修提取】分块 %s/%s 解析成功，记录数=%s，尝试次数=%s", idx, len(chunks), len(records_i), attempt + 1)
                    break
                if attempt < parse_chunk_retry:
                    logger.warning("【检修提取】分块 %s/%s 解析失败，开始重试 第 %s 次", idx, len(chunks), attempt + 1)
            if not records_i:
                logger.warning("【检修提取】分块 %s/%s 最终解析失败，已跳过该分块", idx, len(chunks))
            stage1_records.extend(records_i)

        logger.info("inspection_extract llm stage=parse merged_records=%s", len(stage1_records))
        logger.info("【检修提取】Parse阶段合并后记录数=%s", len(stage1_records))
        if not stage1_records:
            table_records = _extract_records_from_markdown_table(parsed_text)
            if table_records:
                stage1_records = table_records
                logger.info(
                    "inspection_extract llm stage=parse fallback=markdown_table records=%s",
                    len(stage1_records),
                )
                if stage1_records and isinstance(stage1_records[0], dict):
                    logger.info("inspection_extract parse fallback sample_keys=%s", sorted(stage1_records[0].keys()))
                logger.info("【检修提取】启用表格兜底成功，记录数=%s", len(stage1_records))
        if not stage1_records:
            logger.info("inspection_extract llm short_circuit_empty_parse_records")
            logger.warning("【检修提取】Parse阶段无记录，流程提前结束")
            return []

        if _records_have_full_schema(stage1_records):
            logger.info("inspection_extract llm skip_classify_parse_already_full records=%s", len(stage1_records))
            logger.info("【检修提取】Parse结果字段完整，跳过Classify阶段")
            return stage1_records

        stage2_records: list[dict[str, Any]] = []
        cls_bs = 80
        if pr == "docx_v2":
            cls_bs = max(8, int(getattr(self._cfg, "v2_classify_batch_size", 40)))
        classify_batches = _batch_records(stage1_records, batch_size=cls_bs)
        logger.info("【检修提取】Classify阶段批次数=%s", len(classify_batches))
        for bidx, batch in enumerate(classify_batches, start=1):
            classify_prompt = (
                f"{classify_tpl}\n\n"
                "请仅输出 JSON。顶层对象包含 records 数组。\n"
                "检测类型只能是：测厚/缺陷。\n"
                "缺陷类型只能是：高温腐蚀、磨损、结渣、蠕变、管道变形、表面吹损、氧化皮堆积、机械损伤。\n"
                "是否换管只能是：是/否。\n"
                f"候选记录(JSON)：{json.dumps(batch, ensure_ascii=False)}\n"
                f"文档摘要：{snippets[:4000]}"
            )
            logger.info(
                "inspection_extract llm stage=classify batch=%s/%s model=%s records_in=%s prompt_chars=%s timeout_s=%.1f",
                bidx,
                len(classify_batches),
                model,
                len(batch),
                len(classify_prompt),
                llm_timeout_s,
            )
            logger.info("【检修提取】开始分类批次 %s/%s，输入记录=%s", bidx, len(classify_batches), len(batch))
            classify_result = await self._llm.generate(
                model=model,
                prompt=classify_prompt,
                timeout=llm_timeout_s,
                max_tokens=int(getattr(self._cfg, "llm_max_tokens_classify", 1024)),
            )
            self._log_llm_raw(stage=f"classify[{bidx}/{len(classify_batches)}]", raw=classify_result)
            stage2 = _extract_json_like(classify_result)
            logger.info("inspection_extract classify payload_type=%s", type(stage2).__name__ if stage2 is not None else "None")
            recs_b = _extract_records(stage2)
            logger.info("inspection_extract llm stage=classify batch=%s result_records=%s", bidx, len(recs_b))
            logger.info("【检修提取】分类批次 %s/%s 完成，输出记录=%s", bidx, len(classify_batches), len(recs_b))
            stage2_records.extend(recs_b)
        logger.info("inspection_extract llm stage=classify merged_records=%s", len(stage2_records))
        logger.info("【检修提取】Classify阶段合并后记录数=%s", len(stage2_records))

        if not _need_repair(stage2_records):
            logger.info("inspection_extract llm skip_repair records=%s", len(stage2_records))
            return stage2_records

        if len(stage2_records) > 200:
            logger.info("inspection_extract llm skip_repair_too_many_records records=%s", len(stage2_records))
            return stage2_records

        retries = max(0, int(self._cfg.max_repair_retries))
        candidate_records = stage2_records
        for _ in range(retries + 1):
            repair_input = candidate_records
            repair_prompt = (
                f"{repair_tpl}\n\n"
                "请仅输出 JSON。顶层对象包含 records 数组。\n"
                "修复项：字段缺失、枚举非法、数字格式错误。无法修复时保留 warnings。\n"
                f"待修复记录(JSON)：{json.dumps(repair_input, ensure_ascii=False)}"
            )
            logger.info(
                "inspection_extract llm stage=repair model=%s records_in=%s prompt_chars=%s timeout_s=%.1f",
                model,
                len(repair_input),
                len(repair_prompt),
                llm_timeout_s,
            )
            logger.info("【检修提取】开始Repair阶段，输入记录=%s", len(repair_input))
            repaired_result = await self._llm.generate(
                model=model,
                prompt=repair_prompt,
                timeout=llm_timeout_s,
                max_tokens=int(getattr(self._cfg, "llm_max_tokens_repair", 768)),
            )
            self._log_llm_raw(stage="repair", raw=repaired_result)
            stage3 = _extract_json_like(repaired_result)
            logger.info("inspection_extract repair payload_type=%s", type(stage3).__name__ if stage3 is not None else "None")
            candidate_records = _extract_records(stage3)
            logger.info("inspection_extract llm stage=repair result_records=%s", len(candidate_records))
            logger.info("【检修提取】Repair阶段输出记录=%s", len(candidate_records))
            if candidate_records:
                break
        logger.info("【检修提取】LLM抽取流程结束，最终记录数=%s", len(candidate_records))
        return candidate_records

    def _get_prompt_content(self, *, scene: str, user_id: str, version: str) -> str:
        tpl = self._prompts.get_template(scene=scene, user_id=user_id, version=version)
        if tpl and tpl.content:
            return tpl.content
        fallback = self._prompts.get_template(scene="inspection_extract", user_id=user_id, version=version)
        if fallback and fallback.content:
            return fallback.content
        return "你是检修报告结构化抽取助手。"

    def _log_llm_raw(self, *, stage: str, raw: str) -> None:
        if not bool(getattr(self._cfg, "log_llm_raw_response", False)):
            return
        limit = int(getattr(self._cfg, "log_llm_raw_max_chars", 2000))
        text = (raw or "").strip()
        clipped = text[:limit]
        if len(text) > limit:
            clipped += f"\n...<truncated {len(text) - limit} chars>"
        logger.info("inspection_extract llm raw stage=%s response=\n%s", stage, clipped)

    def _log_parse_chunk_full(self, *, idx: int, total: int, chunk: str) -> None:
        if not bool(getattr(self._cfg, "log_parse_chunk_full", False)):
            return
        max_c = int(getattr(self._cfg, "log_parse_chunk_max_chars", 0))
        body = chunk or ""
        truncated_note = ""
        if max_c > 0 and len(body) > max_c:
            body = body[:max_c]
            truncated_note = f" (truncated_to_max_chars={max_c})"
        sha = hashlib.sha1((chunk or "").encode("utf-8", errors="ignore")).hexdigest()[:12]
        logger.info(
            "inspection_extract parse_chunk_full_meta chunk=%s/%s bytes=%s sha1=%s%s",
            idx,
            total,
            len(chunk or ""),
            sha,
            truncated_note,
        )
        step = 24000
        if not body:
            logger.info("inspection_extract parse_chunk_full_body chunk=%s/%s part=1/1 content=", idx, total)
            return
        for off in range(0, len(body), step):
            part = body[off : off + step]
            pi = off // step + 1
            total_parts = (len(body) + step - 1) // step
            logger.info(
                "inspection_extract parse_chunk_full_body chunk=%s/%s part=%s/%s content=\n%s",
                idx,
                total,
                pi,
                total_parts,
                part,
            )


def _batch_records(records: list[dict[str, Any]], *, batch_size: int) -> list[list[dict[str, Any]]]:
    if not records:
        return [[]]
    out: list[list[dict[str, Any]]] = []
    for i in range(0, len(records), max(1, batch_size)):
        out.append(records[i : i + max(1, batch_size)])
    return out


def _summarize_chunk(chunk: str) -> dict[str, Any]:
    lines = [x.strip() for x in (chunk or "").splitlines() if x.strip()]
    if not lines:
        return {
            "heading_path": "-",
            "text_lines": 0,
            "table_lines": 0,
            "table_blocks": 0,
            "table_idx_range": "-",
            "row_idx_range": "-",
            "chunk_sha1": "-",
            "preview": "-",
        }

    heading_path = "-"
    text_lines = 0
    table_lines = 0
    table_blocks = 0
    table_idxs: list[int] = []
    row_idxs: list[int] = []

    heading_re = re.compile(r"^\[处理单元\s+heading_path=(.+?)\]\s*$")
    for ln in lines:
        m = heading_re.match(ln)
        if m:
            heading_path = m.group(1).strip() or "-"
            continue
        tm = re.match(r"^\[DOCX_V2_TABLE\s+idx=(\d+)\b", ln)
        if tm:
            table_blocks += 1
            table_idxs.append(int(tm.group(1)))
            continue
        rm = re.match(r"^r(\d+)\s*:", ln)
        if rm:
            table_lines += 1
            row_idxs.append(int(rm.group(1)))
            continue
        text_lines += 1

    preview_raw = " | ".join(lines[:3])[:260]
    preview = preview_raw.replace("\n", " ").replace("\r", " ")
    chunk_sha1 = hashlib.sha1((chunk or "").encode("utf-8", errors="ignore")).hexdigest()[:12]

    def _fmt_range(nums: list[int]) -> str:
        if not nums:
            return "-"
        return f"{min(nums)}-{max(nums)}"

    return {
        "heading_path": heading_path,
        "text_lines": text_lines,
        "table_lines": table_lines,
        "table_blocks": table_blocks,
        "table_idx_range": _fmt_range(table_idxs),
        "row_idx_range": _fmt_range(row_idxs),
        "chunk_sha1": chunk_sha1,
        "preview": preview,
    }


def _need_repair(records: list[dict[str, Any]]) -> bool:
    if not records:
        return True
    for rec in records:
        if not isinstance(rec, dict):
            return True
        keys = set(rec.keys())
        if "检测位置" in keys and "壁厚" in keys:
            continue
        if "location" in keys and ("thickness" in keys or "壁厚" in keys):
            continue
        return True
    return False


def _records_have_full_schema(records: list[dict[str, Any]]) -> bool:
    if not records:
        return False
    required_cn = {"检测位置", "行号", "管号", "壁厚", "检测类型", "缺陷类型", "是否换管"}
    required_en = {"location", "row_no", "tube_no", "thickness", "detection_type", "defect_type", "replaced"}
    for rec in records:
        if not isinstance(rec, dict):
            return False
        keys = set(rec.keys())
        has_cn = required_cn.issubset(keys)
        has_en = required_en.issubset(keys)
        if not (has_cn or has_en):
            return False
    return True


def _extract_records_from_markdown_table(parsed_text: str) -> list[dict[str, Any]]:
    lines = [x.strip() for x in (parsed_text or "").splitlines() if x.strip()]
    if not lines:
        return []

    def _norm_header(x: str) -> str:
        return re.sub(r"\s+", "", x).lower()

    records: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if "|" not in line:
            i += 1
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        normalized = [_norm_header(c) for c in cells]
        key_map: dict[str, int] = {}
        for idx, h in enumerate(normalized):
            if any(k in h for k in ("检测位置", "位置", "location")):
                key_map["location"] = idx
            elif any(k in h for k in ("行号", "行", "row")):
                key_map["row_no"] = idx
            elif any(k in h for k in ("管号", "管", "tube")):
                key_map["tube_no"] = idx
            elif any(k in h for k in ("壁厚", "厚度", "thickness", "thk")):
                key_map["thickness"] = idx

        if len(key_map) < 4:
            i += 1
            continue

        j = i + 1
        if j < len(lines) and "|" in lines[j]:
            sep_cells = [c.strip() for c in lines[j].strip("|").split("|")]
            if sep_cells and all(set(c) <= {"-", ":"} for c in sep_cells if c):
                j += 1

        while j < len(lines) and "|" in lines[j]:
            row_cells = [c.strip() for c in lines[j].strip("|").split("|")]
            max_idx = max(key_map.values())
            if len(row_cells) <= max_idx:
                j += 1
                continue
            rec = {
                "检测位置": row_cells[key_map["location"]],
                "行号": row_cells[key_map["row_no"]],
                "管号": row_cells[key_map["tube_no"]],
                "壁厚": row_cells[key_map["thickness"]],
            }
            if rec["检测位置"] and rec["行号"] and rec["管号"] and str(rec["壁厚"]).strip():
                records.append(rec)
            j += 1
        i = j
    return records


def _extract_json_like(raw: str) -> dict[str, Any] | list[Any] | None:
    text = _strip_markdown_fence(raw)
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, (dict, list)):
            return parsed
    except json.JSONDecodeError:
        pass
    obj_start = text.find("{")
    obj_end = text.rfind("}")
    if obj_start >= 0 and obj_end > obj_start:
        try:
            parsed = json.loads(text[obj_start : obj_end + 1])
            if isinstance(parsed, (dict, list)):
                return parsed
        except json.JSONDecodeError:
            pass
    arr_start = text.find("[")
    arr_end = text.rfind("]")
    if arr_start >= 0 and arr_end > arr_start:
        try:
            parsed = json.loads(text[arr_start : arr_end + 1])
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
    return None


def _extract_records(payload: dict[str, Any] | list[Any] | None) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    rows = payload.get("records")
    if isinstance(rows, list):
        return [x for x in rows if isinstance(x, dict)]
    return []


def _strip_markdown_fence(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        text = "\n".join(lines).strip()
        if text.endswith("```"):
            text = text[:-3].strip()
    return text


def _looks_like_inspection_record_row(d: dict[str, Any]) -> bool:
    """排除 NDJSON 误解析的 {\"records\":[]} 包装行，仅保留业务行对象。"""
    keys = set(d.keys())
    if keys <= {"records"}:
        return False
    loc = "检测位置" in keys or "location" in keys
    tube = "管号" in keys or "tube_no" in keys
    thk = "壁厚" in keys or "thickness" in keys
    return bool(loc and tube and thk)


def _extract_records_from_ndjson(raw: str) -> list[dict[str, Any]]:
    text = _strip_markdown_fence(raw)
    if not text:
        return []
    out: list[dict[str, Any]] = []
    for ln in text.splitlines():
        s = ln.strip().rstrip(",")
        if not s or not s.startswith("{") or not s.endswith("}"):
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and _looks_like_inspection_record_row(obj):
            out.append(obj)
    return out


def _salvage_records_from_truncated_json(raw: str) -> list[dict[str, Any]]:
    """
    宽松恢复：当大 JSON 尾部截断时，尽量提取 records 数组中已经完整闭合的对象。
    """
    text = _strip_markdown_fence(raw)
    if not text:
        return []
    anchor = text.find('"records"')
    if anchor >= 0:
        text = text[anchor:]
    arr_pos = text.find("[")
    if arr_pos >= 0:
        text = text[arr_pos + 1 :]

    out: list[dict[str, Any]] = []
    depth = 0
    in_str = False
    esc = False
    buf: list[str] = []
    for ch in text:
        if in_str:
            buf.append(ch)
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            if depth > 0:
                buf.append(ch)
            continue
        if ch == "{":
            depth += 1
            buf.append(ch)
            continue
        if ch == "}":
            if depth > 0:
                buf.append(ch)
                depth -= 1
                if depth == 0:
                    candidate = "".join(buf).strip().rstrip(",")
                    buf = []
                    try:
                        obj = json.loads(candidate)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(obj, dict):
                        out.append(obj)
            continue
        if depth > 0:
            buf.append(ch)
    return out
