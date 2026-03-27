from __future__ import annotations

"""
智能客服 Gradio Demo（流式版）。

用途：
- 为客户/开发者提供可视化的智能客服测试界面；
- 直接调用后端 `/chatbot/chat/stream`（token 级 SSE）。

使用方式：
1) 先启动后端服务；
2) 设置可选环境变量：
   - CHATBOT_API_BASE（默认 http://127.0.0.1:8000/chatbot）
3) 运行：
   `python -m app.test_scripts.chatbot_gradio_demo`
"""

import json
import os
from typing import Generator, List

import gradio as gr
import httpx

API_BASE = os.getenv("CHATBOT_API_BASE", "http://127.0.0.1:8000/chatbot").rstrip("/")


def _collect_image_urls(image_files: list | None, image_urls_text: str) -> List[str]:
    urls: List[str] = []
    if image_urls_text:
        urls.extend([u.strip() for u in image_urls_text.split(",") if u.strip()])
    if image_files:
        # Demo 中允许传 file:// 本地路径作为图片 URL 线索。
        for f in image_files:
            p = getattr(f, "name", None)
            if p:
                urls.append(f"file://{p}")
    return urls


def stream_fn(
    message: str,
    history: List[List[str]],
    enable_rag: bool,
    enable_context: bool,
    image_files: list | None,
    image_urls_text: str,
    user_id: str,
    session_id: str,
) -> Generator:
    if not message:
        yield "", history
        return
    image_urls = _collect_image_urls(image_files, image_urls_text)
    req = {
        "user_id": user_id or "demo_user",
        "session_id": session_id or "demo_session",
        "query": message,
        "image_urls": image_urls,
        "enable_rag": bool(enable_rag),
        "enable_context": bool(enable_context),
    }
    history = history + [[message, ""]]

    url = f"{API_BASE}/chat/stream"
    try:
        with httpx.Client(timeout=None) as client:
            with client.stream("POST", url, json=req) as resp:
                resp.raise_for_status()
                answer = ""
                for line in resp.iter_lines():
                    if not line:
                        continue
                    if not line.startswith("data:"):
                        continue
                    data_raw = line[len("data:") :].strip()
                    if not data_raw:
                        continue
                    try:
                        event = json.loads(data_raw)
                    except Exception:
                        continue
                    if event.get("finished"):
                        break
                    delta = event.get("delta") or ""
                    if delta:
                        answer += delta
                        history[-1][1] = answer
                        yield "", history
    except Exception as exc:  # noqa: BLE001
        history[-1][1] = f"[stream error] {exc}"
        yield "", history
        return

    yield "", history


with gr.Blocks() as demo:
    gr.Markdown("## 智能客服 Demo（多模态 + token 流式）")
    gr.Markdown(f"后端地址：`{API_BASE}`")
    chatbot = gr.Chatbot()
    with gr.Row():
        user_id = gr.Textbox(value="demo_user", label="user_id", scale=1)
        session_id = gr.Textbox(value="demo_session", label="session_id", scale=1)
    with gr.Row():
        msg = gr.Textbox(label="你的问题", scale=4)
        images = gr.File(label="上传图片（可选，多选）", file_count="multiple", scale=2)
    image_urls_text = gr.Textbox(label="图片URL（可选，逗号分隔）", value="")
    with gr.Row():
        enable_rag = gr.Checkbox(value=True, label="启用 RAG 检索")
        enable_context = gr.Checkbox(value=True, label="启用会话上下文")

    msg.submit(
        stream_fn,
        [msg, chatbot, enable_rag, enable_context, images, image_urls_text, user_id, session_id],
        [msg, chatbot],
    )


if __name__ == "__main__":
    demo.launch()

