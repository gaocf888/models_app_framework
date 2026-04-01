from __future__ import annotations

import io
import json
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

import httpx

from app.core.config import MinerUConfig
from app.core.logging import get_logger
from app.rag.mineru_errors import MinerUParseError

logger = get_logger(__name__)


# 仅从这些键取字符串，避免把 FastAPI 的 detail / error 长文本误当 Markdown
_MD_JSON_KEYS = frozenset({"markdown", "md", "result_md", "text", "content", "result", "body"})


def _extract_markdown_from_json(obj: Any) -> str | None:
    if obj is None:
        return None
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _MD_JSON_KEYS and isinstance(v, str) and v.strip():
                return v.strip()
        for k, v in obj.items():
            if k in _MD_JSON_KEYS and isinstance(v, (dict, list)):
                found = _extract_markdown_from_json(v)
                if found:
                    return found
        for _k, v in obj.items():
            if isinstance(v, (dict, list)):
                found = _extract_markdown_from_json(v)
                if found:
                    return found
    if isinstance(obj, list):
        for item in obj:
            found = _extract_markdown_from_json(item)
            if found:
                return found
    return None


def _markdown_from_zip_bytes(data: bytes) -> str | None:
    if not data.startswith(b"PK\x03\x04"):
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".md")]
            names.sort()
            for name in names:
                raw = zf.read(name)
                try:
                    return raw.decode("utf-8")
                except UnicodeDecodeError:
                    return raw.decode("utf-8", errors="replace")
    except zipfile.BadZipFile:
        return None
    return None


def _read_markdown_from_disk(io_base: Path, job_id: str) -> str | None:
    root = io_base / "mineru_jobs" / job_id
    if not root.exists():
        return None
    md_files = sorted(root.rglob("*.md"))
    if not md_files:
        return None
    parts: list[str] = []
    for p in md_files:
        try:
            parts.append(p.read_text(encoding="utf-8"))
        except OSError as e:
            logger.warning("mineru read md file failed: %s %s", p, e)
    return "\n\n".join(parts).strip() if parts else None


class MinerUClient:
    """同步调用 mineru-api `POST /file_parse`（官方文档：同步等待结果）。"""

    def __init__(self, cfg: MinerUConfig) -> None:
        self._cfg = cfg

    def parse_pdf_to_markdown(self, pdf_path: Path, *, doc_name: str) -> tuple[str, dict[str, Any]]:
        """
        上传本地 PDF，返回 (markdown, meta)。

        meta 含 job_id、http_status、elapsed 等，供 metrics。
        """
        job_id = str(uuid.uuid4())
        # MinerU 容器内挂载 /io；与 models-app 的 io_path 共享同一宿主目录
        output_dir_container = f"/io/mineru_jobs/{job_id}"
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

        data = {
            "return_md": "true",
            "return_middle_json": "false",
            "backend": self._cfg.backend,
            "parse_method": self._cfg.parse_method,
            "lang_list": self._cfg.language,
            "formula_enable": "true",
            "table_enable": "true",
            "output_dir": output_dir_container,
        }

        logger.info(
            "MinerU file_parse start doc_name=%s url=%s output_dir=%s backend=%s parse_method=%s",
            doc_name,
            url,
            output_dir_container,
            self._cfg.backend,
            self._cfg.parse_method,
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
                "MinerU HTTP error doc_name=%s status=%s body[:4000]=%s output_dir=%s",
                doc_name,
                resp.status_code,
                snippet,
                output_dir_container,
            )
            raise MinerUParseError(
                f"MinerU HTTP {resp.status_code} for doc={doc_name}",
                status_code=resp.status_code,
                response_snippet=snippet,
                output_dir_hint=output_dir_container,
            )

        md: str | None = None
        ct = (resp.headers.get("content-type") or "").lower()
        if "application/json" in ct or body[:1] in (b"{", b"["):
            try:
                payload = resp.json()
            except json.JSONDecodeError:
                payload = None
            if payload is not None:
                md = _extract_markdown_from_json(payload)

        if not md:
            md = _markdown_from_zip_bytes(body)

        io_base = Path(self._cfg.io_path).expanduser()
        if not md:
            md = _read_markdown_from_disk(io_base, job_id)

        if not md or not md.strip():
            logger.error(
                "MinerU empty markdown doc_name=%s status=%s ct=%s snippet=%s disk_tried=%s",
                doc_name,
                resp.status_code,
                ct,
                snippet,
                io_base / "mineru_jobs" / job_id,
            )
            raise MinerUParseError(
                "MinerU returned no markdown content",
                status_code=resp.status_code,
                response_snippet=snippet,
                output_dir_hint=output_dir_container,
            )

        meta = {
            "mineru_job_id": job_id,
            "mineru_http_status": resp.status_code,
            "mineru_parse_wall_s": round(elapsed, 3),
            "mineru_output_dir_container": output_dir_container,
        }
        logger.info(
            "MinerU file_parse ok doc_name=%s chars=%s wall_s=%s",
            doc_name,
            len(md),
            meta["mineru_parse_wall_s"],
        )
        return md.strip(), meta
