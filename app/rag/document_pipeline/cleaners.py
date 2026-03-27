from __future__ import annotations

import re


class TextCleaner:
    _multi_newline_re = re.compile(r"\n{3,}")
    _multi_space_re = re.compile(r"[ \t]{2,}")

    _toc_noise_re = re.compile(r"^\s*(目录|contents?)\s*$", re.IGNORECASE | re.MULTILINE)
    _page_mark_re = re.compile(r"^\s*(page|第)\s*\d+\s*(页)?\s*$", re.IGNORECASE | re.MULTILINE)

    _mojibake_map = {
        "â€™": "'",
        "â€œ": '"',
        "â€": '"',
        "â€”": "-",
        "Â ": " ",
        "Â": "",
        "Ã©": "e",
    }

    def __init__(
        self,
        profile: str = "normal",
        remove_header_footer: bool = True,
        merge_duplicate_paragraphs: bool = True,
        fix_encoding_noise: bool = True,
        min_repeated_line_pages: int = 2,
    ) -> None:
        self._profile = (profile or "normal").lower()
        self._remove_header_footer = remove_header_footer
        self._merge_duplicate_paragraphs = merge_duplicate_paragraphs
        self._fix_encoding_noise = fix_encoding_noise
        self._min_repeated_line_pages = max(2, int(min_repeated_line_pages))

    def clean(self, content: str) -> str:
        text = (content or "").replace("\r\n", "\n").replace("\r", "\n")
        if self._fix_encoding_noise:
            text = self._repair_encoding_noise(text)
        if self._profile in {"normal", "strict"}:
            text = self._toc_noise_re.sub("", text)
            text = self._page_mark_re.sub("", text)
            text = re.sub(r"^\s*[\.\-_]{2,}\s*$", "", text, flags=re.MULTILINE)
            text = re.sub(r"^\s*.+\.{3,}\s*\d+\s*$", "", text, flags=re.MULTILINE)
        if self._remove_header_footer:
            text = self._remove_repeated_headers_footers(text)
        if self._merge_duplicate_paragraphs:
            text = self._merge_repeated_paragraphs(text)
        text = self._multi_newline_re.sub("\n\n", text)
        text = self._multi_space_re.sub(" ", text)
        if self._profile == "strict":
            # 严格档位：进一步压缩孤立符号行
            text = re.sub(r"^[\-\_=*#]{3,}\s*$", "", text, flags=re.MULTILINE)
        return text.strip()

    def _repair_encoding_noise(self, text: str) -> str:
        fixed = text
        for bad, good in self._mojibake_map.items():
            fixed = fixed.replace(bad, good)
        return fixed.replace("\ufffd", "")

    def _remove_repeated_headers_footers(self, text: str) -> str:
        pages = [p for p in text.split("\f") if p.strip()]
        if len(pages) < self._min_repeated_line_pages:
            return text
        first_counts: dict[str, int] = {}
        last_counts: dict[str, int] = {}
        per_page_lines: list[list[str]] = []
        for page in pages:
            lines = [ln.strip() for ln in page.split("\n") if ln.strip()]
            per_page_lines.append(lines)
            if lines:
                first = lines[0]
                last = lines[-1]
                if len(first) <= 60:
                    first_counts[first] = first_counts.get(first, 0) + 1
                if len(last) <= 60:
                    last_counts[last] = last_counts.get(last, 0) + 1
        header_candidates = {k for k, v in first_counts.items() if v >= self._min_repeated_line_pages}
        footer_candidates = {k for k, v in last_counts.items() if v >= self._min_repeated_line_pages}
        if not header_candidates and not footer_candidates:
            return text
        rebuilt: list[str] = []
        for lines in per_page_lines:
            tmp = list(lines)
            if tmp and tmp[0] in header_candidates:
                tmp = tmp[1:]
            if tmp and tmp[-1] in footer_candidates:
                tmp = tmp[:-1]
            rebuilt.append("\n".join(tmp))
        return "\n\n".join(x for x in rebuilt if x.strip())

    @staticmethod
    def _merge_repeated_paragraphs(text: str) -> str:
        parts = [p.strip() for p in text.split("\n\n") if p.strip()]
        out: list[str] = []
        last_norm = ""
        for p in parts:
            norm = " ".join(p.split()).lower()
            if norm == last_norm:
                continue
            out.append(p)
            last_norm = norm
        return "\n\n".join(out)

