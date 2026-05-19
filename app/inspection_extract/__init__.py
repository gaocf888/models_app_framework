"""检修报告结构化提取：上传校验等横切能力。"""

from app.inspection_extract.legacy_doc_guard import (
    LEGACY_DOC_UPLOAD_MESSAGE,
    LegacyWordDocNotSupportedError,
    assert_upload_not_legacy_doc,
    is_legacy_word_doc,
)

__all__ = [
    "LEGACY_DOC_UPLOAD_MESSAGE",
    "LegacyWordDocNotSupportedError",
    "assert_upload_not_legacy_doc",
    "is_legacy_word_doc",
]
