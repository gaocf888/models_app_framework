"""
将 Word 文档转为 PDF 字节流，供 /v1/layout-ocr 与 pdf2image 管线衔接。

依赖宿主机/镜像内 PATH 中的 LibreOffice（soffice 或 libreoffice）。
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


def docx_bytes_to_pdf_bytes(body: bytes, original_name: str, *, timeout_s: int = 180) -> bytes:
    """
    :param body: .doc / .docx 原始字节
    :param original_name: 原始文件名（用于后缀与 LibreOffice 类型推断）
    :raises RuntimeError: 未找到 LibreOffice 或转换失败
    """
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:
        raise RuntimeError("LibreOffice (soffice/libreoffice) not found in PATH; install libreoffice-writer-nogui or equivalent")
    suffix = Path(original_name or "source.docx").suffix.lower()
    if suffix not in {".doc", ".docx"}:
        suffix = ".docx"
    with tempfile.TemporaryDirectory(prefix="paddle_layout_lo_") as d:
        dpath = Path(d)
        src = dpath / ("source" + suffix)
        src.write_bytes(body)
        cmd = [
            exe,
            "--headless",
            "--nologo",
            "--nofirststartwizard",
            "--nodefault",
            "--nolockcheck",
            "--convert-to",
            "pdf",
            "--outdir",
            str(dpath),
            str(src),
        ]
        proc = subprocess.run(cmd, timeout=timeout_s, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "")[:2000]
            raise RuntimeError(f"LibreOffice failed (exit {proc.returncode}): {err}")
        pdf_path = src.with_suffix(".pdf")
        if not pdf_path.is_file():
            raise RuntimeError("LibreOffice produced no PDF output")
        return pdf_path.read_bytes()
