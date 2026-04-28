from __future__ import annotations

import io
import json
import re
import tempfile
import time
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.core.metrics import (
    INSPECT_EXTRACT_LLM_LATENCY,
    INSPECT_EXTRACT_RECORD_COUNT,
    INSPECT_EXTRACT_REQUEST_COUNT,
    INSPECT_EXTRACT_PARSE_LATENCY,
    INSPECT_EXTRACT_VALIDATION_FAIL_COUNT,
)
from app.llm.client import VLLMHttpClient
from app.llm.prompt_registry import PromptTemplateRegistry
from app.models.inspection_extract import (
    DefectType,
    DetectionType,
    InspectionExtractRequest,
    InspectionExtractResponse,
    InspectionExtractTrace,
    InspectionUploadResponse,
    InspectionRecord,
    InspectionSummary,
    ReplaceFlag,
)
from app.rag.document_pipeline.parsers import DocumentParser
from app.rag.mineru_ingest import prepare_pdf_document_for_pipeline
from app.rag.models import DocumentSource

logger = get_logger(__name__)

try:
    from minio import Minio
except Exception:  # noqa: BLE001
    Minio = None  # type: ignore[assignment]


class InspectionExtractService:
    def __init__(
        self,
        *,
        parser: DocumentParser | None = None,
        llm_client: VLLMHttpClient | None = None,
        prompt_registry: PromptTemplateRegistry | None = None,
    ) -> None:
        self._parser = parser or DocumentParser()
        self._llm = llm_client or VLLMHttpClient()
        self._prompts = prompt_registry or PromptTemplateRegistry()
        self._cfg = get_app_config().inspection_extract
        self._chat_cfg = get_app_config().chatbot
        self._minio_bucket = (self._chat_cfg.image_minio_bucket or "chatbot-images").strip()
        self._minio_presign_ttl_seconds = max(300, int(self._chat_cfg.image_minio_presign_ttl_seconds))
        self._minio = self._build_minio_client()

    async def upload_file(self, *, file_name: str, content: bytes, content_type: str | None = None) -> InspectionUploadResponse:
        if self._minio is None:
            raise RuntimeError("minio client is not available, check MinIO dependency and config")
        source_type = self._guess_source_type_from_name(file_name)
        object_name = f"inspection_extract/{uuid.uuid4().hex}_{Path(file_name).name}"
        ct = (content_type or "").strip() or "application/octet-stream"
        self._minio.put_object(
            bucket_name=self._minio_bucket,
            object_name=object_name,
            data=io.BytesIO(content),
            length=len(content),
            content_type=ct,
        )
        url = self._minio.presigned_get_object(
            bucket_name=self._minio_bucket,
            object_name=object_name,
            expires=timedelta(seconds=self._minio_presign_ttl_seconds),
        )
        return InspectionUploadResponse(
            ok=True,
            file_name=file_name,
            object_name=object_name,
            source_type=source_type,
            url=url,
            bucket=self._minio_bucket,
        )

    async def extract_from_document(self, req: InspectionExtractRequest) -> InspectionExtractResponse:
        INSPECT_EXTRACT_REQUEST_COUNT.labels(status="started").inc()
        strict = self._cfg.strict_default if req.strict is None else bool(req.strict)
        prompt_version = (req.prompt_version or self._cfg.prompt_version or "v1").strip() or "v1"
        llm_model = self._cfg.model_name or get_app_config().llm.default_model
        try:
            parse_t0 = time.perf_counter()
            parsed_text, parse_route = self._parse_document(req)
            threshold_rules = self._extract_threshold_rules(parsed_text)
            parse_ms = int((time.perf_counter() - parse_t0) * 1000)
            INSPECT_EXTRACT_PARSE_LATENCY.observe(parse_ms / 1000.0)

            llm_t0 = time.perf_counter()
            raw_records = await self._run_llm_extraction(req, parsed_text, prompt_version=prompt_version, model=llm_model)
            llm_ms = int((time.perf_counter() - llm_t0) * 1000)
            INSPECT_EXTRACT_LLM_LATENCY.observe(llm_ms / 1000.0)

            records, warnings = self._post_process_records(
                raw_records=raw_records,
                return_evidence=req.return_evidence,
                threshold_rules=threshold_rules,
                parsed_text=parsed_text,
            )
            if not records:
                if raw_records:
                    warnings.append("all_records_failed_validation")
                else:
                    warnings.append("llm_no_records_extracted")
            summary = self._build_summary(records, warnings)
            INSPECT_EXTRACT_RECORD_COUNT.inc(len(records))

            if strict and (not records):
                raise ValueError("strict mode enabled: no valid structured records extracted")

            trace = InspectionExtractTrace(
                parse_route=parse_route,
                llm_model=llm_model,
                prompt_version=f"inspection_extract:{prompt_version}",
                parse_latency_ms=parse_ms,
                llm_latency_ms=llm_ms,
            )
            INSPECT_EXTRACT_REQUEST_COUNT.labels(status="success").inc()
            return InspectionExtractResponse(ok=True, records=records, summary=summary, trace=trace)
        except Exception:
            INSPECT_EXTRACT_REQUEST_COUNT.labels(status="failed").inc()
            raise

    def _parse_document(self, req: InspectionExtractRequest) -> tuple[str, str]:
        st = (req.source_type or "text").lower()
        tmp_path: Path | None = None
        content = req.content
        if self._looks_like_http_url(content) and st in {"pdf", "doc", "docx"}:
            tmp_path = self._download_to_temp_file(content=content, source_type=st)
            content = str(tmp_path.resolve())
        if st == "pdf":
            doc_name = req.doc_name or self._guess_doc_name(req.content, fallback="inspection_report.pdf")
            doc = DocumentSource(
                dataset_id="inspection_extract",
                doc_name=doc_name,
                namespace=None,
                content=content,
                source_type="pdf",
                metadata={},
            )
            try:
                routed_doc, mineru_wall_s = prepare_pdf_document_for_pipeline(doc)
                parse_route = "mineru" if mineru_wall_s is not None else "pdf_text"
                parsed = self._parser.parse(routed_doc.content, routed_doc.source_type)
                return parsed, parse_route
            finally:
                if tmp_path is not None:
                    tmp_path.unlink(missing_ok=True)
        try:
            parsed = self._parser.parse(content, st)
            if st in {"doc", "docx"}:
                return parsed, "docx"
            if st in {"markdown", "md"}:
                return parsed, "markdown"
            return parsed, "text"
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

    async def _run_llm_extraction(
        self,
        req: InspectionExtractRequest,
        parsed_text: str,
        *,
        prompt_version: str,
        model: str,
    ) -> list[dict[str, Any]]:
        snippets = parsed_text[:20000]
        llm_timeout_s = float(getattr(self._cfg, "llm_timeout_seconds", 180.0))
        parse_tpl = self._get_prompt_content(scene="inspection_extract_parse", user_id=req.user_id, version=prompt_version)
        classify_tpl = self._get_prompt_content(
            scene="inspection_extract_classify", user_id=req.user_id, version=prompt_version
        )
        repair_tpl = self._get_prompt_content(scene="inspection_extract_repair", user_id=req.user_id, version=prompt_version)

        parse_prompt = (
            f"{parse_tpl}\n\n"
            "请仅输出 JSON。顶层必须是对象，且包含 records 数组。\n"
            "records 每项尽量包含：检测位置、行号、管号、壁厚、evidence。\n"
            f"文档内容如下（已截断）：\n{snippets}"
        )
        logger.info(
            "inspection_extract llm stage=parse model=%s prompt_chars=%s timeout_s=%.1f",
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
        stage1 = self._extract_json_like(parse_result)
        stage1_records = self._extract_records(stage1)
        logger.info("inspection_extract llm stage=parse result_records=%s", len(stage1_records))

        classify_prompt = (
            f"{classify_tpl}\n\n"
            "请仅输出 JSON。顶层对象包含 records 数组。\n"
            "检测类型只能是：测厚/缺陷。\n"
            "缺陷类型只能是：高温腐蚀、磨损、结渣、蠕变、管道变形、表面吹损、氧化皮堆积、机械损伤。\n"
            "是否换管只能是：是/否。\n"
            f"候选记录(JSON)：{json.dumps(stage1_records, ensure_ascii=False)}\n"
            f"文档摘要：{snippets[:8000]}"
        )
        logger.info(
            "inspection_extract llm stage=classify model=%s records_in=%s prompt_chars=%s timeout_s=%.1f",
            model,
            len(stage1_records),
            len(classify_prompt),
            llm_timeout_s,
        )
        classify_result = await self._llm.generate(
            model=model,
            prompt=classify_prompt,
            timeout=llm_timeout_s,
            max_tokens=int(getattr(self._cfg, "llm_max_tokens_classify", 1024)),
        )
        stage2 = self._extract_json_like(classify_result)
        stage2_records = self._extract_records(stage2)
        logger.info("inspection_extract llm stage=classify result_records=%s", len(stage2_records))

        if not self._need_repair(stage2_records):
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
            repaired_result = await self._llm.generate(
                model=model,
                prompt=repair_prompt,
                timeout=llm_timeout_s,
                max_tokens=int(getattr(self._cfg, "llm_max_tokens_repair", 768)),
            )
            stage3 = self._extract_json_like(repaired_result)
            candidate_records = self._extract_records(stage3)
            logger.info("inspection_extract llm stage=repair result_records=%s", len(candidate_records))
            if candidate_records:
                break
        return candidate_records

    @staticmethod
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

    def _post_process_records(
        self,
        *,
        raw_records: list[dict[str, Any]],
        return_evidence: bool,
        threshold_rules: list[dict[str, Any]],
        parsed_text: str,
    ) -> tuple[list[InspectionRecord], list[str]]:
        line_index = self._build_line_index(parsed_text)
        normalized: list[InspectionRecord] = []
        warnings: list[str] = []
        for i, item in enumerate(raw_records, start=1):
            canon = self._canonicalize_record(item, threshold_rules=threshold_rules, line_index=line_index)
            if not return_evidence:
                canon["evidence"] = None
            try:
                normalized.append(InspectionRecord.model_validate(canon))
            except ValidationError as exc:
                INSPECT_EXTRACT_VALIDATION_FAIL_COUNT.inc()
                warnings.append(f"record_{i}_invalid:{exc.errors()[0].get('msg', 'validation error')}")
        return normalized, warnings

    @staticmethod
    def _build_summary(records: list[InspectionRecord], warnings: list[str]) -> InspectionSummary:
        defect_count = sum(1 for x in records if x.detection_type == DetectionType.DEFECT)
        replace_count = sum(1 for x in records if x.replaced == ReplaceFlag.YES)
        return InspectionSummary(
            total=len(records),
            defect_count=defect_count,
            replace_count=replace_count,
            warnings=warnings,
        )

    def _get_prompt_content(self, *, scene: str, user_id: str, version: str) -> str:
        tpl = self._prompts.get_template(scene=scene, user_id=user_id, version=version)
        if tpl and tpl.content:
            return tpl.content
        fallback = self._prompts.get_template(scene="inspection_extract", user_id=user_id, version=version)
        if fallback and fallback.content:
            return fallback.content
        return "你是检修报告结构化抽取助手。"

    @staticmethod
    def _extract_json_like(raw: str) -> dict[str, Any] | list[Any] | None:
        text = (raw or "").strip()
        if not text:
            return None
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
        if fence:
            text = fence.group(1).strip()
        # 优先尝试完整 JSON
        try:
            parsed = json.loads(text)
            if isinstance(parsed, (dict, list)):
                return parsed
        except json.JSONDecodeError:
            pass
        # 回退提取对象
        obj_start = text.find("{")
        obj_end = text.rfind("}")
        if obj_start >= 0 and obj_end > obj_start:
            try:
                parsed = json.loads(text[obj_start : obj_end + 1])
                if isinstance(parsed, (dict, list)):
                    return parsed
            except json.JSONDecodeError:
                pass
        # 回退提取数组
        arr_start = text.find("[")
        arr_end = text.rfind("]")
        if arr_start >= 0 and arr_end > arr_start:
            try:
                parsed = json.loads(text[arr_start : arr_end + 1])
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                return None
        return None

    @staticmethod
    def _extract_records(payload: dict[str, Any] | list[Any] | None) -> list[dict[str, Any]]:
        if payload is None:
            return []
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        rows = payload.get("records")
        if isinstance(rows, list):
            return [x for x in rows if isinstance(x, dict)]
        return []

    def _canonicalize_record(
        self,
        item: dict[str, Any],
        *,
        threshold_rules: list[dict[str, Any]],
        line_index: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        location = self._pick(item, ["检测位置", "location", "position"])
        row_no = self._pick(item, ["行号", "row_no", "row"])
        tube_no = self._pick(item, ["管号", "tube_no", "tube"])
        thickness_raw = self._pick(item, ["壁厚", "thickness", "thk"])
        detection_raw = self._pick(item, ["检测类型", "detection_type", "type"])
        defect_raw = self._pick(item, ["缺陷类型", "defect_type"])
        replaced_raw = self._pick(item, ["是否换管", "replaced", "replace_flag"])
        evidence = self._pick(item, ["evidence", "证据", "依据"])
        warnings = item.get("warnings")
        warn_list = [str(x) for x in warnings] if isinstance(warnings, list) else []

        thickness = self._parse_float(thickness_raw)
        threshold, threshold_source = self._select_threshold_for_location(
            str(location or ""),
            threshold_rules=threshold_rules,
            row_no=row_no,
            line_index=(line_index or {}),
        )
        detection_type = self._normalize_detection_type(
            detection_raw,
            defect_raw=defect_raw,
            thickness=thickness,
            threshold=threshold,
            evidence=evidence,
        )
        defect_type = self._normalize_defect_type(defect_raw)
        replaced = self._normalize_replaced(replaced_raw, detection_type=detection_type)
        if detection_type == DetectionType.MEASUREMENT:
            defect_type = None
        if detection_type == DetectionType.DEFECT and defect_type is None:
            defect_type = DefectType.MECHANICAL_DAMAGE
            warn_list.append("缺陷类型缺失，已默认归一为机械损伤")
        if threshold is not None:
            warn_list.append(f"阈值绑定:{threshold:.3f}mm")
        if threshold_source:
            warn_list.append(f"阈值命中来源:{threshold_source}")
        return {
            "location": str(location or "").strip(),
            "row_no": str(row_no or "").strip(),
            "tube_no": str(tube_no or "").strip(),
            "thickness": thickness,
            "detection_type": detection_type,
            "defect_type": defect_type,
            "replaced": replaced,
            "evidence": (str(evidence).strip() if evidence is not None else None),
            "warnings": warn_list,
        }

    @staticmethod
    def _pick(item: dict[str, Any], keys: list[str]) -> Any:
        for k in keys:
            if k in item and item[k] not in (None, ""):
                return item[k]
        return None

    @staticmethod
    def _parse_float(v: Any) -> float:
        if isinstance(v, (int, float)):
            return float(v)
        text = str(v or "").strip()
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        if not match:
            return 0.0
        return float(match.group(0))

    @staticmethod
    def _normalize_detection_type(
        v: Any,
        *,
        defect_raw: Any,
        thickness: float,
        threshold: float | None,
        evidence: Any,
    ) -> DetectionType:
        text = str(v or "").strip()
        if text in {"测厚", "正常", "测量"}:
            return DetectionType.MEASUREMENT
        if text in {"缺陷", "异常"}:
            return DetectionType.DEFECT
        ev_text = str(evidence or "").strip()
        if any(k in ev_text for k in ("超标", "减薄", "腐蚀", "磨损", "缺陷", "吹损", "变形")):
            return DetectionType.DEFECT
        if threshold is not None and thickness > 0 and thickness < threshold:
            return DetectionType.DEFECT
        if defect_raw not in (None, "", "无", "none", "None"):
            return DetectionType.DEFECT
        if thickness <= 0:
            return DetectionType.DEFECT
        return DetectionType.MEASUREMENT

    @staticmethod
    def _normalize_defect_type(v: Any) -> DefectType | None:
        text = str(v or "").strip()
        if not text or text.lower() in {"none", "null", "无"}:
            return None
        if text in {x.value for x in DefectType}:
            return DefectType(text)
        mapping = {
            "高温氧化": DefectType.HIGH_TEMP_CORROSION,
            "腐蚀": DefectType.HIGH_TEMP_CORROSION,
            "吹蚀": DefectType.SURFACE_EROSION,
            "冲刷": DefectType.SURFACE_EROSION,
            "磨蚀": DefectType.WEAR,
            "变形": DefectType.PIPE_DEFORMATION,
            "结焦": DefectType.SLAGGING,
            "积灰": DefectType.OXIDE_SCALE,
            "氧化皮": DefectType.OXIDE_SCALE,
            "损伤": DefectType.MECHANICAL_DAMAGE,
        }
        for k, val in mapping.items():
            if k in text:
                return val
        return None

    @staticmethod
    def _normalize_replaced(v: Any, *, detection_type: DetectionType) -> ReplaceFlag:
        text = str(v or "").strip().lower()
        if text in {"是", "y", "yes", "true", "1", "已更换", "更换"}:
            return ReplaceFlag.YES
        if text in {"否", "n", "no", "false", "0", "未更换"}:
            return ReplaceFlag.NO
        if detection_type == DetectionType.MEASUREMENT:
            return ReplaceFlag.NO
        return ReplaceFlag.NO

    @staticmethod
    def _guess_doc_name(content: str, fallback: str) -> str:
        p = DocumentParser.resolve_local_path(content)
        if p is not None:
            return p.name
        raw = (content or "").strip()
        if raw.lower().startswith("file://"):
            raw = raw[7:]
        guess = Path(raw).name.strip()
        return guess or fallback

    @staticmethod
    def _looks_like_http_url(content: str) -> bool:
        s = (content or "").strip().lower()
        return s.startswith("http://") or s.startswith("https://")

    @staticmethod
    def _guess_source_type_from_name(file_name: str) -> str:
        suffix = Path(file_name).suffix.lower()
        if suffix in {".doc", ".docx"}:
            return "docx"
        if suffix == ".pdf":
            return "pdf"
        if suffix in {".md", ".markdown"}:
            return "markdown"
        if suffix in {".txt"}:
            return "text"
        return "text"

    @staticmethod
    def _download_to_temp_file(*, content: str, source_type: str) -> Path:
        timeout = httpx.Timeout(120.0, connect=30.0)
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(content)
            resp.raise_for_status()
            data = resp.content
        suffix = ".pdf" if source_type == "pdf" else ".docx"
        fd, path_str = tempfile.mkstemp(prefix="inspect_extract_", suffix=suffix)
        path = Path(path_str)
        try:
            import os

            with os.fdopen(fd, "wb") as f:
                f.write(data)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return path

    def _build_minio_client(self):
        if Minio is None:
            logger.warning("minio package not installed; inspection upload disabled.")
            return None
        endpoint = (self._chat_cfg.image_minio_endpoint or "").strip()
        access_key = (self._chat_cfg.image_minio_access_key or "").strip()
        secret_key = (self._chat_cfg.image_minio_secret_key or "").strip()
        if not endpoint or not access_key or not secret_key:
            logger.warning("minio config incomplete; inspection upload disabled.")
            return None
        try:
            client = Minio(
                endpoint,
                access_key=access_key,
                secret_key=secret_key,
                secure=bool(self._chat_cfg.image_minio_secure),
            )
            if bool(self._chat_cfg.image_minio_auto_create_bucket) and not client.bucket_exists(self._minio_bucket):
                client.make_bucket(self._minio_bucket)
            return client
        except Exception as exc:  # noqa: BLE001
            logger.warning("init minio client for inspection upload failed: %s", exc)
            return None

    @staticmethod
    def _extract_threshold_rules(parsed_text: str) -> list[dict[str, Any]]:
        rules: list[dict[str, Any]] = []
        lines = [x.strip() for x in (parsed_text or "").splitlines() if x.strip()]
        for idx, line in enumerate(lines):
            match = re.search(r"(低于|小于|不足|<|≤|<=)\s*([0-9]+(?:\.[0-9]+)?)\s*mm", line, re.IGNORECASE)
            if not match:
                continue
            th = float(match.group(2))
            # 从阈值行之前回溯定位语义（近邻位置描述 + 标题语境）
            location_hint = ""
            context_lines: list[str] = []
            for back in range(max(0, idx - 5), idx):
                cand = lines[back]
                context_lines.append(cand)
                if any(k in cand for k in ("墙", "吹灰器", "水冷壁", "再热器", "过热器", "位置", "区域", "上数", "下数")):
                    location_hint = cand
            if not location_hint and context_lines:
                location_hint = context_lines[-1]
            rules.append(
                {
                    "threshold": th,
                    "location_hint": location_hint,
                    "tokens": InspectionExtractService._location_tokens(location_hint),
                    "line_idx": idx,
                }
            )
        return rules

    @staticmethod
    def _select_threshold_for_location(
        location: str,
        *,
        threshold_rules: list[dict[str, Any]],
        row_no: Any,
        line_index: dict[str, int],
    ) -> tuple[float | None, str]:
        if not threshold_rules:
            return None, "未命中"
        loc = (location or "").strip()
        row_text = str(row_no or "").strip()
        near_rule = InspectionExtractService._pick_nearest_rule_by_row_context(
            location=loc,
            row_text=row_text,
            threshold_rules=threshold_rules,
            line_index=line_index,
        )
        if near_rule is not None:
            return float(near_rule["threshold"]), "段落近邻"
        if loc:
            loc_tokens = InspectionExtractService._location_tokens(loc)
            scored: list[tuple[float, dict[str, Any]]] = []
            for rule in threshold_rules:
                hint = str(rule.get("location_hint") or "").strip()
                if not hint:
                    continue
                if hint in loc or loc in hint:
                    return float(rule["threshold"]), "位置强匹配"
                rule_tokens = rule.get("tokens") or []
                overlap = len(set(loc_tokens) & set(rule_tokens))
                if overlap <= 0:
                    continue
                # 重叠词越多分越高；字符覆盖用于细化同分情况
                coverage = overlap / max(1, len(set(rule_tokens)))
                scored.append((coverage, rule))
            if scored:
                scored.sort(key=lambda x: (x[0], -int(x[1].get("line_idx", 0))), reverse=True)
                return float(scored[0][1]["threshold"]), "位置相似匹配"
        # 无匹配时：仅在“唯一阈值”场景下使用全局回退，避免跨区域误判
        if len(threshold_rules) == 1:
            return float(threshold_rules[0]["threshold"]), "全局回退"
        return None, "未命中"

    @staticmethod
    def _build_line_index(parsed_text: str) -> dict[str, int]:
        lines = [x.strip() for x in (parsed_text or "").splitlines() if x.strip()]
        index: dict[str, int] = {}
        for i, line in enumerate(lines):
            if line not in index:
                index[line] = i
        return index

    @staticmethod
    def _pick_nearest_rule_by_row_context(
        *,
        location: str,
        row_text: str,
        threshold_rules: list[dict[str, Any]],
        line_index: dict[str, int],
    ) -> dict[str, Any] | None:
        if not location or not row_text:
            return None
        try:
            _ = int(float(row_text))
        except Exception:  # noqa: BLE001
            return None
        loc_idx = line_index.get(location)
        if loc_idx is None:
            return None
        candidates: list[tuple[int, dict[str, Any]]] = []
        for rule in threshold_rules:
            line_idx = int(rule.get("line_idx", -1))
            if line_idx < 0:
                continue
            # 限定同段：location 附近 18 行内的阈值
            if abs(line_idx - loc_idx) > 18:
                continue
            hint = str(rule.get("location_hint") or "")
            if hint and not (hint in location or location in hint):
                # 位置不一致，跳过段内伪匹配
                continue
            candidates.append((abs(line_idx - loc_idx), rule))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    @staticmethod
    def _location_tokens(text: str) -> list[str]:
        raw = (text or "").strip()
        if not raw:
            return []
        # 英文/数字连续串 + 常见中文地理/设备片段
        parts = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{1,4}", raw)
        out: list[str] = []
        for p in parts:
            token = p.strip()
            if not token:
                continue
            if token in {"检测", "数据", "报告", "内容", "说明"}:
                continue
            out.append(token)
        return out

