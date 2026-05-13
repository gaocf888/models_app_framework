"""
检修 V0 LangGraph：状态、节点、编译与执行。

无 langgraph 或 sqlite checkpointer 依赖缺失时，退化为同语义顺序执行。
"""

from __future__ import annotations

import contextvars
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, TypedDict

from pydantic import BaseModel, Field

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.inspection_extract_v0.vision.layout_ocr_client import LayoutOcrClient, LayoutOcrError
from app.llm.client import VLLMHttpClient
from app.llm.prompt_registry import PromptTemplateRegistry
from app.models.inspection_extract import InspectionExtractRequest
from app.models.inspection_extract_v0 import InspectionExtractV0Request
from app.services.inspection_extract_llm_orchestrator import InspectionExtractJobCancelled
from app.services.inspection_extract_service import InspectionExtractService

logger = get_logger(__name__)

_CANCEL_CB: contextvars.ContextVar[Callable[[], bool] | None] = contextvars.ContextVar(
    "inspection_extract_v0_cancel_cb", default=None
)


def set_cancel_predicate(cb: Callable[[], bool] | None) -> contextvars.Token[Callable[[], bool] | None]:
    return _CANCEL_CB.set(cb)


def reset_cancel_predicate(token: contextvars.Token[Callable[[], bool] | None]) -> None:
    _CANCEL_CB.reset(token)


def _should_cancel() -> bool:
    cb = _CANCEL_CB.get()
    return bool(cb and cb())


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


class IrtBlockModel(BaseModel):
    block_id: str
    type: str = "text"
    page_no: int = 1
    text: str = ""
    confidence: float = 1.0
    bbox: dict[str, float] | None = None
    reading_order: int = 0


class IrtDocument(BaseModel):
    irt_version: str = Field(default="v0.1", description="IRT schema revision")
    parse_route: str
    engine_version: str | None = None
    ocr_engine: str | None = None
    layout_engine: str | None = None
    pages: list[dict[str, Any]] = Field(default_factory=list)
    blocks: list[IrtBlockModel] = Field(default_factory=list)
    tables: list[dict[str, Any]] = Field(default_factory=list)


def _strip_json_fence(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s).strip()
    return s


def _parse_llm_records_json(text: str) -> list[dict[str, Any]]:
    raw = _strip_json_fence(text)
    data = json.loads(raw)
    rows = data.get("records") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    return [x for x in rows if isinstance(x, dict)]


class InspectionExtractV0State(TypedDict, total=False):
    job_id: str
    job_dir: str
    request: dict[str, Any]
    local_input_path: str
    parsed_text: str
    parse_route: str
    layout_payload: dict[str, Any] | None
    irt: dict[str, Any]
    llm_raw: str
    raw_records: list[dict[str, Any]]
    stage_ms: dict[str, int]
    error: str | None
    ocr_engine: str | None
    layout_engine: str | None
    layout_api_version: str | None
    low_confidence: bool
    review_flags: list[str]
    validated_records: list[dict[str, Any]]
    validated_summary: dict[str, Any]
    validated_warnings: list[str]


def _merge_stage_ms(state: InspectionExtractV0State, key: str, ms: int) -> dict[str, int]:
    cur = dict(state.get("stage_ms") or {})
    cur[key] = int(ms)
    return cur


async def node_ingest(state: InspectionExtractV0State) -> dict[str, Any]:
    if _should_cancel():
        raise InspectionExtractJobCancelled()
    t0 = time.perf_counter()
    req = InspectionExtractV0Request.model_validate(state["request"])
    content = (req.content or "").strip()
    st = (req.source_type or "text").lower()
    tmp: Path | None = None
    try:
        if InspectionExtractService._looks_like_http_url(content) and st in {"pdf", "doc", "docx"}:
            tmp = InspectionExtractService._download_to_temp_file(content=content, source_type=st)
            local_path = str(tmp.resolve())
        else:
            local_path = content
        p = Path(local_path)
        if not p.is_file():
            raise FileNotFoundError(f"ingest path is not a file: {local_path}")
        _ = int((time.perf_counter() - t0) * 1000)
        return {"local_input_path": str(p.resolve())}
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def _pdf_text_pages(path: Path, *, max_pages: int) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    texts: list[str] = []
    n = min(len(reader.pages), max(1, int(max_pages)))
    for i in range(n):
        page = reader.pages[i]
        texts.append(page.extract_text() or "")
    return "\n".join(texts)


async def node_preprocess(state: InspectionExtractV0State) -> dict[str, Any]:
    if _should_cancel():
        raise InspectionExtractJobCancelled()
    t0 = time.perf_counter()
    req = InspectionExtractV0Request.model_validate(state["request"])
    st = (req.source_type or "text").lower()
    v0cfg = get_app_config().inspection_extract_v0
    path = Path(state["local_input_path"])
    if st == "pdf":
        parsed_text = _pdf_text_pages(path, max_pages=v0cfg.max_pdf_pages_preprocess)
        parse_route = "irt_pdf_ocr"
    else:
        legacy_req = InspectionExtractRequest.model_validate(req.model_dump())
        svc = InspectionExtractService()
        parsed_text, route = svc._parse_document(legacy_req.model_copy(update={"content": str(path), "source_type": st}))
        parse_route = "irt_native_docx" if st in {"doc", "docx"} else "irt_text_fallback"
        if route == "docx_v2":
            parse_route = "irt_native_docx"
    ms = int((time.perf_counter() - t0) * 1000)
    return {
        "parse_route": parse_route,
        "parsed_text": parsed_text,
        "stage_ms": _merge_stage_ms(state, "preprocess", ms),
    }


async def node_layout_ocr(state: InspectionExtractV0State) -> dict[str, Any]:
    if _should_cancel():
        raise InspectionExtractJobCancelled()
    st = (state["request"].get("source_type") or "text").lower()
    if st != "pdf":
        return {
            "layout_payload": None,
            "stage_ms": _merge_stage_ms(state, "layout_ocr", 0),
            "ocr_engine": None,
            "layout_engine": None,
            "layout_api_version": None,
        }
    t0 = time.perf_counter()
    v0cfg = get_app_config().inspection_extract_v0
    path = Path(state["local_input_path"])
    data = path.read_bytes()
    client = LayoutOcrClient(v0cfg)
    payload = await client.layout_ocr_pdf_or_image(
        file_bytes=data,
        filename=path.name,
        max_pages=v0cfg.max_pdf_pages_preprocess,
    )
    ms = int((time.perf_counter() - t0) * 1000)
    return {
        "layout_payload": payload,
        "ocr_engine": str(payload.get("ocr_engine") or "") or None,
        "layout_engine": str(payload.get("layout_engine") or "") or None,
        "layout_api_version": str(payload.get("engine_version") or "") or None,
        "stage_ms": _merge_stage_ms(state, "layout_ocr", ms),
    }


async def node_build_irt(state: InspectionExtractV0State) -> dict[str, Any]:
    if _should_cancel():
        raise InspectionExtractJobCancelled()
    t0 = time.perf_counter()
    pr = str(state.get("parse_route") or "irt_text_fallback")
    review_flags: list[str] = []
    low_conf = False
    if pr == "irt_pdf_ocr" and isinstance(state.get("layout_payload"), dict):
        lp = state["layout_payload"] or {}
        blocks_in = lp.get("blocks") if isinstance(lp.get("blocks"), list) else []
        blocks: list[IrtBlockModel] = []
        for i, b in enumerate(blocks_in):
            if not isinstance(b, dict):
                continue
            conf = float(b.get("confidence") or 1.0)
            if conf < 0.7:
                low_conf = True
                review_flags.append("ocr_conf_lt_0.7")
            blocks.append(
                IrtBlockModel(
                    block_id=str(b.get("block_id") or f"b{i}"),
                    type=str(b.get("type") or "text"),
                    page_no=int(b.get("page_no") or 1),
                    text=str(b.get("text") or ""),
                    confidence=conf,
                    bbox=b.get("bbox") if isinstance(b.get("bbox"), dict) else None,
                    reading_order=int(b.get("reading_order") or i),
                )
            )
        pages = lp.get("pages") if isinstance(lp.get("pages"), list) else []
        tables = lp.get("tables") if isinstance(lp.get("tables"), list) else []
        doc = IrtDocument(
            parse_route=pr,
            engine_version=str(lp.get("engine_version") or "") or None,
            ocr_engine=str(lp.get("ocr_engine") or "") or None,
            layout_engine=str(lp.get("layout_engine") or "") or None,
            pages=[p for p in pages if isinstance(p, dict)],
            blocks=blocks,
            tables=[t for t in tables if isinstance(t, dict)],
        )
    else:
        lines = [ln for ln in (state.get("parsed_text") or "").splitlines() if ln.strip()]
        blocks = [
            IrtBlockModel(
                block_id=f"L{i}",
                type="text",
                page_no=1,
                text=lines[i][:2000],
                confidence=1.0,
                reading_order=i,
            )
            for i in range(len(lines))
        ]
        doc = IrtDocument(parse_route=pr, pages=[{"page_no": 1, "width": 0, "height": 0}], blocks=blocks, tables=[])

    irt_dict = doc.model_dump(mode="json")
    jd = Path(state["job_dir"])
    _atomic_write_json(jd / "artifacts" / "irt.json", irt_dict)
    ms = int((time.perf_counter() - t0) * 1000)
    return {
        "irt": irt_dict,
        "stage_ms": _merge_stage_ms(state, "build_irt", ms),
        "low_confidence": low_conf if pr == "irt_pdf_ocr" else False,
        "review_flags": list(sorted(set(review_flags))),
    }


async def node_llm_extract(state: InspectionExtractV0State) -> dict[str, Any]:
    if _should_cancel():
        raise InspectionExtractJobCancelled()
    t0 = time.perf_counter()
    req = InspectionExtractV0Request.model_validate(state["request"])
    v0cfg = get_app_config().inspection_extract_v0
    prompts = PromptTemplateRegistry()
    pv = (req.prompt_version or v0cfg.prompt_version or "v1").strip() or "v1"
    tpl = prompts.get_template("inspection_extract_v0_extract", user_id=req.user_id, version=pv)
    if tpl is None:
        raise RuntimeError("missing prompt template scene=inspection_extract_v0_extract")
    irt = state.get("irt") or {}
    blocks = irt.get("blocks") if isinstance(irt.get("blocks"), list) else []
    slim_blocks = blocks[:80] if len(blocks) > 80 else blocks
    irt_slim = {**irt, "blocks": slim_blocks}
    snippet = (state.get("parsed_text") or "")[:6000]
    user_body = (
        "【IRT】\n"
        + json.dumps(irt_slim, ensure_ascii=False)[:24000]
        + "\n\n【原文片段】\n"
        + snippet
    )
    llm = VLLMHttpClient(timeout=float(v0cfg.llm_timeout_seconds))
    model = v0cfg.model_name or get_app_config().llm.default_model
    messages: list[dict[str, str]] = [
        {"role": "system", "content": tpl.content},
        {"role": "user", "content": user_body},
    ]
    last_err: Exception | None = None
    raw = ""
    for attempt in range(2):
        try:
            raw = await llm.chat(
                model,
                messages,
                max_tokens=int(v0cfg.llm_max_tokens_extract),
                temperature=float(v0cfg.llm_temperature),
                timeout=float(v0cfg.llm_timeout_seconds),
            )
            records = _parse_llm_records_json(raw)
            ms = int((time.perf_counter() - t0) * 1000)
            return {
                "llm_raw": raw,
                "raw_records": records,
                "stage_ms": _merge_stage_ms(state, "llm", ms),
            }
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if attempt == 0:
                messages = [
                    {"role": "system", "content": tpl.content},
                    {
                        "role": "user",
                        "content": user_body + "\n\n请严格只输出 JSON，顶层必须含 records 数组。",
                    },
                ]
                continue
            raise
    raise last_err or RuntimeError("llm_extract_failed")


async def node_validate(state: InspectionExtractV0State) -> dict[str, Any]:
    if _should_cancel():
        raise InspectionExtractJobCancelled()
    t0 = time.perf_counter()
    req = InspectionExtractV0Request.model_validate(state["request"])
    v0cfg = get_app_config().inspection_extract_v0
    svc = InspectionExtractService()
    strict = v0cfg.strict_default if req.strict is None else bool(req.strict)
    raw_records = list(state.get("raw_records") or [])
    threshold_rules = svc._extract_threshold_rules(state.get("parsed_text") or "")
    records, warnings = svc._post_process_records(
        raw_records=raw_records,
        return_evidence=req.return_evidence,
        threshold_rules=threshold_rules,
        parsed_text=state.get("parsed_text") or "",
    )
    if strict and not records:
        raise ValueError("strict mode enabled: no valid structured records extracted")
    ms = int((time.perf_counter() - t0) * 1000)
    return {
        "stage_ms": _merge_stage_ms(state, "postprocess", ms),
        "validated_records": [r.model_dump(mode="json") for r in records],
        "validated_warnings": warnings,
        "validated_summary": svc._build_summary(records, warnings).model_dump(mode="json"),
    }


def _build_graph_compiled(checkpointer: Any) -> Any:
    from langgraph.graph import END, StateGraph  # type: ignore[import-not-found]

    g = StateGraph(InspectionExtractV0State)
    g.add_node("ingest", node_ingest)
    g.add_node("preprocess", node_preprocess)
    g.add_node("layout_ocr", node_layout_ocr)
    g.add_node("build_irt", node_build_irt)
    g.add_node("llm_extract", node_llm_extract)
    g.add_node("validate", node_validate)
    g.set_entry_point("ingest")
    g.add_edge("ingest", "preprocess")
    g.add_edge("preprocess", "layout_ocr")
    g.add_edge("layout_ocr", "build_irt")
    g.add_edge("build_irt", "llm_extract")
    g.add_edge("llm_extract", "validate")
    g.add_edge("validate", END)
    if checkpointer is not None:
        return g.compile(checkpointer=checkpointer)
    return g.compile()


async def _run_sequential(initial: InspectionExtractV0State) -> InspectionExtractV0State:
    out: InspectionExtractV0State = dict(initial)
    for fn in (node_ingest, node_preprocess, node_layout_ocr, node_build_irt, node_llm_extract, node_validate):
        patch = await fn(out)
        out.update(patch)
    return out


async def run_inspection_extract_v0_graph(
    *,
    job_id: str,
    job_dir: Path,
    request: InspectionExtractV0Request,
    should_cancel: Callable[[], bool],
) -> dict[str, Any]:
    token = set_cancel_predicate(should_cancel)
    initial: InspectionExtractV0State = {
        "job_id": job_id,
        "job_dir": str(job_dir.resolve()),
        "request": request.model_dump(mode="json"),
        "stage_ms": {},
        "review_flags": [],
    }
    try:
        v0cfg = get_app_config().inspection_extract_v0
        ck_path = job_dir / v0cfg.langgraph_checkpoint_filename
        try:
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            logger.warning("inspection_extract_v0 AsyncSqliteSaver unavailable sequential fallback err=%s", exc)
            return await _run_sequential(initial)

        raw_path = os.fspath(ck_path.resolve())
        db_uri = "sqlite:///" + raw_path.replace(os.sep, "/")

        try:
            async with AsyncSqliteSaver.from_conn_string(db_uri) as saver:  # type: ignore[attr-defined]
                graph = _build_graph_compiled(saver)
                config: dict[str, Any] = {"configurable": {"thread_id": job_id}}
                final_state = await graph.ainvoke(initial, config)  # type: ignore[attr-defined]
                if isinstance(final_state, dict):
                    return final_state
                return dict(final_state)
        except Exception as exc:  # noqa: BLE001
            logger.warning("inspection_extract_v0 langgraph invoke failed sequential fallback err=%s", exc)
            return await _run_sequential(initial)
    finally:
        reset_cancel_predicate(token)
