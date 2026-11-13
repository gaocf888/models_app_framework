"""
知识引用预览：PDF 原件模式（PDF.js 文本搜索高亮）。

一期策略：可访问的 PDF URL + text_preview/chunk 正文做近似定位；
失败时回退到解析文本预览。不依赖 page/bbox 元数据。
"""

from __future__ import annotations

import html
import json
import re
from typing import Any
from urllib.parse import urlparse


def looks_like_pdf_url(url: str | None) -> bool:
    """根据 URL 路径/查询粗判是否为 PDF 原件。"""
    raw = (url or "").strip()
    if not raw:
        return False
    low = raw.lower()
    if low.startswith("data:application/pdf"):
        return True
    try:
        path = urlparse(raw).path.lower()
    except Exception:  # noqa: BLE001
        path = low
    if path.endswith(".pdf"):
        return True
    # 部分对象存储用 query 带 response-content-type / filename
    if ".pdf?" in low or "filename=" in low and ".pdf" in low:
        return True
    if "application/pdf" in low:
        return True
    return False


def looks_like_pdf_meta(meta: Any) -> bool:
    if not isinstance(meta, dict):
        return False
    for key in ("content_fetch_content_type", "content_type", "source_type", "mime_type"):
        v = str(meta.get(key) or "").strip().lower()
        if "pdf" in v:
            return True
    return False


def build_pdf_search_queries(*texts: str | None) -> list[str]:
    """
    为 PDF 文本层搜索生成由长到短的候选串。
    PDF 文本层常与 Markdown 解析结果不完全一致，多候选可提高命中率。
    """
    out: list[str] = []
    seen: set[str] = set()

    def _add(s: str) -> None:
        t = re.sub(r"\s+", " ", (s or "")).strip()
        if len(t) < 4:
            return
        # 控制长度，避免 find 过慢
        if len(t) > 80:
            t = t[:80]
        key = re.sub(r"\s+", "", t)
        if key in seen:
            return
        seen.add(key)
        out.append(t)

    for raw in texts:
        if not (raw or "").strip():
            continue
        text = str(raw).strip()
        # 去 Markdown 标题符
        cleaned_lines: list[str] = []
        for line in text.splitlines():
            line = re.sub(r"^#{1,6}\s*", "", line.strip())
            if line:
                cleaned_lines.append(line)
        cleaned = " ".join(cleaned_lines) if cleaned_lines else text
        _add(cleaned)
        if cleaned_lines:
            _add(cleaned_lines[0])
        # FAQ 题干
        m = re.search(r"(\d{1,4}\.\s*[^?\n？]{6,48}[?？]?)", cleaned)
        if m:
            _add(m.group(1))
        # 再缩短一档
        compact = re.sub(r"\s+", "", cleaned)
        if len(compact) >= 12:
            _add(compact[:24])
        if len(compact) >= 20:
            _add(compact[:40])

    return out


def render_pdf_viewer_html(
    *,
    title: str,
    meta_line: str,
    pdf_url: str,
    proxy_path: str,
    search_queries: list[str],
    text_fallback_href: str,
    original_open_url: str,
    notice: str | None = None,
) -> str:
    """生成 PDF.js 原件预览页（内嵌文本搜索高亮脚本）。"""
    title_e = html.escape(title)
    meta_e = meta_line
    pdf_url_json = json.dumps(pdf_url, ensure_ascii=False)
    proxy_json = json.dumps(proxy_path, ensure_ascii=False)
    queries_json = json.dumps(search_queries, ensure_ascii=False)
    fallback_e = html.escape(text_fallback_href, quote=True)
    orig_e = html.escape(original_open_url, quote=True)
    notice_html = (
        f'<p class="notice" id="page-notice">{html.escape(notice)}</p>' if notice else ""
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>知识引用预览 · {title_e}</title>
  <style>
    :root {{
      --bg: #e8eef2;
      --paper: #ffffff;
      --ink: #1c1917;
      --muted: #64748b;
      --line: #d6dee6;
      --hit: rgba(250, 204, 21, 0.45);
      --accent: #0f766e;
      --warn: #92400e;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      background: linear-gradient(180deg, #f4f7fa 0%, var(--bg) 40%);
      color: var(--ink);
    }}
    .top {{
      position: sticky; top: 0; z-index: 20;
      backdrop-filter: blur(8px);
      background: rgba(255,255,255,0.94);
      border-bottom: 1px solid var(--line);
      padding: 0.7rem 1rem;
      display: flex; gap: 0.75rem; flex-wrap: wrap; align-items: center;
      justify-content: space-between;
    }}
    .top h1 {{ margin: 0; font-size: 1.05rem; font-weight: 650; }}
    .meta {{ color: var(--muted); font-size: 0.8rem; }}
    .btn {{
      display: inline-block; text-decoration: none; cursor: pointer; border: none;
      background: var(--accent); color: #fff;
      padding: 0.4rem 0.75rem; border-radius: 6px; font-size: 0.85rem;
    }}
    .btn.ghost {{ background: transparent; color: var(--accent); border: 1px solid var(--accent); }}
    .btn.secondary {{ background: #334155; }}
    main {{ max-width: 54rem; margin: 0.85rem auto 2.5rem; padding: 0 0.75rem; }}
    .notice {{
      font-size: 0.85rem; color: var(--warn);
      background: #fffbeb; border: 1px solid #fde68a;
      padding: 0.55rem 0.75rem; border-radius: 8px; margin: 0 0 0.75rem;
    }}
    .status {{
      font-size: 0.82rem; color: var(--muted); margin: 0 0 0.65rem;
    }}
    #viewer {{
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 0.75rem;
      box-shadow: 0 8px 24px rgba(15, 23, 42, 0.06);
      min-height: 70vh;
    }}
    .page-wrap {{
      position: relative;
      margin: 0 auto 1rem;
      width: fit-content;
      max-width: 100%;
    }}
    .page-wrap canvas {{
      display: block;
      max-width: 100%;
      height: auto;
      box-shadow: 0 1px 4px rgba(15,23,42,0.12);
    }}
    .hl-layer {{
      position: absolute; left: 0; top: 0; right: 0; bottom: 0;
      pointer-events: none;
    }}
    .hl-box {{
      position: absolute;
      background: var(--hit);
      border-radius: 2px;
      outline: 1px solid rgba(217, 119, 6, 0.55);
    }}
    .foot {{
      margin-top: 0.75rem; color: var(--muted); font-size: 0.78rem;
    }}
  </style>
</head>
<body>
  <div class="top">
    <div>
      <h1>{title_e}</h1>
      <div class="meta">{meta_e}</div>
    </div>
    <div style="display:flex;gap:0.5rem;flex-wrap:wrap;">
      <a class="btn" href="{orig_e}" target="_blank" rel="noopener noreferrer">打开原文档</a>
      <button type="button" class="btn secondary" id="btn-jump">回到引用位置</button>
      <a class="btn ghost" href="{fallback_e}">解析文本预览</a>
    </div>
  </div>
  <main>
    {notice_html}
    <p class="status" id="status">正在加载 PDF 原件…</p>
    <div id="viewer"></div>
    <p class="foot">PDF 原件预览：按知识片段正文在 PDF 文本层中近似搜索高亮；扫描件或无文本层时请改用「解析文本预览」。</p>
  </main>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js"></script>
  <script>
(function () {{
  var PDF_URL = {pdf_url_json};
  var PROXY_PATH = {proxy_json};
  var QUERIES = {queries_json};
  var statusEl = document.getElementById("status");
  var viewer = document.getElementById("viewer");
  var hitAnchor = null;

  function setStatus(msg) {{
    if (statusEl) statusEl.textContent = msg || "";
  }}

  function apiKeyFromPage() {{
    try {{
      return new URLSearchParams(window.location.search).get("api_key") || "";
    }} catch (e) {{
      return "";
    }}
  }}

  function isStableObjectRef(u) {{
    if (typeof u !== "string") return false;
    return (
      u.indexOf("minio://") === 0 ||
      u.indexOf("local:") === 0 ||
      u.indexOf("rag-docs/") === 0
    );
  }}

  function buildFetchUrl(directUrl) {{
    var key = apiKeyFromPage();
    var u;
    // MinIO/本地稳定引用：走 pdf-proxy?ref=（preview_router 鉴权）
    if (isStableObjectRef(directUrl)) {{
      u = PROXY_PATH + "?ref=" + encodeURIComponent(directUrl);
      if (key) u += "&api_key=" + encodeURIComponent(key);
      return u;
    }}
    // 同源相对路径（如 /chatbot/documents/original/preview?ref=…）直接带 api_key
    var isRelative = typeof directUrl === "string" && directUrl.charAt(0) === "/";
    if (isRelative) {{
      u = directUrl;
      if (key) {{
        u += (u.indexOf("?") >= 0 ? "&" : "?") + "api_key=" + encodeURIComponent(key);
      }}
      return u;
    }}
    u = PROXY_PATH + "?url=" + encodeURIComponent(directUrl);
    if (key) u += "&api_key=" + encodeURIComponent(key);
    return u;
  }}

  function normalize(s) {{
    return String(s || "").replace(/\\s+/g, "");
  }}

  function findNeedle(hayNorm, queries) {{
    for (var i = 0; i < queries.length; i++) {{
      var n = normalize(queries[i]);
      if (n.length < 4) continue;
      var idx = hayNorm.indexOf(n);
      if (idx >= 0) return {{ start: idx, end: idx + n.length, query: queries[i] }};
      // 再试短一点
      if (n.length > 16) {{
        var shortN = n.slice(0, 16);
        idx = hayNorm.indexOf(shortN);
        if (idx >= 0) return {{ start: idx, end: idx + shortN.length, query: queries[i] }};
      }}
    }}
    return null;
  }}

  function mapNormRangeToItems(items) {{
    // 返回每个字符在规范化串中的起止 → item 下标
    var map = [];
    for (var i = 0; i < items.length; i++) {{
      var raw = String(items[i].str || "");
      var norm = normalize(raw);
      for (var k = 0; k < norm.length; k++) map.push(i);
    }}
    return map;
  }}

  function itemRect(item, viewport) {{
    // pdf.js text item transform: [a,b,c,d,e,f]
    var t = pdfjsLib.Util.transform(viewport.transform, item.transform);
    var fontHeight = Math.sqrt(t[2] * t[2] + t[3] * t[3]);
    var width = (item.width || 0) * viewport.scale;
    // 部分条目 width 为 0，按字数估宽
    if (!width && item.str) width = fontHeight * 0.6 * String(item.str).length;
    var height = fontHeight;
    var x = t[4];
    var y = t[5] - height * 0.8;
    return {{ left: x, top: y, width: Math.max(width, 4), height: Math.max(height, 8) }};
  }}

  async function renderPage(page, pageNum, highlightItemIdxs) {{
    var scale = 1.25;
    var viewport = page.getViewport({{ scale: scale }});
    var wrap = document.createElement("div");
    wrap.className = "page-wrap";
    wrap.dataset.page = String(pageNum);
    var canvas = document.createElement("canvas");
    var ctx = canvas.getContext("2d");
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    wrap.appendChild(canvas);
    var hl = document.createElement("div");
    hl.className = "hl-layer";
    wrap.appendChild(hl);
    viewer.appendChild(wrap);

    await page.render({{ canvasContext: ctx, viewport: viewport }}).promise;

    if (highlightItemIdxs && highlightItemIdxs.length) {{
      var tc = await page.getTextContent();
      var marked = {{}};
      for (var i = 0; i < highlightItemIdxs.length; i++) {{
        var ii = highlightItemIdxs[i];
        if (marked[ii]) continue;
        marked[ii] = true;
        var item = tc.items[ii];
        if (!item) continue;
        var r = itemRect(item, viewport);
        var box = document.createElement("div");
        box.className = "hl-box";
        box.style.left = r.left + "px";
        box.style.top = r.top + "px";
        box.style.width = r.width + "px";
        box.style.height = r.height + "px";
        hl.appendChild(box);
        if (!hitAnchor) hitAnchor = box;
      }}
    }}
    return wrap;
  }}

  async function loadPdf() {{
    if (!window.pdfjsLib) {{
      setStatus("无法加载 PDF.js（网络或 CDN 不可用）。请改用「解析文本预览」。");
      return;
    }}
    pdfjsLib.GlobalWorkerOptions.workerSrc =
      "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";

    var loadingTask = pdfjsLib.getDocument({{
      url: buildFetchUrl(PDF_URL),
      withCredentials: false,
    }});

    var pdf;
    try {{
      pdf = await loadingTask.promise;
    }} catch (err) {{
      console.error(err);
      setStatus("PDF 加载失败（可能是地址不可达或鉴权/CORS）。请改用「解析文本预览」或「打开原文档」。");
      return;
    }}

    setStatus("正在定位知识片段（共 " + pdf.numPages + " 页）…");

    var hitPage = -1;
    var hitItems = null;
    var usedQuery = "";

    if (QUERIES && QUERIES.length) {{
      for (var p = 1; p <= pdf.numPages; p++) {{
        var page = await pdf.getPage(p);
        var tc = await page.getTextContent();
        var items = tc.items || [];
        var hay = "";
        for (var i = 0; i < items.length; i++) hay += String(items[i].str || "");
        var hayNorm = normalize(hay);
        var found = findNeedle(hayNorm, QUERIES);
        if (found) {{
          var map = mapNormRangeToItems(items);
          var idxs = [];
          for (var c = found.start; c < found.end && c < map.length; c++) {{
            idxs.push(map[c]);
          }}
          // 去重保序
          var uniq = [];
          var seen = {{}};
          for (var j = 0; j < idxs.length; j++) {{
            if (!seen[idxs[j]]) {{ seen[idxs[j]] = 1; uniq.push(idxs[j]); }}
          }}
          hitPage = p;
          hitItems = uniq;
          usedQuery = found.query;
          break;
        }}
      }}
    }}

    // 渲染策略：命中页前后各 1 页优先；否则前 8 页，避免超大 PDF 一次拖垮浏览器
    var pagesToRender = [];
    if (hitPage > 0) {{
      var lo = Math.max(1, hitPage - 1);
      var hi = Math.min(pdf.numPages, hitPage + 1);
      for (var x = lo; x <= hi; x++) pagesToRender.push(x);
      // 附带首页方便上下文（若不同）
      if (pagesToRender.indexOf(1) < 0 && pdf.numPages > 3) {{
        /* skip cover to keep focus */
      }}
    }} else {{
      var cap = Math.min(pdf.numPages, 12);
      for (var y = 1; y <= cap; y++) pagesToRender.push(y);
    }}

    viewer.innerHTML = "";
    hitAnchor = null;
    for (var ri = 0; ri < pagesToRender.length; ri++) {{
      var pn = pagesToRender[ri];
      var pg = await pdf.getPage(pn);
      var hlIdx = (pn === hitPage) ? hitItems : null;
      await renderPage(pg, pn, hlIdx);
    }}

    if (hitPage > 0) {{
      setStatus("已定位到第 " + hitPage + " 页" + (usedQuery ? "（匹配：「" + usedQuery.slice(0, 24) + "」）" : "") + "。");
      setTimeout(function () {{
        if (hitAnchor) hitAnchor.scrollIntoView({{ behavior: "smooth", block: "center" }});
      }}, 80);
    }} else if (QUERIES && QUERIES.length) {{
      setStatus("未在 PDF 文本层找到匹配片段（可能是扫描件或解析文本与原件不一致）。可切换「解析文本预览」。已展示部分页面。");
    }} else {{
      setStatus("已加载 PDF（无可用的定位线索）。");
    }}

    // 剩余页按需追加（命中后把全文慢慢挂上，便于翻阅）
    if (hitPage > 0 && pdf.numPages > pagesToRender.length) {{
      var rendered = {{}};
      for (var z = 0; z < pagesToRender.length; z++) rendered[pagesToRender[z]] = 1;
      (async function appendRest() {{
        for (var p2 = 1; p2 <= pdf.numPages; p2++) {{
          if (rendered[p2]) continue;
          var pg2 = await pdf.getPage(p2);
          await renderPage(pg2, p2, null);
        }}
      }})();
    }}
  }}

  document.getElementById("btn-jump").addEventListener("click", function () {{
    if (hitAnchor) hitAnchor.scrollIntoView({{ behavior: "smooth", block: "center" }});
  }});

  loadPdf();
}})();
  </script>
</body>
</html>
"""
