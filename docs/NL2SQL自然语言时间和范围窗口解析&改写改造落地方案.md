# NL2SQL 自然语言「时间 + 范围」窗口解析 & 改写 — 改造落地方案

> **版本**：2026-06-16  
> **范围**：NL2SQL 基座能力 — 从用户问句中解析**时间语义**与**实体范围**（机组/锅炉、设备/受热面、管排名称、排数、管数），并可选改写 SQL 过滤条件。  
> **关联文档**：`docs/NL2SQL系统概要设计.md`、`docs/NL2SQL缓存实现方案.md`、`docs/超温分析-v2-NL2SQL-QA指纹修复操作说明.md`  
> **关联代码**：`app/nl2sql/chain.py`、`app/nl2sql/time_intent_display.py`、`app/nl2sql/sql_skeleton.py`、`app/llm/graphs/overheat_synthesis_render.py`

---

## 0. 文档定位

### 0.1 背景

用户自然语言问题中常同时包含：

1. **时间口径**：如「昨天」「近一周」「2025年第一季度」；
2. **实体范围**：如「1号锅炉」「低温过热器」「第一层」「第一排第一根」。

当前 NL2SQL 基座已实现：

| 能力 | 实现方式 | 主要代码 |
|------|----------|----------|
| 时间解析 | 程序规则（较完整） | `time_intent_display.py`、`sql_skeleton.py` |
| 机组/锅炉范围 | 程序规则 | `NL2SQLChain._extract_boiler_scope_label_from_question` 等 |
| 设备/受热面、管排、排数、管数 | **未实现** | — |

时间解析结果用于 `_rewrite_query_filters` 中的动态时间窗改写；锅炉范围用于 `@unit_keyword` 占位符与 `boiler_name LIKE` 替换。**超温分析**（`analysis_type=overheat_guidance`）重度依赖上述两项，改造必须保证向后兼容。

### 0.2 改造目标

| 编号 | 目标 |
|------|------|
| G1 | **完善范围解析**：机组/锅炉、设备/受热面、管排名称、排数、管数；问句含什么解析什么，其余字段为空 |
| G2 | **锅炉解析不变**：保持现有「N号机组 → N号锅炉」、全厂 → `None` 语义 |
| G3 | **时间解析不变**：继续走现有 `time_intent_display` / `sql_skeleton` 逻辑 |
| G4 | **双模式可配置**：默认程序规则；可选 vLLM 大模型解析；提示词模板化 |
| G5 | **不影响现网**：尤其超温分析、QA 槽位 replay、`time_intent_source` 机制 |

### 0.3 非目标（本方案不强制包含）

- 不改造 NL2SQL **SQL 生成 Prompt** 的主链路结构（仅可选注入「已识别意图」摘要）；
- 不强制所有 plan 子任务 SQL 立即支持管排/管数占位符（Phase 2 按需启用）；
- 不替代检修报告结构化提取（`inspection_v2`）中的表格行号/管号逻辑；
- 不在本阶段实现多轮对话中的指代消解（如「它」「上述管段」）。

---

## 1. 可行性结论

**结论：可行，且与现有架构高度契合。**

理由：

1. **时间 + 锅炉**已在 `_rewrite_query_filters` 形成稳定流水线，扩展 scope 字段属于同层「问句 → 结构化意图 → SQL 改写」模式。
2. **超温分析**当前 plan SQL 以「时间窗 + 锅炉过滤」为主；新增字段采用**增量、可选、默认不改 SQL**策略，回归风险可控。
3. 检修模块（`app/inspection_v2/record_normalization.py`）已有受热面简称、水冷壁排数默认等业务规则，可复用为解析词典与后处理依据。
4. 项目已具备 **PromptTemplateRegistry**、**VLLMHttpClient**、**环境变量配置**（`app/core/config.py`）等 LLM 解析基础设施。

---

## 2. 现状分析

### 2.1 时间解析（保持不动）

**入口**：`extract_time_window_from_question`（`time_intent_display.py`）

**优先级**（与 `resolve_time_intent` 对齐）：近 N 天 → 近 N 年 → 本周/上周 → 季度 → 半年 → 本月/上月 → 前年 → 相对日 → 精确日等。

**消费点**：

- `NL2SQLChain._resolve_time_window_for_rewrite`：plan 长问句与用户 `time_intent_source` 的优先级仲裁；
- `NL2SQLChain._rewrite_dynamic_time_window` / `_rewrite_time_placeholders`：替换 `@t_start`/`@t_end` 与字面量日期；
- `overheat_synthesis_render.infer_overheat_report_context`：报告展示用统计时间窗。

**关键兼容机制**：综合分析传入 `time_intent_text`（即 `req.query`），避免 plan 任务长问句尾部 RAG 规则线索中的示例日期/锅炉名污染时间抽取。见 `tests/test_nl2sql_chain_tidb.py::test_today_wins_over_iso_date_in_long_plan_question`。

### 2.2 锅炉/机组范围解析（扩展基础）

**入口**：`NL2SQLChain._extract_boiler_scope_label_from_question`

**规则摘要**：

- `N号锅炉`、`N号机组`、`N#机组`、`#N机组`、中文数字序号 → 归一化为 **`N号锅炉`**；
- 显式全厂（「全厂」「所有机组」等）→ **`None`**（表示不过滤）；
- 问句同时含单机组与「所有机组」→ **单机组优先**（见 `test_single_boiler_wins_over_full_plant_phrase_in_same_query`）。

**消费点**：

- `_extract_scope_literals_from_question` → 仅返回 `unit_keyword` / `boiler`；
- `_rewrite_entity_scope_literals` → 替换 `@unit_keyword`、`boiler_name = '…'`、`LIKE CONCAT('%', '…', '%')` 等。

### 2.3 实体范围问句来源（防污染）

```python
# app/nl2sql/chain.py — _resolve_entity_scope_question
# 优先 time_intent_source（用户原句），否则 strip plan 尾部 RAG 附录
```

超温分析 acquire_data 调用 `NL2SQLService.query` 时设置 `time_intent_text=req.query`，scope 解析**必须复用同一问句来源策略**。

### 2.4 数据库字段对照（改写 Phase 2 参考）

| 语义 | 典型表.列 | 说明 |
|------|-----------|------|
| 锅炉名称 | `account_boiler.boiler_name` | 已有 LIKE 改写 |
| 受热面/设备 | `account_static_device.device_name` | 模糊匹配 |
| 管排名称 | `account_device_piperow.piperow_name` | 模糊匹配；口语与台账可能不完全一致 |
| 排数/管数（台账汇总） | `account_device_piperow.row_count` / `pipe_count` | 管排级汇总字段 |
| 测点定位 | `base_temp_point` 等 | 单管/单排测点过滤需 JOIN 链路，按 catalog FK 书写 |

---

## 3. 目标数据模型

### 3.1 范围意图 `QuestionScopeIntent`

```python
@dataclass(frozen=True)
class QuestionScopeIntent:
    boiler: str | None       # 归一化「N号锅炉」；None = 全厂或未指定
    device_name: str | None  # 受热面全称，如「低温过热器」「水冷壁前墙垂直段」
    piperow_name: str | None # 管排名称，如「第一层」「炉前向炉后数」「第一屏」
    row_no: int | None       # 排数（阿拉伯数字）
    tube_no: int | None      # 管数（阿拉伯数字）
```

**分级解析原则**：问句中**只出现哪一层级，就只填该层级**； deeper 层级未出现则 deeper 字段为 `null`。

示例：

| 用户问题片段 | boiler | device_name | piperow_name | row_no | tube_no |
|-------------|--------|-------------|--------------|--------|---------|
| 「1号锅炉超温」 | 1号锅炉 | null | null | null | null |
| 「低温过热器超温」 | null | 低温过热器 | null | null | null |
| 「1号锅炉低温过热器第一层第一排第一根」 | 1号锅炉 | 低温过热器 | 第一层 | 1 | 1 |

### 3.2 统一问句意图 `QuestionIntent`

```python
@dataclass(frozen=True)
class QuestionIntent:
    raw_question: str
    scope_question: str          # 经 _resolve_entity_scope_question 清洗后的问句
    time_window: tuple[str, str, str] | None  # (start_expr, end_expr, tag)，复用现有结构
    scope: QuestionScopeIntent
    parse_mode: Literal["rule", "llm", "llm_fallback_rule"]
```

### 3.3 对外 JSON 示例

**示例 1**：`1号锅炉低温过热器第一层第一排第一根`

```json
{
  "time_window_tag": null,
  "boiler": "1号锅炉",
  "device_name": "低温过热器",
  "piperow_name": "第一层",
  "row_no": 1,
  "tube_no": 1
}
```

**示例 2**：`请分析2号机组昨天的超温情况`（仅锅炉 + 时间）

```json
{
  "time_window_tag": "yesterday",
  "boiler": "2号锅炉",
  "device_name": null,
  "piperow_name": null,
  "row_no": null,
  "tube_no": null
}
```

**示例 3**：`请分析所有机组近一周超温`（全厂 + 时间）

```json
{
  "time_window_tag": "recent_7_days",
  "boiler": null,
  "device_name": null,
  "piperow_name": null,
  "row_no": null,
  "tube_no": null
}
```

---

## 4. 程序规则解析（Phase 1 — 默认）

### 4.1 模块划分

```
app/nl2sql/
  question_intent.py       # 统一 facade：resolve_question_intent()
  scope_parser_rule.py     # 程序规则解析实现
  scope_lexicon.py         # 词典加载（JSON / env 路径）
  scope_parser_llm.py      # Phase 3：LLM 解析
  chain.py                 # 兼容层；Phase 2 SQL 占位符改写
```

### 4.2 解析流水线（顺序固定）

```
原始问句 question
    │
    ▼
_resolve_entity_scope_question(question, time_intent_source)
    │  → scope_question（优先用户原句，防 plan/RAG 污染）
    ▼
① 锅炉/机组（复用 NL2SQLChain._extract_boiler_scope_label_from_question）
    │  → boiler；逻辑**不得修改**
    ▼
② 简称展开（低过→低温过热器、屏过→屏式过热器…）
    ▼
③ 受热面/设备（词典最长匹配，匹配后从剩余文本剥离）
    ▼
④ 管排名称（模式库 + 正则，匹配后剥离）
    ▼
⑤ 排数（第N排 / 第N行 → int）
    ▼
⑥ 管数（第N根 / 第N管 → int）
    ▼
⑦ 后处理：水冷壁系且 row_no 未解析 → row_no = 1
    ▼
QuestionScopeIntent
```

**时间解析**在 facade 层并行调用 `extract_time_window_from_question(scope_question)`，与 scope 流水线解耦。

### 4.3 字段规则明细

#### 4.3.1 机组/锅炉（保持原样）

- 复用 `NL2SQLChain._extract_boiler_scope_label_from_question`、`_extract_unit_keyword_from_question`、`_has_explicit_full_plant_scope`；
- **禁止**在新模块中重写锅炉序号归一逻辑，仅调用现有方法；
- 单元测试：迁移/引用 `tests/test_nl2sql_chain_tidb.py` 中现有锅炉相关用例，确保零回归。

#### 4.3.2 设备/受热面

**匹配策略**：配置词典按**最长子串优先**；简称先展开再匹配。

**简称表（初始）**：

| 简称 | 全称 |
|------|------|
| 低过 | 低温过热器 |
| 高过 | 高温过热器 |
| 高再 | 高温再热器 |
| 低再 | 低温再热器 |
| 屏过 | 屏式过热器 |

> 屏过也称「屏式过热器」或「分隔屏过热器」，解析结果统一为 **屏式过热器**。

**复合受热面示例**（整段作为 device_name）：

- 水冷壁前墙垂直段
- 水冷壁左墙
- 低温过热器
- 屏式过热器

**剥离规则**：命中 device 后，从 `scope_question` 的工作副本中移除该片段（及前置锅炉短语），避免与管排/排数正则冲突。

#### 4.3.3 管排名称

下列均属 **piperow_name**，保留用户语义或做轻量归一：

| 类型 | 示例 | 归一化建议 |
|------|------|------------|
| 层别 | 第一层、第1层 | 保留「第一层」 |
| 屏别 | 第一屏、第1屏 | 保留「第一屏」 |
| 屏别别名 | 前屏、后屏 | **前屏 → 第一屏；后屏 → 第二屏** |
| 计数方向 | 炉前向炉后数、炉后向炉前数 | 保留原文 |
| 复合 | 第一层炉右向炉左数 | 保留原文 |

**模式库（正则示意）**：

```text
第\s*([0-9一二三四五六七八九十]+)\s*层
第\s*([0-9一二三四五六七八九十]+)\s*屏
前屏|后屏
炉[前后左右]\s*向\s*炉[前后左右]\s*数
第\s*[0-9一二三四五六七八九十]+\s*层\s*炉[前后左右]\s*向\s*炉[前后左右]\s*数
```

#### 4.3.4 排数

- 匹配：`第(\d+|[一二两三四五六七八九十百]+)排` 或 `第…行`（可选，与检修口径对齐时启用）；
- 中文数字 → 阿拉伯数字（复用 `NL2SQLChain._cn_unit_index_to_int`）；
- **水冷壁特殊规则**：当 `device_name` 含「水冷壁」且问句**未**显式出现排数时，`row_no = 1`（与 `inspection_v2/record_normalization.py` 中 `_WALL_ROW1_MARKERS` 一致）。

#### 4.3.5 管数

- 匹配：`第(\d+|[一二…])根` 或 `第…管`；
- 输出阿拉伯数字 `tube_no`。

### 4.4 验收用例（必须全部通过）

| # | 用户问题 | boiler | device_name | piperow_name | row_no | tube_no |
|---|---------|--------|-------------|--------------|--------|---------|
| 1 | 1号锅炉低温过热器第一层第一排第一根 | 1号锅炉 | 低温过热器 | 第一层 | 1 | 1 |
| 2 | 二号锅炉水冷壁前墙垂直段炉前向炉后数第一根 | 2号锅炉 | 水冷壁前墙垂直段 | 炉前向炉后数 | **1** | 1 |
| 3 | 一号机组屏式过热器第一屏第一排第一根 | 1号锅炉 | 屏式过热器 | 第一屏 | 1 | 1 |
| 4 | 2号机组屏式过热器前屏第一排第一根 | 2号锅炉 | 屏式过热器 | 第一屏 | 1 | 1 |
| 5 | 1号机组低温过热器第一层炉右向炉左数第一排第一根 | 1号锅炉 | 低温过热器 | 第一层炉右向炉左数 | 1 | 1 |
| 6 | 二号机组水冷壁左墙炉后向炉前数第一根 | 2号锅炉 | 水冷壁左墙 | 炉后向炉前数 | **1** | 1 |

补充边界用例（建议单测覆盖）：

- 仅「请分析近一周超温」→ 全字段 scope 为空，时间 tag = `recent_7_days`；
- 「所有机组低温过热器超温」→ boiler=null，device_name=低温过热器；
- 「低过第一排」→ device_name=低温过热器，row_no=1，boiler=null；
- plan 长问句 + `time_intent_source=用户原句` → scope 以原句为准（对齐 `test_entity_scope_uses_time_intent_not_rag_guide_boiler_example`）。

### 4.5 配置化词典

**文件**：`configs/nl2sql_scope_device_aliases.json`（或通过环境变量 `NL2SQL_SCOPE_LEXICON_FILE` 指定）

```json
{
  "abbreviations": {
    "低过": "低温过热器",
    "高过": "高温过热器",
    "高再": "高温再热器",
    "低再": "低温再热器",
    "屏过": "屏式过热器"
  },
  "devices": [
    "低温过热器",
    "高温过热器",
    "高温再热器",
    "低温再热器",
    "屏式过热器",
    "分隔屏过热器",
    "水冷壁前墙垂直段",
    "水冷壁后墙垂直段",
    "水冷壁左墙",
    "水冷壁右墙",
    "省煤器"
  ],
  "piperow_aliases": {
    "前屏": "第一屏",
    "后屏": "第二屏"
  },
  "wall_row1_markers": ["水冷壁", "包墙", "后竖井", "冷灰斗"]
}
```

**加载策略**：进程内懒加载 + 文件 mtime 缓存；解析失败时回退内置默认词典并打 warning 日志。

### 4.6 与 `NL2SQLChain` 集成（兼容层）

#### 4.6.1 扩展 `_extract_scope_literals_from_question`

```python
@staticmethod
def _extract_scope_literals_from_question(question: str) -> dict[str, str | int | None]:
    intent = resolve_question_intent(question)  # 或传入 time_intent_source
    s = intent.scope
    unit_kw = s.boiler  # 与现网 unit_keyword 语义一致
    return {
        "unit_keyword": unit_kw,
        "boiler": unit_kw,
        "device_name": s.device_name,
        "piperow_name": s.piperow_name,
        "row_no": s.row_no,
        "tube_no": s.tube_no,
    }
```

**向后兼容**：仅读取 `unit_keyword` / `boiler` 的调用方行为不变。

#### 4.6.2 统一入口 API

```python
# app/nl2sql/question_intent.py

def resolve_question_intent(
    question: str,
    *,
    time_intent_source: str | None = None,
    parse_mode: str | None = None,
) -> QuestionIntent:
    ...
```

**调用建议**：

| 调用方 | question | time_intent_source |
|--------|----------|------------------|
| 独立 NL2SQL API | req.question | req.time_intent_text or req.question |
| 超温 acquire_data | plan 子任务 question | req.query |
| 超温 synthesis | req.query | req.query |

### 4.7 超温分析兼容性说明

| 关注点 | 策略 |
|--------|------|
| 时间窗改写 | **不改**；继续 `_resolve_time_window_for_rewrite` |
| 锅炉过滤 | **不改**；继续 `@unit_keyword` / `boiler_name` 改写 |
| 全厂 vs 单机组 | `infer_overheat_report_context` 仍用 regex 判断 `unit_scope`，不依赖新字段 |
| plan SQL（q0–q7） | Phase 1 **不**注入管排/管数 WHERE；仅结构化意图可供 trace/debug |
| QA strict replay | 不修改 replay SQL 模板；新占位符 Phase 2 默认关闭 |
| 回归测试 | 全量跑通 `tests/test_nl2sql_chain_tidb.py`、`tests/test_time_intent_p0_p1.py` |

---

## 5. SQL 占位符改写（Phase 2 — 按需启用）

### 5.1 设计原则

- **Opt-in**：默认 `NL2SQL_SCOPE_SQL_REWRITE_ENABLED=false`，现网零行为变化；
- **仅当 SQL 含对应占位符时才替换**（与 `@unit_keyword` 对称）；
- 某 scope 字段为 `null` 时，**不注入**对应条件（避免误收窄为 0 行）。

### 5.2 占位符约定

| 占位符 | 单值替换 | 全厂/未指定 |
|--------|----------|-------------|
| `@unit_keyword` | `'1号锅炉'` | `''`（已有） |
| `@device_keyword` | `'低温过热器'` | `''` |
| `@piperow_keyword` | `'第一层'` | `''` |
| `@row_no` | `1` | 不替换 / 跳过条件分支 |
| `@tube_no` | `1` | 不替换 / 跳过条件分支 |

**条件模板示例**：

```sql
AND (@device_keyword IS NULL OR @device_keyword = '' OR asd.device_name LIKE CONCAT('%', @device_keyword, '%'))
AND (@piperow_keyword IS NULL OR @piperow_keyword = '' OR adp.piperow_name LIKE CONCAT('%', @piperow_keyword, '%'))
```

**前屏/第一屏双分支**（当 piperow 归一为「第一屏」且台账可能存「前屏」时）：

```sql
AND (
  adp.piperow_name LIKE '%第一屏%'
  OR adp.piperow_name LIKE '%前屏%'
)
```

由改写层在识别到 `@piperow_keyword='第一屏'` 时展开；或通过 plan 模板显式 OR。

### 5.3 改写入口

在 `_rewrite_entity_scope_literals` 中，于现有 boiler 改写之后追加：

```python
if os.getenv("NL2SQL_SCOPE_SQL_REWRITE_ENABLED", "false").lower() == "true":
    rewritten, device_notes = self._rewrite_device_scope_placeholders(rewritten, scopes)
    ...
```

`_rewrite_relaxed_region_match` 继续对 `device_name` 等列做 `=` → `LIKE` 放宽，与 Phase 2 互补。

### 5.4 启用条件建议

- 单管段/单排查询类 plan 任务已改用占位符模板；
- 已在测试库验证 `piperow_name` 与口语映射；
- 超温全厂汇总类任务**不应**启用管排占位符（除非 question 显式含管排）。

---

## 6. 大模型解析（Phase 3 — 可配置）

### 6.1 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `NL2SQL_INTENT_PARSE_MODE` | `rule` | `rule` \| `llm` \| `rule_with_llm_fallback` |
| `NL2SQL_SCOPE_PARSE_LLM_TIMEOUT_MS` | `8000` | LLM 调用超时（毫秒） |
| `NL2SQL_SCOPE_PARSE_PROMPT_VERSION` | `v1` | `configs/prompts.yaml` 中 `nl2sql_scope_parse` 版本 |
| `NL2SQL_SCOPE_LEXICON_FILE` | （内置路径） | 词典 JSON 路径 |

写入位置：`app/core/config.py`（`AnalysisConfig` 或独立 `NL2SQLConfig` 段）、`app/app-deploy/.env.example`。

### 6.2 模式说明

| 模式 | 行为 |
|------|------|
| `rule` | 仅程序规则（**默认**，现网推荐） |
| `llm` | 范围字段走 LLM；**时间仍走程序规则**（需求：时间保持原逻辑） |
| `rule_with_llm_fallback` | 优先 LLM；超时/JSON 非法/校验失败 → 回退 rule |

### 6.3 提示词模板

**Scene**：`nl2sql_scope_parse`（新增于 `configs/prompts.yaml`）

**版本**：`v1`

**内容结构**：

1. 角色与输出约束（仅 JSON，无 markdown）；
2. **时间规则摘要**（供 LLM 理解上下文；实际 time 字段仍由程序计算，或 LLM 输出 `time_window_tag` 仅作交叉校验）；
3. **范围规则**（分级解析、简称、水冷壁排数默认、前屏/后屏别名）；
4. **6 条验收示例**（输入问句 + 标准 JSON）；
5. **输出 JSON Schema**。

**模板正文（建议写入 prompts.yaml）**：

```yaml
nl2sql_scope_parse:
  - version: v1
    weight: 1.0
    description: NL2SQL 问句范围结构化解析（时间由程序侧处理，LLM 仅输出 scope 字段）
    content: |
      你是锅炉领域问句「实体范围」解析器。根据用户问题，仅输出一个 JSON 对象，不要 markdown 代码块，不要解释。

      【范围解析规则】
      1. 分级解析：问句只提到哪一层，就只填那一层；未出现的字段必须为 null。
      2. 机组/锅炉：「N号锅炉」「N号机组」「N#机组」等统一输出为「N号锅炉」（阿拉伯数字+N号锅炉）。
         显式全厂（全厂、所有机组、全部锅炉等）且未同时指定单个机组时，boiler 为 null。
      3. 受热面简称须展开为全称：
         低过→低温过热器，高过→高温过热器，高再→高温再热器，低再→低温再热器，屏过→屏式过热器。
      4. 设备/受热面：提取完整受热面短语（如「水冷壁前墙垂直段」「水冷壁左墙」「屏式过热器」）。
      5. 管排名称：如「第一层」「炉前向炉后数」「第一屏」「前屏（等同第一屏）」「后屏（等同第二屏）」、
         「第一层炉右向炉左数」「炉后向炉前数」等，填入 piperow_name；前屏输出「第一屏」，后屏输出「第二屏」。
      6. 排数：「第一排」「第1排」等 → row_no 为阿拉伯数字 1；问句未写排数则为 null。
         特殊：受热面名称含水冷壁且问句未写排数时，row_no 输出 1。
      7. 管数：「第一根」「第1根」等 → tube_no 为阿拉伯数字；未写则为 null。

      【示例】
      问：1号锅炉低温过热器第一层第一排第一根
      {"boiler":"1号锅炉","device_name":"低温过热器","piperow_name":"第一层","row_no":1,"tube_no":1}

      问：二号锅炉水冷壁前墙垂直段炉前向炉后数第一根
      {"boiler":"2号锅炉","device_name":"水冷壁前墙垂直段","piperow_name":"炉前向炉后数","row_no":1,"tube_no":1}

      问：一号机组屏式过热器第一屏第一排第一根
      {"boiler":"1号锅炉","device_name":"屏式过热器","piperow_name":"第一屏","row_no":1,"tube_no":1}

      问：2号机组屏式过热器前屏第一排第一根
      {"boiler":"2号锅炉","device_name":"屏式过热器","piperow_name":"第一屏","row_no":1,"tube_no":1}

      问：1号机组低温过热器第一层炉右向炉左数第一排第一根
      {"boiler":"1号锅炉","device_name":"低温过热器","piperow_name":"第一层炉右向炉左数","row_no":1,"tube_no":1}

      问：二号机组水冷壁左墙炉后向炉前数第一根
      {"boiler":"2号锅炉","device_name":"水冷壁左墙","piperow_name":"炉后向炉前数","row_no":1,"tube_no":1}

      问：请分析所有机组近一周超温
      {"boiler":null,"device_name":null,"piperow_name":null,"row_no":null,"tube_no":null}

      【输出 schema】
      {"boiler":string|null,"device_name":string|null,"piperow_name":string|null,"row_no":number|null,"tube_no":number|null}

      【用户问题】
      {{QUESTION}}
```

运行时占位符 `{{QUESTION}}` 替换为 `scope_question`（经 `_resolve_entity_scope_question` 清洗）。

### 6.4 LLM 实现要点

**模块**：`app/nl2sql/scope_parser_llm.py`

1. 通过 `PromptTemplateRegistry.get("nl2sql_scope_parse", version=...)` 加载模板；
2. 调用 `VLLMHttpClient`（与 `NL2SQLChain` 一致，复用 vLLM 部署）；
3. 响应 JSON 经 Pydantic 模型校验；
4. **后处理**（与 rule 对齐）：
   - 简称展开；
   - 前屏/后屏 → 第一屏/第二屏；
   - 锅炉格式归一（调用 `_boiler_scope_label_from_index`）；
   - 水冷壁 row_no 默认 1；
5. 可选：LLM 与 rule 结果 diff 写入 debug 日志，便于提示词迭代。

**Pydantic 模型**：

```python
class ScopeParseLLMOutput(BaseModel):
    boiler: str | None = None
    device_name: str | None = None
    piperow_name: str | None = None
    row_no: int | None = None
    tube_no: int | None = None
```

### 6.5 时间与 LLM 的分工

按需求 **「时间的解析先保持原来的逻辑」**：

- **生产推荐**：`resolve_question_intent` 中 `time_window` **始终**由 `extract_time_window_from_question` 计算；
- LLM 模板中的时间规则仅帮助模型理解问句，**不以 LLM 输出覆盖时间窗**；
- 若未来需要 LLM 时间解析，另开 `NL2SQL_TIME_PARSE_MODE`，与本方案解耦。

---

## 7. 上层消费与可观测性（Phase 4 — 可选）

### 7.1 NL2SQL 主链路

在 `generate_sql` 构建 Prompt 时，可选追加一段（环境变量 `NL2SQL_INJECT_PARSED_INTENT=true`）：

```text
【已识别问句意图】
- 时间窗：yesterday（2026-06-15 00:00:00 ~ 2026-06-16 00:00:00）
- 锅炉：1号锅炉
- 受热面：低温过热器
- 管排：第一层；排数：1；管数：1
```

辅助 LLM 生成 SQL，**不替代**规则改写。

### 7.2 API 响应

`NL2SQLQueryResponse` 可选增加字段：

```python
parsed_intent: dict[str, Any] | None = Field(default=None, description="调试：结构化问句意图")
```

默认不返回或仅 debug 模式返回，避免增大响应体。

### 7.3 综合分析 trace

在 `AnalysisGraphRunner` acquire_data 日志 / trace 中记录：

```json
{
  "plan_item_id": "q1",
  "question_intent": { "...": "..." }
}
```

便于超温报告排障与后续 plan 细粒度过滤。

### 7.4 超温 synthesis

`infer_overheat_report_context` 短期**不改**；待单管段超温报告产品化后，可读取 `QuestionScopeIntent` 填充报告上下文。

---

## 8. 测试策略

### 8.1 新增测试文件

`tests/test_question_scope_parser.py`

- 6 条验收用例（§4.4）；
- 简称展开、全厂、仅受热面、水冷壁默认排数；
- `time_intent_source` 防污染用例；
- LLM 模式：mock `VLLMHttpClient`，验证 JSON 解析与 fallback。

### 8.2 回归测试

| 套件 | 目的 |
|------|------|
| `tests/test_nl2sql_chain_tidb.py` | 锅炉/时间 SQL 改写零回归 |
| `tests/test_time_intent_p0_p1.py` | 时间窗 P0/P1 零回归 |
| 超温相关集成测试（若有） | end-to-end plan 取数 |

### 8.3 手工验证清单

- [ ] `NL2SQL_INTENT_PARSE_MODE=rule`，6 示例问句解析 JSON 正确；
- [ ] 超温 `run-with-nl2sql`：仅「1号锅炉昨天超温」报告正常；
- [ ] plan 长问句 + 用户原句 2 号机组，SQL 中 boiler 仍为 2 号锅炉；
- [ ] `NL2SQL_INTENT_PARSE_MODE=llm`，vLLM 可达时解析正确；
- [ ] LLM 超时 → `rule_with_llm_fallback` 回退 rule；
- [ ] Phase 2 占位符开启后，单管段 plan SQL 条件正确（若已接入）。

---

## 9. 风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| 受热面与管排名称边界模糊 | 误拆「水冷壁前墙垂直段」 | 词典最长匹配；device 命中后剥离再解析管排 |
| 台账 `piperow_name` 与口语不一致 | SQL 0 行 | `LIKE` 模糊匹配；前屏/第一屏 OR；Phase 2 才改写 |
| LLM 延迟 | NL2SQL P99 上升 | 默认 `rule`；LLM 仅 scope；超时 fallback |
| LLM JSON 不稳定 | 解析失败 | Pydantic 校验 + fallback；禁止 LLM 覆盖时间窗 |
| 误解析收窄超温全厂汇总 | 报告数据变少 | Phase 1 不改 SQL；Phase 2 占位符 opt-in；null 不注入条件 |
| plan 问句污染 scope | 2 号机组被替成 1 号锅炉 | 强制 `time_intent_source` / `_resolve_entity_scope_question` |
| QA 指纹变更 | slot replay miss | 本改造不改变 SQL 模板指纹；若新增占位符需同步 QA 文档 |

---

## 10. 实施阶段与工时估算

| 阶段 | 交付物 | 预估工时 | 上线策略 |
|------|--------|----------|----------|
| **P1** | `question_intent.py`、`scope_parser_rule.py`、`scope_lexicon.py`、词典 JSON、单测、chain 兼容层 | 3–4 人日 | **可独立上线**；只解析，不改 SQL |
| **P2** | 占位符改写、`NL2SQL_SCOPE_SQL_REWRITE_ENABLED`、集成测试 | 2 人日 | 默认 false；单场景灰度 |
| **P3** | `scope_parser_llm.py`、`nl2sql_scope_parse` 提示词、env、fallback | 2–3 人日 | 默认 rule；LLM 内测 |
| **P4** | trace/API 暴露、Prompt 注入、文档 | 1 人日 | 可选 |

**推荐路径**：P1 全量测试通过后合并主分支 → 超温回归 → 再排 P3 LLM → 有单管段查询需求时开 P2。

---

## 11. 模块与文件清单（实施 Checklist）

### 11.1 新增

- [ ] `app/nl2sql/question_intent.py`
- [ ] `app/nl2sql/scope_parser_rule.py`
- [ ] `app/nl2sql/scope_lexicon.py`
- [ ] `app/nl2sql/scope_parser_llm.py`（P3）
- [ ] `configs/nl2sql_scope_device_aliases.json`
- [ ] `tests/test_question_scope_parser.py`

### 11.2 修改

- [ ] `app/nl2sql/chain.py` — `_extract_scope_literals_from_question`、Phase 2 改写
- [ ] `configs/prompts.yaml` — 新增 `nl2sql_scope_parse`（P3）
- [ ] `app/core/config.py` — 新环境变量
- [ ] `app/app-deploy/.env.example` — 配置说明
- [ ] `app/models/nl2sql.py` — 可选 `parsed_intent`（P4）

### 11.3 不变

- `app/nl2sql/time_intent_display.py` — 时间规则
- `app/nl2sql/sql_skeleton.py` — L1 缓存时间骨架
- `app/llm/graphs/overheat_synthesis_render.py` — 短期不改（P4 可选）

---

## 12. 附录：与现有代码的关键衔接点

### 12.1 时间改写调用链

```
NL2SQLService.query(time_intent_text=req.query)
  → NL2SQLChain.generate_sql_with_validation_context(..., time_intent_text=...)
  → _rewrite_query_filters(question, time_intent_source=time_src)
  → _resolve_time_window_for_rewrite
  → _rewrite_entity_scope_literals  ← scope 扩展点
```

### 12.2 锅炉解析（禁止重写）

```python
# app/nl2sql/chain.py
_extract_boiler_scope_label_from_question  # 单机组 → 「N号锅炉」
_extract_unit_keyword_from_question        # None = 全厂
_has_explicit_full_plant_scope             # 显式全厂
_resolve_entity_scope_question             # 防 plan/RAG 污染
```

### 12.3 检修模块可复用规则

```python
# app/inspection_v2/record_normalization.py
_WALL_ROW1_MARKERS      # 水冷壁系 → 行号/排数默认 1
_REHEATER_TUBE1_MARKERS  # 过热器系管号默认（本方案 tube_no 按需参考）
```

---

## 13. 修订记录

| 日期 | 版本 | 说明 |
|------|------|------|
| 2026-06-16 | v1.0 | 初稿：范围解析 + 双模式 + SQL 改写分阶段 + 超温兼容 |

---

**评审通过后**，建议先实施 **Phase 1** 并提交 `tests/test_question_scope_parser.py` 与 `tests/test_nl2sql_chain_tidb.py` 全绿 PR，再评估 Phase 2/3 排期。
