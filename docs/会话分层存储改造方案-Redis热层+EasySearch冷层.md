# 会话分层存储改造方案（Redis 热层 + EasySearch 冷层）

> 目标：在保持在线会话低延迟体验的前提下，实现企业级“长期可追溯会话历史”。  
> 适用范围：`/chatbot/sessions*`、对话上下文记忆、会话归档与历史回查。  
> 关联现状文档：`enterprise-level_transformation_docs/系统会话管理实现方案.md`。
> 当前实现状态（本次）：已落地“写入即归档 + 查询自动回查 + 对象存储备份增强（可配置）”。

---

## 1. 背景与问题

当前会话存储以 Redis 为主，受 `CONV_SESSION_TTL_MINUTES` 控制过期；默认值较小时（如 60 分钟），会出现：

- 在线接口在会话过期后查不到历史；
- `docker compose down/up` 后更容易暴露“之前会话消失”的现象（本质是 key 过期，不是 volume 清空）；
- 将 TTL 直接设为 0 会把 Redis 变成长期历史库，带来内存、AOF/RDB 体积与恢复时延风险。

结论：Redis 更适合“热数据缓存与上下文记忆”，不应单独承担长期历史存档。

---

## 2. 改造目标

| 目标 | 说明 |
|------|------|
| 在线低延迟 | 近期会话继续走 Redis，保障 `/chatbot/chat*` 与 `/chatbot/sessions*` 响应速度 |
| 长期可追溯 | 全量会话消息异步归档到 EasySearch，支持按用户/会话完整回查 |
| 低侵入演进 | 兼容现有 `ConversationManager` 抽象，分阶段灰度 |
| 成本可控 | Redis 保持有限 TTL；冷层按 ILM 控容量，必要时再做对象存储二级归档 |

---

## 3. 总体架构（目标态）

```text
写路径（对话产生）
Chatbot/LLM/Analysis/NL2SQL
          │
          ▼
ConversationManager.append_*
          │
          ├─ Redis 热层（List + ZSET + Hash，短中期保留）
          │
          └─ 归档任务（异步）→ EasySearch 冷层（长期）

读路径（会话查询）
/chatbot/sessions* API
          │
          ├─ 先读 Redis 热层
          ├─ miss / 历史页超窗 -> 回查 EasySearch
          └─ 聚合排序后返回（可标注来源 hot/cold）
```

---

## 4. 数据分层与保留策略

### 4.1 热层（Redis）

- 作用：上下文记忆、近期会话目录、最近历史导出。
- 结构沿用现有：
  - `conv:{user_id}:{session_id}`（List）
  - `conv:index:{user_id}`（ZSET）
  - `conv:meta:{user_id}:{session_id}`（Hash）
- 建议策略：
  - `CONV_SESSION_TTL_MINUTES`：建议 7~30 天（如 `10080` / `43200`），不建议 0。
  - `CONV_MAX_HISTORY_MESSAGES`：保持上下文窗口可控，避免热层无界增长。

### 4.2 冷层（EasySearch）

- 作用：完整历史、审计追溯、运营检索。
- 建议索引：
  - `conversation_messages_v1`（消息事件索引）
  - 可选 `conversation_sessions_v1`（会话汇总索引，非必须）
- 保留策略：
  - ES ILM：hot/warm/cold 分层 + 删除策略（如 180 天、365 天）。

### 4.3 对象存储（备份增强层）

- 本次已落地（可配置开启）；支持 `local` 与 `s3`（S3 兼容对象存储）两类后端。
- 不建议直接用于在线查询接口主数据源。

---

## 5. 冷层索引模型（建议）

### 5.1 `conversation_messages_v1`

每条消息一条文档（推荐）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `tenant_id` | keyword | 多租户隔离（可选） |
| `user_id` | keyword | 用户 ID |
| `session_id` | keyword | 会话 ID |
| `message_id` | keyword | 全局唯一消息 ID（UUID/ULID） |
| `seq` | long | 会话内顺序号（可选，便于严格排序） |
| `role` | keyword | user/assistant/system |
| `content` | text + keyword_sub | 消息正文 |
| `ts_ms` | long | 写入时间（毫秒） |
| `title_snapshot` | keyword | 该时刻会话标题快照（可选） |
| `title_source_snapshot` | keyword | off/truncated/user |
| `intent_label` | keyword | 可选（assistant 元信息） |
| `used_rag` | boolean | 可选 |
| `used_nl2sql` | boolean | 可选 |
| `trace_id` | keyword | 可选追踪 |
| `archived_at_ms` | long | 归档时间 |

索引主排序建议：`user_id` + `session_id` + `ts_ms`（查询时按时间正序）。

---

## 6. 归档机制（自动）

## 6.1 触发方式

当前实现采用**写入即归档（append 旁路）**：

- 用户/助手消息写入热层后，立即写入 EasySearch 冷层；
- 幂等：`message_id = sha256(user_id + session_id + role + ts + content)`，重复写入同一条消息时覆盖同 ID 文档；
- 归档失败不影响在线主链路（best-effort）。

后续可按流量演进为“队列异步归档 + 定时回补”模式。

### 6.2 位点与幂等

- 维护 `archive_cursor`（按 `(user_id, session_id)` 的最后归档 `ts/seq/message_id`）。
- 归档任务支持重试；重复执行不产生重复消息。

### 6.3 失败补偿

- 归档失败不影响在线会话写入（解耦）；
- 指标告警：归档延迟、失败率、积压量；
- 支持手工回补任务（按用户、会话、时间窗重放）。

---

## 7. 查询自动回查冷层（核心）

### 7.1 目录接口 `/chatbot/sessions`

1. 先读 Redis 目录（热层）；
2. 当查询页超出热层范围，或配置 `CONV_QUERY_FALLBACK_COLD=true` 时，补查冷层会话聚合；
3. 合并去重（按 `session_id`），按 `last_activity_at` 排序分页；
4. 可选返回 `source: hot|cold|mixed`（便于前端标识）。

### 7.2 消息接口 `/chatbot/sessions/messages`

1. 优先读取 Redis（满足低延迟）；
2. Redis miss 或 `before_ts` 超过热层窗口时，回查 ES；
3. 若同时命中热层 + 冷层，按 `ts` 合并去重，确保“完整历史”。

### 7.3 一致性要求

- 回查结果必须保持 `(role, content, ts)` 语义一致；
- 标题优先级延续现有：`meta.title` > `preview` > `session_id`，冷层可补 `title_snapshot`；
- 保证同会话消息按时间稳定排序。

---

## 8. 当前会话存储与上下文记忆的关系（澄清）

- **同一数据源，不同用途**：
  - 会话管理：查询/导出/目录展示（期望长期可追溯）
  - 上下文记忆：推理时读取最近窗口（`CHATBOT_HISTORY_LIMIT`）
- 本改造后：
  - 上下文记忆仍只读热层最近窗口（性能优先）
  - 历史查询可自动回查冷层（完整性优先）

---

## 9. 实施分期（建议）

### Phase 1（止血）

- 将 `CONV_SESSION_TTL_MINUTES` 提高到 7~30 天；
- 保持现有接口与行为，避免短期历史丢失体验。

### Phase 2（冷层落地）

- 新增 ES 索引与归档任务；
- 建立归档位点、幂等写入、监控告警。

### Phase 3（自动回查）

- `/sessions`、`/sessions/messages` 增加冷层回查逻辑；
- 灰度发布（按用户比例/环境开关）。

### Phase 4（治理优化）

- ILM 调优、对象存储二级归档（可选）；
- 运营分析与审计报表接入。

---

## 10. 配置建议（新增）

| 变量 | 建议值 | 说明 |
|------|--------|------|
| `CONV_SESSION_TTL_MINUTES` | 10080 或 43200 | 热层保留 7/30 天 |
| `CONV_QUERY_FALLBACK_COLD` | true | 查询自动回查冷层开关 |
| `CONV_ARCHIVE_ENABLED` | true | 归档总开关 |
| `CONV_ARCHIVE_ES_INDEX` | conversation_messages_v1 | 冷层索引名 |
| `CONV_ARCHIVE_ES_SESSIONS_INDEX` | conversation_sessions_v1 | 冷层会话汇总索引 |
| `CONV_ARCHIVE_QUERY_MAX_LIMIT` | 2000 | 冷层单次最大查询条数 |
| `CONV_ARCHIVE_OBJECT_ENABLED` | true | 对象存储备份开关 |
| `CONV_ARCHIVE_OBJECT_BACKEND` | local / s3 | 备份后端类型 |
| `CONV_ARCHIVE_OBJECT_LOCAL_DIR` | ./data/conversation_archive | local 备份目录 |
| `CONV_ARCHIVE_OBJECT_S3_BUCKET` | conversation-archive | s3 bucket（启用 s3 时） |

> 命名可按项目既有前缀规范调整，上表用于定义实施时的配置面。

---

## 11. 验收标准

- 同一用户会话在超过 Redis TTL 后仍可通过 `/chatbot/sessions*` 查到完整历史；
- 任意时间窗回查消息顺序正确、无重复、无缺失；
- 归档失败不影响在线问答；
- 监控可观测：归档延迟、失败率、回查命中率、接口时延。

---

## 12. 风险与注意事项

- 冷层回查会增加查询延迟，需分页与时间窗约束；
- 若消息量巨大，必须做 ILM 与分片规划；
- 严禁将 Redis TTL=0 作为长期方案替代冷层；
- 归档与查询都要考虑多租户隔离与敏感字段脱敏策略。

---

## 13. 本方案与现有文档关系

- 本文是“目标态改造方案”；  
- `enterprise-level_transformation_docs/系统会话管理实现方案.md` 继续描述“当前已实现（Redis 主存）”；  
- 两者并行维护：一个讲“现状”，一个讲“演进”。

