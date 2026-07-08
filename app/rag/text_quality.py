from __future__ import annotations


def looks_like_binary_text(text: str) -> bool:
    """
    判断文本是否像二进制误当 UTF-8/latin-1 解码后的乱码（如 PDF 字节流）。
    用于摄入校验与检索侧过滤历史脏数据。
    """
    if not text:
        return False
    sample = text[:8192]
    if len(sample) < 16:
        return False
    if "%PDF-" in sample[:2048]:
        return True
    non_printable = 0
    for ch in sample:
        o = ord(ch)
        if o < 32 and ch not in "\n\r\t":
            non_printable += 1
        elif o == 0xFFFD:
            non_printable += 1
    return (non_printable / len(sample)) > 0.12
