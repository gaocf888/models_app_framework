from __future__ import annotations


def strip_utf16_surrogates(text: str | None) -> str:
    """
    去掉 UTF-16 代理项（U+D800–U+DFFF）。

    pypdf 等解析老 PDF 时可能把解不开的码点塞进 Python str；
    这类字符不能 utf-8 encode（surrogates not allowed），会在 chunk_hash / 写索引时炸掉。
    ``errors="replace"`` 对 surrogate 无效，必须先剔除。
    """
    if not text:
        return ""
    if not any("\ud800" <= ch <= "\udfff" for ch in text):
        return text
    return "".join(ch for ch in text if not ("\ud800" <= ch <= "\udfff"))


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
