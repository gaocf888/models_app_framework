# Service API Key 认证与安全说明

本文描述 **app 接口服务** 当前采用的业务层鉴权方式：调用方在 HTTP 请求头携带 **静态 Service API Key**，服务端与配置中的密钥做比对。适用于「算法 / 中台服务」被 **受信任的后端** 调用的典型集成形态。

---

## 1. 机制概述

| 项目 | 说明 |
|------|------|
| **协议形态** | HTTP `Authorization: Bearer <service-api-key>` |
| **密钥来源** | 运维生成后写入环境变量；**应用内不提供**在线签发、登录或换票接口。 |
| **校验位置** | `app/auth/dependencies.py` 中 `require_service_api_key`，作为 FastAPI 路由依赖挂载在业务路由上。 |
| **用户身份** | **不与 API Key 一一映射**；业务上的 `user_id` / `session_id` 仍由调用方在请求体或 Query 传入，与 Key 正交。 |

---

## 2. 环境变量配置

| 变量 | 说明 |
|------|------|
| `SERVICE_API_KEYS` | **推荐**。英文逗号分隔的多个密钥；校验时 **任一匹配** 即通过。用于轮换期新旧并存。 |
| `SERVICE_API_KEY` | 仅配置 **单个** 密钥时使用（当未设置 `SERVICE_API_KEYS` 时读取）。 |

- 未配置任何有效密钥时：受保护路由返回 **503**，`detail` 为 `SERVICE_API_KEYS is not configured`（表示服务未就绪，而非客户端凭据错误）。
- 示例与注释见：`app/app-deploy/.env.example`；部署步骤见同目录 **`README.md` / `README-simple-deploy.md`** 中「Service API Key」小节。

---

## 3. 密钥生成与轮换

### 3.1 生成

- 使用 `app/auth/keygen.py` 中的 **`generate_service_api_key()`**（基于 `secrets.token_urlsafe`，密码学安全随机源）。
- 本机命令示例见该模块顶部文档字符串（需将仓库根目录加入 `PYTHONPATH` 以便 `import app`）。

### 3.2 轮换建议

1. 在 `SERVICE_API_KEYS` 中同时写入 **旧钥 + 新钥**（逗号分隔）。  
2. 调用方逐步切换为新钥。  
3. 全部切换完成后从配置中移除旧钥并重新部署 / 滚动发布使环境变量生效。

### 3.3 禁止事项

- 勿将真实密钥提交到 Git、写入镜像层或明文工单。  
- 生产环境优先使用 **密钥管理系统 / K8s Secret / CI 注入** 与运行环境一致。

---

## 4. 安全实现要点（代码层）

- **常量时间比对**：对候选密钥使用 `hmac.compare_digest`，并在 **长度一致** 时才比对，降低时序侧信道风险。  
- **多钥遍历**：任一等长且内容匹配的密钥即通过；无数据库查询。  
- **错误信息**：非法或缺失密钥统一为 **401**（`Invalid API key` 或 Header 格式错误提示），避免在响应中区分「用户不存在」等细粒度枚举（当前实现本身也不区分多用户）。

---

## 5. HTTP 行为摘要

| 场景 | HTTP 状态 |
|------|-----------|
| 未配置任何密钥 | **503**（服务不可用语义） |
| 缺少 `Authorization` 或非 Bearer | **401** |
| Bearer 与配置均不匹配 | **401** |
| 匹配成功 | 进入业务逻辑 |

**默认不校验 Service API Key 的路由**（以当前 `app/main.py` 为准）：例如健康检查 **`/health/*`、`/api/health`**、**`/metrics`** 等。业务路由（如 `/chatbot`、`/llm`、`/rag`、`/dajia` 等）挂载鉴权依赖。

---

## 6. 运维与安全建议

1. **传输**：生产环境应对外 **HTTPS**，避免密钥在链路上明文暴露。  
2. **权限边界**：持有有效 Service API Key 的调用方 **可调用所有受保护业务 API**；若需按租户或产品线隔离，应在 **网关 / BFF** 侧再控权，或演进为多 Key 与路由映射（当前代码为单类共享密钥）。  
3. **日志**：避免在 access log 或应用日志中打印完整 `Authorization` 头。  
4. **与 vLLM 密钥区分**：访问 vLLM 的 `LLM_DEFAULT_API_KEY` 与 Service API Key **无关**，勿混用变量名。

---

## 7. OpenAPI / Swagger

应用在 OpenAPI 组件中声明了 **`ServiceApiKey`**（HTTP Bearer），便于在 Swagger UI 中填写密钥试调；实际校验仍以进程环境变量为准。

---

## 8. 相关代码与文档路径

| 内容 | 路径 |
|------|------|
| 鉴权依赖 | `app/auth/dependencies.py` |
| 密钥生成 | `app/auth/keygen.py` |
| 路由挂载 | `app/main.py`（`include_router(..., dependencies=[Depends(require_service_api_key)])`） |
| 环境模板 | `app/app-deploy/.env.example` |
| 部署说明 | `app/app-deploy/README.md`、`README-simple-deploy.md` |

---

## 9. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-04-09 | 初版：替代已删除的 SQLite/JWT 方案文档，描述当前 Service API Key 模型与安全注意。 |
