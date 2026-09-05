# 23 · 结构化输出、工具协议与 MCP

> 面试定位：2025 后 Agent/平台岗高频。能讲 JSON 约束解码、Function Calling 与 MCP 分层。  
> 预计：40 分钟。

---

## 0. 30 秒能讲清楚

要让模型驱动软件，就不能靠「请输出 JSON」碰运气。可靠路径是：**Schema → 约束解码或工具调用 → 运行时校验 → 失败重试**。MCP（Model Context Protocol）则是 **工具与数据源的标准插线板**，把「每个 Agent 框架自造插件」变成可复用服务器。

---

## 1. 结构化输出三档

| 档位 | 做法 | 可靠性 |
|------|------|--------|
| 提示约束 | 「只输出 JSON」 | 低，长文本易坏 |
| 校验重试 | Pydantic / jsonschema，失败回灌 | 中，多一轮 |
| 约束解码 | 按 CFG/JSON Schema 掩码 logits | 高，语法保证 |

约束解码（Outlines、xgrammar、llama.cpp grammar、vLLM guided decoding、OpenAI Structured Outputs）：

- **保证句法合法**，不保证语义正确（字段值仍可能瞎填）  
- 过严的 schema 会逼模型「填满可选字段」  
- 与思维链冲突：要先 think 再填 JSON，或分两阶段  

面试金句：

> 约束解码解决括号，校验器解决类型，业务规则解决对错。

---

## 2. Function Calling 细节

典型协议：模型输出 `tool_calls: [{name, arguments}]`，宿主执行后把 `tool` 角色消息喂回。

要会讲：

- **并行工具**：同时查天气和日历  
- **多轮**：根据观察再调  
- **枚举参数**：减少幻觉出不存在的表名  
- **strict / schema**：参数也走约束解码  
- **失败信息**：把错误类型返回，而不是只说 failed  

和「自己解析 XML 标签」比：官方 tool 协议省解析，但仍要鉴权。

---

## 3. MCP 是什么、不是什么

**是**：客户端（IDE/Agent 宿主）通过标准协议发现并调用 **MCP Server** 提供的 tools/resources/prompts。  
**不是**：不是更大的基座模型，也不是替代 LangGraph 的业务状态机。

类比：LSP 对编辑器；MCP 对 Agent 工具。

面试怎么用：

- 企业把「查 Jira / 查数仓 / 读 Confluence」做成 MCP Server  
- 多个宿主（Cursor、自研客服、批处理）共用  
- 权限仍在 Server 侧：token、ACL、审计  

注意：MCP 扩大了工具攻击面，**每个 Server 都是间接注入入口**，见 [20](./20-安全红队与合规.md)。

---

## 4. 其它协议与框架名词

| 名词 | 一句话 |
|------|--------|
| OpenAI Tools / Anthropic Tools | 厂商 tool 格式，语义相近 |
| LangChain Tools | 编程抽象，可转厂商格式 |
| OpenAPI / Swagger 转工具 | 把 REST 自动暴露给模型，要裁剪与鉴权 |
| A2A / 多 Agent 协议 | 代理间通信，尚不统一，知道即可 |
| Computer Use | 操作系统级动作（鼠标键盘），沙箱刚需 |
| Skills / 插件商店 | 打包提示词+工具+资源 |

---

## 5. 设计好用的工具（行业经验）

1. 一个工具一件事，名字动词化  
2. 描述写清 **不要用它的情况**（否则模型乱调检索）  
3. 返回给模型的是 **摘要**，不是 10MB JSON  
4. 幂等、超时、重试次数在运行时，不让模型负责  
5. 高危工具默认关闭或 HITL  
6. 用「只读查询」和「提交变更」拆成两个工具  

---

## 6. 和编排的关系

```text
业务 Graph（确定流程）
  └─ 节点内：Structured Output 填槽 / Tool Calling
        └─ Tool 实现：本地函数 或 MCP Server
```

不要把 MCP 当成编排器。编排仍要步数上限和状态持久化（[08](./08-Agent与工作流编排.md)）。

---

## 7. 高频问答

**Q：有 Structured Outputs 还要 Pydantic 吗？**  
A：要。句法对了，枚举值、跨字段约束、业务日历仍可能错。

**Q：MCP 和直接写 HTTP 工具？**  
A：工具要给多个宿主复用、给非开发运维配置时用 MCP；单应用三个内部函数直接写更简单。

**Q：约束解码会变慢吗？**  
A：每步多一次合法 token 掩码，通常可接受；复杂文法要看实现。比「生成失败再重试三次」往往更划算。

**Q：模型编造不存在的 tool？**  
A：运行时白名单拦截；Prompt 只列可用工具；strict 模式。

---

## 8. 速记口诀

> **Schema 管语法，校验管类型，权限管后果。**  
> **MCP 是插线板，不是大脑。**
