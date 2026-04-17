from __future__ import annotations

from typing import Iterable, List

IMAGE_BLOCK_MARKER = "\n\n[image_urls]\n"


def build_user_message_with_images(query: str, image_urls: Iterable[str]) -> str:
    """
    将用户 query 与图片链接合并为可展示的会话文本。

    说明：
    - 会话查询接口返回该文本，前端可据此展示图片；
    - LLM 侧会在组装历史时移除 image block，避免历史 token 膨胀。
    """
    urls: List[str] = [u.strip() for u in image_urls if isinstance(u, str) and u.strip()]
    if not urls:
        return query
    lines = "\n".join(f"- {u}" for u in urls)
    return f"{query}{IMAGE_BLOCK_MARKER}{lines}"


def strip_image_block_from_history(content: str) -> str:
    """从历史消息中剥离图片链接附加块，防止模型重复读取链接文本。"""
    if not content:
        return content
    idx = content.find(IMAGE_BLOCK_MARKER)
    if idx < 0:
        return content
    return content[:idx].rstrip()

