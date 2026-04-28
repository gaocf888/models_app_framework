from __future__ import annotations

from typing import Iterable, List, Tuple

IMAGE_BLOCK_MARKER = "\n\n[image_urls]\n"
ORIGINAL_IMAGE_BLOCK_MARKER = "\n\n[original_image_urls]\n"
PROCESSED_IMAGE_BLOCK_MARKER = "\n\n[processed_image_urls]\n"


def _normalize_urls(image_urls: Iterable[str]) -> List[str]:
    return [u.strip() for u in image_urls if isinstance(u, str) and u.strip()]


def build_user_message_with_images(
    query: str,
    image_urls: Iterable[str],
    *,
    original_image_urls: Iterable[str] | None = None,
    processed_image_urls: Iterable[str] | None = None,
) -> str:
    """
    将用户 query 与图片链接合并为可展示的会话文本。

    说明：
    - 会话查询接口返回该文本，前端可据此展示图片；
    - LLM 侧会在组装历史时移除 image block，避免历史 token 膨胀。
    """
    # 兼容旧调用：仅传 image_urls 时按 processed 语义存储。
    processed = _normalize_urls(processed_image_urls if processed_image_urls is not None else image_urls)
    original = _normalize_urls(original_image_urls if original_image_urls is not None else [])
    if not original and not processed:
        return query
    blocks: List[str] = [query]
    if original:
        blocks.append(ORIGINAL_IMAGE_BLOCK_MARKER + "\n".join(f"- {u}" for u in original))
    if processed:
        blocks.append(PROCESSED_IMAGE_BLOCK_MARKER + "\n".join(f"- {u}" for u in processed))
    # 若没有 original 且是旧数据调用，补写 legacy marker 便于向后兼容旧解析链路。
    if processed and not original and processed_image_urls is None:
        blocks.append(IMAGE_BLOCK_MARKER + "\n".join(f"- {u}" for u in processed))
    return "".join(blocks)


def strip_image_block_from_history(content: str) -> str:
    """从历史消息中剥离图片链接附加块，防止模型重复读取链接文本。"""
    if not content:
        return content
    indexes = [content.find(IMAGE_BLOCK_MARKER), content.find(ORIGINAL_IMAGE_BLOCK_MARKER), content.find(PROCESSED_IMAGE_BLOCK_MARKER)]
    starts = [i for i in indexes if i >= 0]
    if not starts:
        return content
    return content[: min(starts)].rstrip()


def _extract_block_lines(content: str, marker: str) -> Tuple[str, List[str]]:
    idx = content.find(marker)
    if idx < 0:
        return content, []
    text = content[:idx].rstrip()
    tail = content[idx + len(marker) :]
    urls: List[str] = []
    for line in tail.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("[") and s.endswith("]"):
            break
        if s.startswith("- "):
            s = s[2:].strip()
        if s:
            urls.append(s)
    return text, urls


def split_message_content_and_images(content: str) -> tuple[str, List[str], List[str]]:
    """
    解析会话持久化文本，拆分为「纯文本 content」与「image_urls 列表」。

    兼容性：
    - 历史老数据（无 marker）会返回 (原文, [], [])；
    - 历史旧格式 [image_urls] 会回填到 processed_image_urls；
    - 格式异常行自动忽略，不影响主文本返回。
    """
    if not content:
        return content, [], []

    text = content
    original_urls: List[str] = []
    processed_urls: List[str] = []

    if ORIGINAL_IMAGE_BLOCK_MARKER in content:
        text, original_urls = _extract_block_lines(content, ORIGINAL_IMAGE_BLOCK_MARKER)
    if PROCESSED_IMAGE_BLOCK_MARKER in content:
        text2, processed_urls = _extract_block_lines(content, PROCESSED_IMAGE_BLOCK_MARKER)
        if len(text2) < len(text):
            text = text2
    if not processed_urls and IMAGE_BLOCK_MARKER in content:
        text3, processed_urls = _extract_block_lines(content, IMAGE_BLOCK_MARKER)
        if len(text3) < len(text):
            text = text3

    return text, original_urls, processed_urls

