"""看图诊断 scope HITL：resume 换图与 URL 归一化。"""

from __future__ import annotations

from typing import Any


def normalize_image_url_list(image_urls: Any) -> tuple[str, ...]:
    if not isinstance(image_urls, list):
        return ()
    return tuple(u.strip() for u in image_urls if isinstance(u, str) and u.strip())


def merge_hitl_image_urls_into_request(
    img_diag_request: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """
    将 payload.image_urls 整表替换写入 img_diag_request（若提供且与旧值不同）。
    返回 (更新后的 request, urls_changed)。
    """
    if "image_urls" not in payload:
        return img_diag_request, False
    raw = payload.get("image_urls")
    if not isinstance(raw, list):
        return img_diag_request, False
    normalized = list(normalize_image_url_list(raw))
    old = normalize_image_url_list(img_diag_request.get("image_urls"))
    new = normalize_image_url_list(normalized)
    if new == old:
        return img_diag_request, False
    updated = dict(img_diag_request)
    updated["image_urls"] = normalized
    return updated, True


def validate_hitl_image_urls_for_subtype(
    *,
    img_diag_subtype: str,
    image_urls: list[str],
) -> str | None:
    """缺陷识别换图时至少保留一张；泄爆可空。返回错误说明或 None。"""
    subtype = (img_diag_subtype or "defect_ident").strip()
    if subtype == "defect_ident" and not image_urls:
        return "缺陷识别换图时 image_urls 至少需要一行有效图片 URL"
    return None
