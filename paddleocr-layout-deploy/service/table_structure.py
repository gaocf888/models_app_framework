"""
PP-Structure 表格识别：与行级 PaddleOCR 并行，产出 `tables`（html + 行列矩阵）。

首请求会触发版面/表格权重下载（与 PaddleOCR whl 内建逻辑一致），可能较慢。
"""

from __future__ import annotations

import html as html_module
import logging
import re
import threading
import time
from typing import Any

import numpy as np
from PIL import Image

logger = logging.getLogger("paddle_layout_api")

_pp_lock = threading.Lock()
_pp_engine: Any = None
_pp_init_failed = False


def _pil_to_bgr(img: Image.Image) -> np.ndarray:
    import cv2  # type: ignore[import-untyped]

    rgb = np.array(img.convert("RGB"), dtype=np.uint8)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _strip_tags(fragment: str) -> str:
    s = re.sub(r"<[^>]+>", " ", fragment)
    return " ".join(s.split())


def html_table_to_rows(html: str | None) -> list[list[str]]:
    """从 PP-Structure 输出的 table HTML 中抽取二维文本矩阵（含 th/td）。"""
    if not html or not isinstance(html, str):
        return []
    rows: list[list[str]] = []
    for tr_m in re.finditer(r"<tr[^>]*>(.*?)</tr>", html, flags=re.I | re.S):
        inner = tr_m.group(1)
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", inner, flags=re.I | re.S)
        row = [html_module.unescape(_strip_tags(c)).strip() for c in cells]
        if any(x for x in row):
            rows.append(row)
    return rows


def _bbox_to_dict(bbox: Any) -> dict[str, float] | None:
    if bbox is None:
        return None
    try:
        if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
            x1, y1, x2, y2 = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
            return {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
    except (TypeError, ValueError):
        return None
    return None


def _cell_bbox_jsonable(cell_bbox: Any) -> list[Any]:
    if cell_bbox is None:
        return []
    try:
        if hasattr(cell_bbox, "tolist"):
            return cell_bbox.tolist()  # type: ignore[no-any-return]
        if isinstance(cell_bbox, np.ndarray):
            return cell_bbox.tolist()
    except Exception:  # noqa: BLE001
        pass
    if isinstance(cell_bbox, list):
        return cell_bbox
    return []


def _ensure_pp_structure(*, use_gpu: bool) -> Any | None:
    global _pp_engine, _pp_init_failed
    if _pp_init_failed:
        return None
    with _pp_lock:
        if _pp_engine is not None:
            return _pp_engine
        try:
            from paddleocr import PPStructure  # type: ignore[import-untyped]

            logger.info("initializing PPStructure (layout+table, lazy; first call may download weights)")
            _pp_engine = PPStructure(
                show_log=False,
                use_gpu=use_gpu,
                lang="ch",
                layout=True,
                table=True,
                ocr=True,
            )
            return _pp_engine
        except Exception:  # noqa: BLE001
            logger.exception("PPStructure initialization failed; tables will be empty")
            _pp_init_failed = True
            return None


def extract_tables_from_page(img: Image.Image, *, page_no: int, use_gpu: bool) -> tuple[list[dict[str, Any]], int]:
    """
    对单页图像运行 PP-Structure，收集 type==table 的区域。

    :return: (tables_json, elapsed_ms)
    """
    engine = _ensure_pp_structure(use_gpu=use_gpu)
    if engine is None:
        return [], 0
    bgr = _pil_to_bgr(img)
    t0 = time.perf_counter()
    try:
        regions = engine(bgr, img_idx=page_no - 1)
    except Exception:  # noqa: BLE001
        logger.exception("PPStructure inference failed page=%s", page_no)
        return [], int((time.perf_counter() - t0) * 1000)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    if not isinstance(regions, list):
        return [], elapsed_ms

    out: list[dict[str, Any]] = []
    ti = 0
    for r in regions:
        if not isinstance(r, dict):
            continue
        if str(r.get("type") or "").lower() != "table":
            continue
        res = r.get("res")
        if not isinstance(res, dict):
            continue
        html = res.get("html")
        html_s = html if isinstance(html, str) else ""
        rows = html_table_to_rows(html_s)
        bbox = _bbox_to_dict(r.get("bbox"))
        tid = f"p{page_no}-t{ti}"
        ti += 1
        out.append(
            {
                "table_id": tid,
                "page_no": page_no,
                "bbox": bbox,
                "html": html_s,
                "rows": rows,
                "n_rows": len(rows),
                "n_cols": max((len(rw) for rw in rows), default=0),
                "cell_bbox": _cell_bbox_jsonable(res.get("cell_bbox")),
            }
        )
    return out, elapsed_ms
