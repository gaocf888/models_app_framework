from __future__ import annotations


class MinerUParseError(RuntimeError):
    """MinerU /file_parse 失败；携带 HTTP 状态与响应片段供日志与任务记录。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_snippet: str | None = None,
        output_dir_hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_snippet = response_snippet
        self.output_dir_hint = output_dir_hint
