# 综合分析智能体 · 报告规格（唯一事实源）

运行时由 `app/analysis_agent/report_spec.py` → `load_report_spec()` 加载本目录 JSON。

## 文件命名

加载顺序（后者为兜底）：

```
{analysis_type}.{plan_template_version}.json
{analysis_type}.analysis_agent.json
```

- `plan_template_version`：逻辑版本，默认来自环境变量 `ANALYSIS_AGENT_PLAN_TEMPLATE_VERSION`（如 `analysis_agent_v1`），用于 NL2SQL QA 五元组隔离，与现网 `/analysis` 的 `v1`/`v2` 区分。
- `.analysis_agent.json`：无版本号兜底文件，章节与 plan 的默认内容。

示例：

- `overheat_guidance.analysis_agent.json`（兜底）
- `overheat_guidance.analysis_agent_v1.json`（可选，与 env 版本一致时优先）

## 结构

- `schema_version: 2`：锅炉等 V1 规格，直接写 `chapters[]` / 可选 `plan.items[]`
- `schema_version: 3`：地降自动报告，写 `compose[]` 引用 `section_library/`，由 `compose.py` 展开后再走 V1 解析
- 无 `compose` 或版本小于 3：**禁止**走展开器

地降改章节只改 `section_library/*.json` 与五类 `subsidence_*.json` 的 compose 序。

## 前端自动报告契约

`POST /analysis-agent/run-stream`：

- 必传 `analysis_type`（`subsidence_daily|weekly|monthly|quarterly|yearly`）
- `query` 可省略
- `options.start_time` / `end_time` 成对；日期-only 的 end 视为次日 0 点（半开上界）
- `options.area`：`t_station.area` 标准名；空/全市/北京市=不按区过滤
- 首帧 `started.period`：`t_start` / `t_end` / `period_label` / `area`
- 表图：`dual_axis` / `map_placeholder` 用 `chart_type` 区分；设备章 `placeholder: true`

## 相关配置（不在此目录）

| 用途 | 位置 |
|------|------|
| NL2SQL 数据计划 | `configs/prompts.yaml` → `analysis_agent_plan_*` |
| 章节合成 system | `configs/prompts.yaml` → `analysis_agent_synthesis_*` |
| NL2SQL SQL 生成 | `configs/prompts.yaml` → `nl2sql`（公共 scene） |
| 逻辑 plan 版本 | `ANALYSIS_AGENT_PLAN_TEMPLATE_VERSION`（默认 `analysis_agent_v1`） |

## 维护说明

- **勿**再将 report JSON 合并进 `prompts.yaml`（已移除 `analysis_agent_report_*` 块）
- 修改章节结构、outline、constraints 时只编辑本目录 JSON
