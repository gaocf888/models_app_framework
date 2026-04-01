from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx

from app.core.config import MinerUConfig
from app.core.logging import get_logger
from app.rag.mineru_errors import MinerUParseError
from app.rag.mineru_response_parse import (
    extract_markdown_from_json,
    markdown_from_zip_bytes,
    read_markdown_from_disk,
)

logger = get_logger(__name__)

# mineru-api 同步响应头（与 MinerU FILE_PARSE_TASK_ID_HEADER 一致）
_MINERU_TASK_ID_HEADER = "x-mineru-task-id"


class MinerUClient:
    """同步调用 mineru-api `POST /file_parse`（官方文档：同步等待结果）。"""

    def __init__(self, cfg: MinerUConfig) -> None:
        self._cfg = cfg

    def parse_pdf_to_markdown(self, pdf_path: Path, *, doc_name: str) -> tuple[str, dict[str, Any]]:
        """
        上传本地 PDF，返回 (markdown, meta)。

        meta 含 mineru_job_id（响应头 X-MinerU-Task-Id）、http_status、elapsed 等。
        """
        url = f"{self._cfg.base_url.rstrip('/')}{self._cfg.file_parse_path}"
        timeout = httpx.Timeout(
            connect=120.0,
            read=max(60.0, float(self._cfg.timeout_s)),
            write=120.0,
            pool=120.0,
        )

        pdf_path = pdf_path.expanduser().resolve()
        if not pdf_path.is_file():
            raise MinerUParseError(f"PDF not found: {pdf_path}")

        logger.info(
            "MinerU file_parse start doc_name=%s url=%s backend=%s parse_method=%s lang=%s formula=%s table=%s batch_size=%s",
            doc_name,
            url,
            self._cfg.backend,
            self._cfg.parse_method,
            self._cfg.language,
            self._cfg.formula_enable,
            self._cfg.table_enable,
            self._cfg.page_batch_size,
        )
        ranges = self._build_page_ranges(pdf_path)
        md_parts: list[str] = []
        elapsed_total = 0.0
        last_status = 200
        task_ids: list[str] = []
        for start_page, end_page in ranges:
            md, task_id, status_code, elapsed = self._file_parse_once(
                pdf_path=pdf_path,
                url=url,
                timeout=timeout,
                doc_name=doc_name,
                start_page_id=start_page,
                end_page_id=end_page,
            )
            if md:
                md_parts.append(md)
            if task_id:
                task_ids.append(task_id)
            elapsed_total += elapsed
            last_status = status_code

        md_merged = "\n\n".join([p.strip() for p in md_parts if p.strip()]).strip()
        if not md_merged:
            raise MinerUParseError("MinerU returned no markdown content after page batching")

        meta = {
            "mineru_job_id": task_ids[-1] if task_ids else None,
            "mineru_job_ids": task_ids,
            "mineru_http_status": last_status,
            "mineru_parse_wall_s": round(elapsed_total, 3),
            "mineru_disk_fallback_subdir": self._cfg.disk_fallback_subdir,
            "mineru_page_batches": len(ranges),
        }
        logger.info(
            "MinerU file_parse ok doc_name=%s chars=%s wall_s=%s batches=%s",
            doc_name,
            len(md_merged),
            meta["mineru_parse_wall_s"],
            len(ranges),
        )
        return md_merged, meta

    def _build_page_ranges(self, pdf_path: Path) -> list[tuple[int | None, int | None]]:
        batch_size = int(self._cfg.page_batch_size or 0)
        if batch_size <= 0:
            return [(None, None)]
        try:
            from pypdf import PdfReader  # 延迟导入，避免无依赖时影响主流程

            total_pages = len(PdfReader(str(pdf_path)).pages)
        except Exception as e:  # noqa: BLE001
            logger.warning("MinerU page batching disabled (cannot read page count): %s", e)
            return [(None, None)]

        if total_pages <= 0:
            return [(None, None)]

        ranges: list[tuple[int, int]] = []
        start = 0
        while start < total_pages:
            end = min(start + batch_size - 1, total_pages - 1)
            ranges.append((start, end))
            start = end + 1
        return ranges

    def _file_parse_once(
        self,
        *,
        pdf_path: Path,
        url: str,
        timeout: httpx.Timeout,
        doc_name: str,
        start_page_id: int | None,
        end_page_id: int | None,
    ) -> tuple[str, str | None, int, float]:
        data = {
            "return_md": "true",
            "return_middle_json": "false",
            "backend": self._cfg.backend,
            "parse_method": self._cfg.parse_method,
            "lang_list": self._cfg.language,
            "formula_enable": "true" if self._cfg.formula_enable else "false",
            "table_enable": "true" if self._cfg.table_enable else "false",
        }
        if start_page_id is not None and end_page_id is not None:
            data["start_page_id"] = str(start_page_id)
            data["end_page_id"] = str(end_page_id)

        t0 = time.perf_counter()
        try:
            with pdf_path.open("rb") as f:
                files = {"files": (pdf_path.name, f, "application/pdf")}
                with httpx.Client(timeout=timeout) as client:
                    resp = client.post(url, data=data, files=files)
        except httpx.TimeoutException as e:
            logger.error("MinerU request timeout doc_name=%s url=%s timeout_s=%s err=%s", doc_name, url, self._cfg.timeout_s, e)
            raise MinerUParseError(f"MinerU HTTP timeout after {self._cfg.timeout_s}s: {e}", response_snippet=str(e)) from e
        except httpx.RequestError as e:
            logger.error("MinerU request error doc_name=%s url=%s err=%s", doc_name, url, e, exc_info=True)
            raise MinerUParseError(f"MinerU HTTP request failed: {e}", response_snippet=str(e)) from e

        elapsed = time.perf_counter() - t0
        body = resp.content
        snippet = body[:4000].decode("utf-8", errors="replace") if body else ""

        if resp.status_code >= 400:
            logger.error("MinerU HTTP error doc_name=%s status=%s body[:4000]=%s", doc_name, resp.status_code, snippet)
            raise MinerUParseError(
                f"MinerU HTTP {resp.status_code} for doc={doc_name}",
                status_code=resp.status_code,
                response_snippet=snippet,
            )

        md: str | None = None
        ct = (resp.headers.get("content-type") or "").lower()
        if "application/json" in ct or body[:1] in (b"{", b"["):
            try:
                payload = resp.json()
            except json.JSONDecodeError:
                payload = None
            if payload is not None:
                md = extract_markdown_from_json(payload)
        if not md:
            md = markdown_from_zip_bytes(body)

        task_id = (resp.headers.get(_MINERU_TASK_ID_HEADER) or "").strip()
        io_base = Path(self._cfg.io_path).expanduser()
        if not md:
            md = read_markdown_from_disk(io_base, task_id, output_subdir=self._cfg.disk_fallback_subdir)
        if not md or not md.strip():
            disk_hint = io_base / self._cfg.disk_fallback_subdir / task_id if task_id else None
            logger.error(
                "MinerU empty markdown doc_name=%s status=%s ct=%s snippet=%s task_id=%s disk_tried=%s pages=%s-%s",
                doc_name,
                resp.status_code,
                ct,
                snippet,
                task_id or "(no header)",
                disk_hint,
                start_page_id,
                end_page_id,
            )
            raise MinerUParseError(
                "MinerU returned no markdown content",
                status_code=resp.status_code,
                response_snippet=snippet,
                output_dir_hint=str(disk_hint) if disk_hint else None,
            )
        return md.strip(), (task_id or None), resp.status_code, elapsed
