# 综合分析智能体 · 报告规格（唯一事实源）

运行时由 `app/analysis_agent/report_spec.py` → `load_report_spec()` 加载本目录 JSON。

## 文件命名

```
{analysis_type}.{version}.json
```

示例：`overheat_guidance.v1.json`

## 结构

- `chapters[]`：章节列表（`static_markdown` / `llm_section`）
- 可选 `plan.items[]`：内嵌 NL2SQL 数据计划；未内嵌时回退 `prompts.yaml` 的 `analysis_agent_plan_{type}`

## 相关配置（不在此目录）

| 用途 | 位置 |
|------|------|
| NL2SQL 数据计划 | `configs/prompts.yaml` → `analysis_agent_plan_*` |
| 章节合成 system | `configs/prompts.yaml` → `analysis_agent_synthesis_*` |
| NL2SQL SQL 生成 | `configs/prompts.yaml` → `nl2sql`（公共 scene） |

## 维护说明

- **勿**再将 report JSON 合并进 `prompts.yaml`（已移除 `analysis_agent_report_*` 块）
- 修改章节结构、outline、constraints 时只编辑本目录 JSON
