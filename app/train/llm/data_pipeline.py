from __future__ import annotations

"""
LabelStudio 导出 → 统一训练 JSON/JSONL，以及数据集校验 / 合并。

统一样本 schema 见 docs/大模型微调实现方案.md §4。
"""

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from app.core.logging import get_logger

logger = get_logger(__name__)

REQUIRED_SAMPLE_KEYS = ("id", "modality", "task_profile", "messages")


def _read_json_any(path: str | Path) -> Any:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".jsonl":
        rows = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        return rows
    return json.loads(text)


def _write_samples(path: str | Path, samples: Sequence[Dict[str, Any]], *, as_jsonl: bool = True) -> str:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if as_jsonl or out.suffix.lower() == ".jsonl":
        with out.open("w", encoding="utf-8") as f:
            for row in samples:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    else:
        out.write_text(json.dumps(list(samples), ensure_ascii=False, indent=2), encoding="utf-8")
    return str(out.resolve())


def load_samples(path: str | Path) -> List[Dict[str, Any]]:
    data = _read_json_any(path)
    if isinstance(data, dict) and "samples" in data:
        data = data["samples"]
    if not isinstance(data, list):
        raise ValueError(f"dataset must be a list or jsonl, got {type(data)}")
    return [x for x in data if isinstance(x, dict)]


def render_vl_ops_assistant(structured: Dict[str, Any]) -> str:
    parts: List[str] = ["基于图像分析，发现以下情况：", "", "## 特殊作业检测"]
    ops = structured.get("special_operations") or []
    if ops:
        for op in ops:
            parts.append(f"- 作业类型：{op.get('operation_type', '未知')}")
            if op.get("bbox"):
                parts.append(f"  位置：{op.get('bbox')}")
            parts.append(f"  描述：{op.get('description', '无描述')}")
            parts.append("")
    else:
        parts.append("未检测到特殊作业")
        parts.append("")

    risk_level = structured.get("risk_level")
    if risk_level:
        parts.append("## 整体风险等级")
        parts.append(f"风险等级：{risk_level}")
        parts.append("")

    overview = structured.get("operation_overview")
    if overview:
        parts.append("## 作业整体描述")
        parts.append(str(overview))
        parts.append("")

    parts.append("## 风险点识别")
    risks = structured.get("risk_points") or []
    if risks:
        for risk in risks:
            parts.append(f"- 风险类型：{risk.get('risk_type', '未知')}")
            parts.append(f"  描述：{risk.get('description', '无描述')}")
            parts.append("")
    else:
        parts.append("未检测到风险点")
        parts.append("")

    parts.append("## 措施建议")
    measures = structured.get("measures") or structured.get("safety_measures") or []
    if measures:
        for m in measures:
            parts.append(f"- {m.get('measure_type', '其他')}")
            parts.append(f"  描述：{m.get('description', '无描述')}")
            parts.append("")
    else:
        parts.append("无特殊措施建议")
        parts.append("")
    return "\n".join(parts).strip()


def render_defect_assistant(structured: Dict[str, Any]) -> str:
    defects = structured.get("defects") or []
    lines = ["基于图像分析，识别到以下缺陷：", ""]
    if not defects:
        lines.append("未发现明显缺陷。")
        return "\n".join(lines)
    for d in defects:
        lines.append(f"- 类型：{d.get('defect_type', '未知')}")
        if d.get("bbox"):
            lines.append(f"  位置：{d.get('bbox')}")
        lines.append(f"  描述：{d.get('description', '无描述')}")
        lines.append("")
    return "\n".join(lines).strip()


def default_user_prompt(profile: str) -> str:
    if profile == "vl_ops":
        return (
            "<image>\n请分析以下监控图像，识别特殊作业类型、风险点并给出措施建议。"
        )
    if profile == "defect_vl":
        return "<image>\n请识别图中设备/管线缺陷类型、位置并给出简要描述。"
    return "请根据上下文回答用户问题。"


class LabelStudioConverter:
    """将 LabelStudio 导出转为统一训练样本。"""

    def __init__(self, task_profile: str = "vl_ops") -> None:
        self.task_profile = task_profile
        self.risk_mapping = {
            "个人防护缺失": "个人防护缺失",
            "环境风险": "环境风险",
            "作业违规": "作业违规",
            "设备故障": "设备故障",
            "安全标识缺失": "安全标识缺失",
            "无风险点": "无风险点",
            "人员防护缺失": "个人防护缺失",
            "个人防护问题": "个人防护缺失",
        }

    def convert_bbox(self, bbox_data: Dict[str, Any], image_width: int, image_height: int) -> List[float]:
        x = int(bbox_data.get("x", 0) / 100.0 * image_width)
        y = int(bbox_data.get("y", 0) / 100.0 * image_height)
        w = int(bbox_data.get("width", 0) / 100.0 * image_width)
        h = int(bbox_data.get("height", 0) / 100.0 * image_height)
        return [x, y, w, h]

    def parse_measures(self, text_data: List[str]) -> List[Dict[str, str]]:
        measures: List[Dict[str, str]] = []
        for text in text_data:
            if not str(text).strip():
                continue
            measure_type = "其他"
            if any(k in text for k in ("停止", "暂停", "中断")):
                measure_type = "立即停止"
            elif any(k in text for k in ("安全帽", "安全带", "防护服", "防护")):
                measure_type = "个人防护"
            elif any(k in text for k in ("清理", "环境", "场地")):
                measure_type = "环境改善"
            elif any(k in text for k in ("许可", "审批")):
                measure_type = "作业许可"
            elif any(k in text for k in ("监护", "监督")):
                measure_type = "监护措施"
            measures.append({"measure_type": measure_type, "description": str(text).strip()})
        return measures

    def align_risk_descriptions(self, selected: List[str], description_texts: List[str]) -> List[Dict[str, str]]:
        description_full = "\n".join([t for t in description_texts if str(t).strip()])
        aligned: Dict[str, str] = {}
        global_notes: List[str] = []
        if description_full:
            parts = [p.strip() for p in re.split(r"[；;\n]", description_full) if p.strip()]
            for part in parts:
                if "：" in part or ":" in part:
                    sep = "：" if "：" in part else ":"
                    t, d = part.split(sep, 1)
                    canonical = self.risk_mapping.get(t.strip(), t.strip())
                    aligned[canonical] = d.strip()
                else:
                    global_notes.append(part)
        selected_canonical = [self.risk_mapping.get(r, r) for r in selected]
        if aligned:
            return [
                {"risk_type": r, "description": aligned.get(r) or f"检测到{r}"}
                for r in selected_canonical
            ]
        if global_notes and len(selected_canonical) == 1:
            return [{"risk_type": selected_canonical[0], "description": "；".join(global_notes)}]
        return [{"risk_type": r, "description": f"检测到{r}"} for r in selected_canonical]

    def _resolve_image_path(
        self,
        task: Dict[str, Any],
        image_root: Optional[str],
    ) -> str:
        data = task.get("data") or {}
        raw = data.get("image") or data.get("img") or ""
        name = Path(str(raw).replace("\\", "/")).name
        if image_root:
            root = Path(image_root)
            cand = root / name
            if cand.exists():
                return str(cand)
            # LabelStudio 常带 uuid 前缀：xxx-original.jpg
            if "-" in name:
                original = name.split("-", 1)[1]
                cand2 = root / original
                if cand2.exists():
                    return str(cand2)
                for fn in root.iterdir():
                    if fn.is_file() and fn.name.lower() == original.lower():
                        return str(fn)
        return str(raw)

    def process_vl_ops_task(self, task: Dict[str, Any], image_path: str) -> Dict[str, Any]:
        annotations = task.get("annotations") or []
        if not annotations:
            raise ValueError(f"task {task.get('id')} has no annotations")
        result = annotations[0].get("result") or []
        structured: Dict[str, Any] = {
            "special_operations": [],
            "risk_points": [],
            "measures": [],
            "risk_level": None,
            "operation_overview": None,
        }
        selected_risks: List[str] = []
        image_width = 1920
        image_height = 1080
        for item in result:
            original = item.get("original_width") or item.get("original_height")
            if item.get("original_width"):
                image_width = int(item["original_width"])
            if item.get("original_height"):
                image_height = int(item["original_height"])
            from_name = item.get("from_name", "")
            item_type = item.get("type", "")
            value = item.get("value") or {}
            if from_name == "risk_level" and item_type == "choices":
                choices = value.get("choices") or []
                if choices:
                    structured["risk_level"] = choices[0]
            elif from_name == "special_operations" and item_type == "rectanglelabels":
                labels = value.get("rectanglelabels") or []
                if labels:
                    structured["special_operations"].append(
                        {
                            "operation_type": labels[0],
                            "bbox": self.convert_bbox(value, image_width, image_height),
                            "description": f"检测到{labels[0]}",
                        }
                    )
            elif from_name == "risk_points" and item_type == "choices":
                selected_risks.extend(value.get("choices") or [])
            elif from_name == "risk_description" and item_type == "textarea":
                texts = value.get("text") or []
                if selected_risks:
                    structured["risk_points"].extend(self.align_risk_descriptions(selected_risks, texts))
                    selected_risks = []
            elif from_name in ("safety_measures", "measures") and item_type == "textarea":
                structured["measures"].extend(self.parse_measures(value.get("text") or []))
            elif from_name == "operation_overview" and item_type == "textarea":
                texts = value.get("text") or []
                if texts:
                    structured["operation_overview"] = texts[0]
            elif from_name == "defects" and item_type == "rectanglelabels":
                labels = value.get("rectanglelabels") or []
                if labels:
                    structured.setdefault("defects", []).append(
                        {
                            "defect_type": labels[0],
                            "bbox": self.convert_bbox(value, image_width, image_height),
                            "description": f"检测到{labels[0]}",
                        }
                    )
            elif from_name == "defect_description" and item_type == "textarea":
                texts = value.get("text") or []
                defects = structured.setdefault("defects", [])
                if defects and texts:
                    defects[-1]["description"] = texts[0]
            _ = original  # silence unused in some LS exports

        if selected_risks and not structured["risk_points"]:
            structured["risk_points"] = self.align_risk_descriptions(selected_risks, [])

        if self.task_profile == "defect_vl":
            assistant = render_defect_assistant(structured)
            user = default_user_prompt("defect_vl")
        else:
            assistant = render_vl_ops_assistant(structured)
            user = default_user_prompt("vl_ops")

        sample_id = str(task.get("id") or Path(image_path).stem)
        return {
            "id": sample_id,
            "modality": "vision",
            "task_profile": self.task_profile,
            "images": [image_path],
            "messages": [
                {"role": "user", "content": user},
                {"role": "assistant", "content": assistant},
            ],
            "meta": {
                "source": "labelstudio",
                "project": str(task.get("project") or ""),
                "structured": structured,
            },
        }

    def convert_export(
        self,
        export_path: str | Path,
        *,
        image_root: Optional[str] = None,
        out_path: Optional[str | Path] = None,
        as_jsonl: bool = True,
    ) -> Dict[str, Any]:
        raw = _read_json_any(export_path)
        if not isinstance(raw, list):
            raise ValueError("LabelStudio export must be a JSON array")
        samples: List[Dict[str, Any]] = []
        errors: List[str] = []
        for idx, task in enumerate(raw):
            try:
                image_path = self._resolve_image_path(task, image_root)
                if self.task_profile in ("vl_ops", "defect_vl"):
                    samples.append(self.process_vl_ops_task(task, image_path))
                elif self.task_profile == "chat_sft":
                    samples.append(self._process_chat_sft_task(task, idx))
                else:
                    raise ValueError(f"unsupported task_profile={self.task_profile}")
            except Exception as exc:  # noqa: BLE001
                msg = f"task[{idx}] id={task.get('id')}: {exc}"
                logger.warning(msg)
                errors.append(msg)
        out = out_path
        if out is None:
            stem = Path(export_path).stem
            out = Path("data/llm_train/converted") / f"{stem}_{self.task_profile}.jsonl"
        written = _write_samples(out, samples, as_jsonl=as_jsonl)
        return {
            "ok": True,
            "out_path": written,
            "total_tasks": len(raw),
            "converted": len(samples),
            "errors": errors,
        }

    def _process_chat_sft_task(self, task: Dict[str, Any], idx: int) -> Dict[str, Any]:
        data = task.get("data") or {}
        annotations = task.get("annotations") or []
        user = str(data.get("prompt") or data.get("text") or data.get("question") or "").strip()
        assistant = ""
        if annotations:
            for item in annotations[0].get("result") or []:
                if item.get("from_name") in ("response", "answer") and item.get("type") == "textarea":
                    texts = (item.get("value") or {}).get("text") or []
                    if texts:
                        assistant = str(texts[0]).strip()
                        break
        if not user or not assistant:
            raise ValueError("chat_sft requires prompt/question and response/answer")
        return {
            "id": str(task.get("id") or f"chat_{idx}"),
            "modality": "text",
            "task_profile": "chat_sft",
            "images": [],
            "messages": [
                {"role": "user", "content": user},
                {"role": "assistant", "content": assistant},
            ],
            "meta": {"source": "labelstudio", "structured": {}},
        }


def convert_legacy_ops_json(
    path: str | Path,
    *,
    out_path: Optional[str | Path] = None,
    task_profile: str = "vl_ops",
) -> Dict[str, Any]:
    """兼容参考项目 image_path + safety_analysis / structured 旧格式。"""
    raw = _read_json_any(path)
    if not isinstance(raw, list):
        raise ValueError("legacy dataset must be a list")
    samples: List[Dict[str, Any]] = []
    for i, item in enumerate(raw):
        if "messages" in item and "modality" in item:
            samples.append(item)
            continue
        image_path = item.get("image_path") or (item.get("images") or [None])[0]
        structured = item.get("meta", {}).get("structured") if isinstance(item.get("meta"), dict) else None
        structured = structured or item.get("safety_analysis") or item.get("structured") or {}
        if "safety_measures" in structured and "measures" not in structured:
            structured = dict(structured)
            structured["measures"] = structured.get("safety_measures") or []
        assistant = (
            render_defect_assistant(structured)
            if task_profile == "defect_vl"
            else render_vl_ops_assistant(structured)
        )
        samples.append(
            {
                "id": str(item.get("id") or f"legacy_{i}"),
                "modality": "vision",
                "task_profile": task_profile,
                "images": [image_path] if image_path else [],
                "messages": [
                    {"role": "user", "content": default_user_prompt(task_profile)},
                    {"role": "assistant", "content": assistant},
                ],
                "meta": {"source": "legacy", "structured": structured},
            }
        )
    out = out_path or Path("data/llm_train/converted") / f"{Path(path).stem}_unified.jsonl"
    written = _write_samples(out, samples, as_jsonl=True)
    return {"ok": True, "out_path": written, "converted": len(samples), "errors": []}


def validate_dataset(path: str | Path, *, check_images: bool = True) -> Dict[str, Any]:
    samples = load_samples(path)
    report: Dict[str, Any] = {
        "path": str(path),
        "total_samples": len(samples),
        "valid_samples": 0,
        "invalid_samples": 0,
        "errors": [],
        "warnings": [],
        "modality_counts": {"text": 0, "vision": 0, "other": 0},
    }
    for i, sample in enumerate(samples):
        errs: List[str] = []
        for key in REQUIRED_SAMPLE_KEYS:
            if key not in sample:
                errs.append(f"missing field: {key}")
        modality = sample.get("modality")
        if modality == "text":
            report["modality_counts"]["text"] += 1
        elif modality == "vision":
            report["modality_counts"]["vision"] += 1
        else:
            report["modality_counts"]["other"] += 1
        messages = sample.get("messages")
        if not isinstance(messages, list) or len(messages) < 2:
            errs.append("messages must contain at least user+assistant")
        else:
            roles = [m.get("role") for m in messages if isinstance(m, dict)]
            if "user" not in roles or "assistant" not in roles:
                errs.append("messages must include user and assistant roles")
        if modality == "vision":
            images = sample.get("images") or []
            if not images:
                errs.append("vision sample requires images")
            elif check_images:
                for img in images:
                    if img and not os.path.exists(str(img)):
                        report["warnings"].append(f"sample[{i}] image missing: {img}")
        if errs:
            report["invalid_samples"] += 1
            report["errors"].append({"index": i, "id": sample.get("id"), "errors": errs})
        else:
            report["valid_samples"] += 1
    report["ok"] = report["invalid_samples"] == 0
    return report


def merge_exports(paths: Sequence[str | Path], out_path: str | Path) -> Dict[str, Any]:
    merged: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for p in paths:
        for sample in load_samples(p):
            sid = str(sample.get("id") or "")
            key = sid or json.dumps(sample, ensure_ascii=False, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            merged.append(sample)
    written = _write_samples(out_path, merged, as_jsonl=True)
    return {"ok": True, "out_path": written, "converted": len(merged), "sources": [str(x) for x in paths]}


def write_validation_report(report: Dict[str, Any], report_dir: str | Path = "data/llm_train/reports") -> str:
    out_dir = Path(report_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = Path(str(report.get("path") or "dataset")).stem
    out = out_dir / f"{name}_validate.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(out.resolve())
