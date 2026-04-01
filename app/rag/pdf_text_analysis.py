from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class PdfTextStats:
    """用于路由决策与日志。"""

    page_count: int
    sampled_pages: int
    total_chars_sampled: int
    avg_chars_per_sampled_page: float


def analyze_pdf_text_layer(
    pdf_path: Path,
    *,
    max_sample_pages: int = 12,
    pdf_reader_cls: type | None = None,
) -> PdfTextStats:
    """
    用 pypdf 快速抽样估算「可选中文字层」密度。
    不用于精确 OCR，仅区分文字版 PDF vs 扫描/图片为主 PDF。

    pdf_reader_cls：可注入 ``PdfReader``，便于单测；默认 ``from pypdf import PdfReader``。
    """
    Reader = pdf_reader_cls
    if Reader is None:
        try:
            from pypdf import PdfReader as Reader  # type: ignore[import-untyped]
        except Exception as e:  # noqa: BLE001
            raise ImportError("pdf_text_analysis requires pypdf") from e

    reader = Reader(str(pdf_path))
    n = len(reader.pages)
    if n <= 0:
        return PdfTextStats(page_count=0, sampled_pages=0, total_chars_sampled=0, avg_chars_per_sampled_page=0.0)

    # 均匀抽样 + 始终包含首页，避免只抽到空白尾页
    if n <= max_sample_pages:
        indices = list(range(n))
    else:
        step = max(1, n // max_sample_pages)
        # range(0, n, step) 已含第 0 页
        indices = sorted(set(list(range(0, n, step))[:max_sample_pages]))[:max_sample_pages]

    total = 0
    for i in indices:
        try:
            txt = reader.pages[i].extract_text() or ""
        except Exception:  # noqa: BLE001
            txt = ""
        total += len(txt.replace("\n", "").replace("\r", "").strip())

    sampled = len(indices)
    avg = float(total) / float(sampled) if sampled else 0.0
    return PdfTextStats(
        page_count=n,
        sampled_pages=sampled,
        total_chars_sampled=total,
        avg_chars_per_sampled_page=avg,
    )


def is_likely_scanned_pdf(
    pdf_path: Path,
    *,
    max_avg_chars_for_text_pdf: float,
    pdf_reader_cls: type | None = None,
) -> tuple[bool, PdfTextStats]:
    """
    若抽样页平均可提取字符数 < max_avg_chars_for_text_pdf，则视为扫描/图片 PDF。
    """
    stats = analyze_pdf_text_layer(pdf_path, pdf_reader_cls=pdf_reader_cls)
    if stats.page_count == 0:
        return True, stats
    scanned = stats.avg_chars_per_sampled_page < max_avg_chars_for_text_pdf
    return scanned, stats
