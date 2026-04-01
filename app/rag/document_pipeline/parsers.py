from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data and data.strip():
            self._parts.append(data.strip())

    def text(self) -> str:
        return "\n".join(self._parts)


class DocumentParser:
    def parse(self, content: str, source_type: str) -> str:
        st = (source_type or "text").lower()
        if st in {"text", "txt"}:
            return content
        if st in {"md", "markdown"}:
            return self._parse_markdown(content)
        if st == "html":
            return self._parse_html(content)
        if st == "pdf":
            return self._parse_pdf(content)
        if st in {"docx", "doc"}:
            return self._parse_docx(content)
        return content

    @staticmethod
    def resolve_local_path(content: str) -> Path | None:
        raw = (content or "").strip()
        if not raw:
            return None
        # 支持 file:// 前缀，或直接传本地绝对路径
        if raw.lower().startswith("file://"):
            raw = raw[7:]
        p = Path(raw)
        if p.exists() and p.is_file():
            return p
        return None

    @staticmethod
    def _parse_markdown(content: str) -> str:
        # 保留标题与正文，去除 code fence 标记符本身
        lines = []
        in_fence = False
        for line in content.splitlines():
            if line.strip().startswith("```"):
                in_fence = not in_fence
                continue
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _parse_html(content: str) -> str:
        parser = _TextExtractor()
        parser.feed(content)
        return parser.text()

    @staticmethod
    def _parse_pdf(content: str) -> str:
        p = DocumentParser.resolve_local_path(content)
        if p is None:
            # 兼容旧行为：上游已传提取后的纯文本
            return content
        try:
            from pypdf import PdfReader  # type: ignore[import-untyped]
        except Exception as e:  # noqa: BLE001
            raise ImportError("Parsing PDF path requires pypdf. Install from requirements.") from e
        reader = PdfReader(str(p))
        parts: list[str] = []
        for page in reader.pages:
            txt = page.extract_text() or ""
            if txt.strip():
                parts.append(txt.strip())
        return "\n\n".join(parts).strip()

    @staticmethod
    def _parse_docx(content: str) -> str:
        p = DocumentParser.resolve_local_path(content)
        if p is None:
            # 兼容旧行为：上游已传提取后的纯文本
            return content
        try:
            import docx  # type: ignore[import-untyped]
        except Exception as e:  # noqa: BLE001
            raise ImportError("Parsing DOCX path requires python-docx. Install from requirements.") from e
        d = docx.Document(str(p))
        lines = [para.text.strip() for para in d.paragraphs if para.text and para.text.strip()]
        return "\n".join(lines).strip()

