# 智能客服意图识别 · 轻量 LLM（已废弃）

> **2026-09-14**：意图后端已收敛为 **`rules` | `funnel`**。  
> 进程内 Qwen2.5-0.5B（`CHATBOT_INTENT_BACKEND=llm`）**已删除**，请改用：
>
> - 默认：`CHATBOT_INTENT_BACKEND=rules`
> - 口语覆盖：`CHATBOT_INTENT_BACKEND=funnel`（L1 规则 → L2 现有 Embedding 原型 → L3 vLLM 主对话）
>
> 详见 `docs/基于地降所项目改造/AI问答改造.md` §5.4。
