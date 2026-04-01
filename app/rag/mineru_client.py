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

        # 官方 parse_request_form：无 output_dir；任务目录由服务端 task_id 决定（见 X-MinerU-Task-Id）
        data = {
            "return_md": "true",
            "return_middle_json": "false",
            "backend": self._cfg.backend,
            "parse_method": self._cfg.parse_method,
            "lang_list": self._cfg.language,
            "formula_enable": "true",
            "table_enable": "true",
        }

        logger.info(
            "MinerU file_parse start doc_name=%s url=%s backend=%s parse_method=%s lang=%s",
            doc_name,
            url,
            self._cfg.backend,
            self._cfg.parse_method,
            self._cfg.language,
        )

        t0 = time.perf_counter()
        try:
            with pdf_path.open("rb") as f:
                files = {"files": (pdf_path.name, f, "application/pdf")}
                with httpx.Client(timeout=timeout) as client:
                    resp = client.post(url, data=data, files=files)
        except httpx.TimeoutException as e:
            logger.error(
                "MinerU request timeout doc_name=%s url=%s timeout_s=%s err=%s",
                doc_name,
                url,
                self._cfg.timeout_s,
                e,
                exc_info=True,
            )
            raise MinerUParseError(
                f"MinerU HTTP timeout after {self._cfg.timeout_s}s: {e}",
                response_snippet=str(e),
            ) from e
        except httpx.RequestError as e:
            logger.error(
                "MinerU request error doc_name=%s url=%s err=%s",
                doc_name,
                url,
                e,
                exc_info=True,
            )
            raise MinerUParseError(f"MinerU HTTP request failed: {e}", response_snippet=str(e)) from e

        elapsed = time.perf_counter() - t0
        body = resp.content
        snippet = body[:4000].decode("utf-8", errors="replace") if body else ""

        if resp.status_code >= 400:
            logger.error(
                "MinerU HTTP error doc_name=%s status=%s body[:4000]=%s",
                doc_name,
                resp.status_code,
                snippet,
            )
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
            md = read_markdown_from_disk(
                io_base,
                task_id,
                output_subdir=self._cfg.disk_fallback_subdir,
            )

        if not md or not md.strip():
            disk_hint = io_base / self._cfg.disk_fallback_subdir / task_id if task_id else None
            logger.error(
                "MinerU empty markdown doc_name=%s status=%s ct=%s snippet=%s task_id=%s disk_tried=%s",
                doc_name,
                resp.status_code,
                ct,
                snippet,
                task_id or "(no header)",
                disk_hint,
            )
            raise MinerUParseError(
                "MinerU returned no markdown content",
                status_code=resp.status_code,
                response_snippet=snippet,
                output_dir_hint=str(disk_hint) if disk_hint else None,
            )

        meta = {
            "mineru_job_id": task_id or None,
            "mineru_http_status": resp.status_code,
            "mineru_parse_wall_s": round(elapsed, 3),
            "mineru_disk_fallback_subdir": self._cfg.disk_fallback_subdir,
        }
        logger.info(
            "MinerU file_parse ok doc_name=%s chars=%s wall_s=%s",
            doc_name,
            len(md),
            meta["mineru_parse_wall_s"],
        )
        return md.strip(), meta
