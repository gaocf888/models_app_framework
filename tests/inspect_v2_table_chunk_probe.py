#!/usr/bin/env python3
"""
检修报告 DOCX V2 大表切块探针（独立验证脚本，不接入现网 API）。

用法（在项目根目录）：
  python tests/inspect_v2_table_chunk_probe.py
  python tests/inspect_v2_table_chunk_probe.py path/to/report.docx
  python tests/inspect_v2_table_chunk_probe.py --max-chars 6000 --rows-per-window 8

将 docx 放在与本脚本同目录下亦可直接运行（自动匹配 *.docx）。

输出：与现网 inspection_extract_llm_orchestrator._log_parse_chunk_full 相同格式的 INFO 日志。
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
from pathlib import Path

# 保证从项目根可 import app.*
_SCRIPT_DIR = Path(__file__).resolve().parent
_ROOT = _SCRIPT_DIR.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.core.config import get_app_config
from app.inspection_v2.chunk_table_filter import filter_table_work_items
from app.inspection_v2.docx_rich_text import serialize_docx_for_inspection_v2
from app.inspection_v2.processing_units import split_docx_v2_by_processing_units

logger = logging.getLogger("app.services.inspection_extract_llm_orchestrator")


def _table_only_chunks(chunks: list[str]) -> list[str]:
    return [c for c in chunks if "[DOCX_V2_TABLE" in c]


def _log_parse_chunk_full(*, idx: int, total: int, chunk: str, max_log_chars: int = 0) -> None:
    """复现现网 parse_chunk_full 日志格式。"""
    body = chunk or ""
    truncated_note = ""
    if max_log_chars > 0 and len(body) > max_log_chars:
        body = body[:max_log_chars]
        truncated_note = f" (truncated_to_max_chars={max_log_chars})"
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


def _resolve_docx_path(arg: str | None, script_dir: Path) -> Path:
    if arg:
        p = Path(arg)
        if not p.is_file():
            raise FileNotFoundError(f"docx not found: {p}")
        return p.resolve()
    candidates = sorted(script_dir.glob("*.docx"))
    if len(candidates) == 1:
        return candidates[0].resolve()
    if len(candidates) > 1:
        names = ", ".join(x.name for x in candidates)
        raise FileNotFoundError(
            f"multiple docx in {script_dir}: {names}; pass path explicitly"
        )
    raise FileNotFoundError(
        f"no docx in {script_dir}; copy a report.docx here or pass path as argv"
    )


def _summarize_chunks(label: str, chunks: list[str]) -> None:
    table_chunks = _table_only_chunks(chunks)
    logger.info(
        "【切块探针】%s total_chunks=%s table_chunks=%s",
        label,
        len(chunks),
        len(table_chunks),
    )
    for i, c in enumerate(table_chunks, start=1):
        has_sub = " sub=" in c
        logger.info(
            "【切块探针】table_chunk[%s] bytes=%s has_row_submark=%s",
            i,
            len(c),
            has_sub,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="DOCX V2 大表切块探针")
    parser.add_argument(
        "docx",
        nargs="?",
        help="docx 路径；省略则在脚本同目录找唯一 *.docx",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=None,
        help="单块字符上限（默认与 INSPECT_EXTRACT_V2_PARSE_UNIT_MAX_CHARS 一致）",
    )
    parser.add_argument(
        "--rows-per-window",
        type=int,
        default=None,
        help="行窗口：每块最多包含的数据行数（表头行每块复制；默认与配置一致）",
    )
    parser.add_argument(
        "--enable-column-split",
        action="store_true",
        help="启用强信号列切（横排多 hmerge 子表）；默认关闭",
    )
    parser.add_argument(
        "--compare-baseline",
        action="store_true",
        help="同时打印关闭行窗口（atomic 整表）切块数量对比",
    )
    parser.add_argument(
        "--log-max-chars",
        type=int,
        default=0,
        help="日志正文截断长度，0=不截断（与现网 log_parse_chunk_max_chars 一致）",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    script_dir = Path(__file__).resolve().parent
    docx_path = _resolve_docx_path(args.docx, script_dir)

    cfg = get_app_config().inspection_extract
    max_chars = args.max_chars
    if max_chars is None:
        max_chars = max(2000, int(getattr(cfg, "v2_parse_unit_max_chars", 6000)))
    rows_per_window = args.rows_per_window
    if rows_per_window is None:
        rows_per_window = max(1, int(getattr(cfg, "v2_table_data_rows_per_window", 20)))

    fills = set(getattr(cfg, "v2_shading_candidate_fills", []) or [])
    logger.info("【切块探针】serialize docx=%s", docx_path)
    parsed_text = serialize_docx_for_inspection_v2(docx_path, candidate_fills=fills)
    logger.info("【切块探针】serialized_chars=%s", len(parsed_text))

    if args.compare_baseline:
        baseline = split_docx_v2_by_processing_units(
            parsed_text,
            max_chunk_chars=max_chars,
            table_row_window_enabled=False,
        )
        work_baseline = filter_table_work_items(baseline, parse_route="docx_v2")
        _summarize_chunks("baseline(atomic_table)", work_baseline)

    chunks = split_docx_v2_by_processing_units(
        parsed_text,
        max_chunk_chars=max_chars,
        table_row_window_enabled=True,
        table_data_rows_per_window=max(1, rows_per_window),
        table_column_split_enabled=bool(args.enable_column_split),
    )
    work_items = filter_table_work_items(chunks, parse_route="docx_v2")
    _summarize_chunks("row_window_split", work_items)

    total = len(work_items)
    logger.info(
        "【切块探针】begin parse_chunk_full logs (table chunks only) max_chars=%s rows_per_window=%s",
        max_chars,
        rows_per_window,
    )
    for idx, (_, chunk) in enumerate(work_items, start=1):
        _log_parse_chunk_full(
            idx=idx,
            total=total,
            chunk=chunk,
            max_log_chars=int(args.log_max_chars),
        )

    logger.info("【切块探针】done table_chunks=%s", total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
