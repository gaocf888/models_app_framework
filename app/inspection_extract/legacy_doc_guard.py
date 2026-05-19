"""
检修报告上传：拒绝老式 Word 二进制 .doc（python-docx 仅支持 .docx）。
"""

from __future__ import annotations

from pathlib import Path

# OLE 复合文档魔数（.doc / 部分旧 Office 格式）；.docx 为 ZIP，以 PK 开头
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP_MAGIC = b"PK"

LEGACY_DOC_UPLOAD_MESSAGE = (
    "不支持 Word 97–2003 的 .doc 格式。请使用 Word 或 WPS 打开该文件，"
    "选择「另存为」并保存为 .docx 后再上传。"
)


class LegacyWordDocNotSupportedError(Exception):
    """上传或解析阶段发现老式 .doc 二进制文件。"""

    def __init__(self, *, file_name: str = "") -> None:
        self.file_name = file_name
        super().__init__(LEGACY_DOC_UPLOAD_MESSAGE)


def is_legacy_word_doc(*, file_name: str, content: bytes) -> bool:
    """判断是否为老式 .doc（扩展名或文件头为 OLE，且非 OOXML zip）。"""
    if not content:
        return False
    suffix = Path(file_name or "").suffix.lower()
    if suffix == ".doc":
        return True
    head = content[:8]
    if not head.startswith(_OLE_MAGIC):
        return False
    if content[:2] == _ZIP_MAGIC:
        return False
    return True


def assert_upload_not_legacy_doc(*, file_name: str, content: bytes) -> None:
    if is_legacy_word_doc(file_name=file_name, content=content):
        raise LegacyWordDocNotSupportedError(file_name=file_name or "")
