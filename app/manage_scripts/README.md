# Manage Scripts

该目录用于放置**管理类脚本**（非测试脚本）。  
后续所有运维/治理脚本统一放在 `app/manage_scripts`。

## 这个目录脚本的定位

- 目标：把“人工逐条调用管理 API”的运维动作改为“可批量、可重复、可留痕”的自动执行。
- 典型对象：实施、运维、平台管理员。
- 典型场景：
  - 一次性批量上库/重灌文档；
  - 按固定顺序执行文档生命周期动作（例如先 upsert 再 delete）；
  - 通过报告追踪每次执行结果（成功/失败/错误原因）。

> 注意：这里的脚本是**管理执行工具**，不是测试验证脚本。  
> 测试链路验证请使用 `app/test_scripts` 下的 E2E 脚本。

## 当前脚本

### `rag_doc_lifecycle_admin.py`

用途：
- 读取一个计划文件（JSON），按顺序执行动作：
  - `upsert`：调用 `/rag/jobs/ingest` 并轮询任务状态；
  - `delete`：调用 `/rag/documents/delete`。

能力：
- `--dry-run`：只做计划校验，不调用 API；
- `--fail-fast`：遇到首个失败动作立即停止；
- `--timeout-s`：异步任务轮询超时控制；
- `--report-out`：输出 JSON 执行报告（便于审计与流水线存档）。

依赖接口：
- `/rag/jobs/ingest`
- `/rag/jobs/{job_id}`
- `/rag/documents/delete`

## 计划文件格式

示例文件：`app/manage_scripts/examples/rag_doc_lifecycle_plan.json`

核心结构：

```json
{
  "actions": [
    {
      "operation": "upsert",
      "dataset_id": "xxx",
      "doc_name": "xxx",
      "doc_version": "v1",
      "namespace": "xxx",
      "content": "..."
    },
    {
      "operation": "delete",
      "doc_name": "xxx",
      "namespace": "xxx"
    }
  ]
}
```

## 运行示例

执行管理动作并输出报告：

```bash
python app/manage_scripts/rag_doc_lifecycle_admin.py --base-url http://127.0.0.1:8000 --plan-file app/manage_scripts/examples/rag_doc_lifecycle_plan.json --report-out app/manage_scripts/output/admin_report.json
```

仅做计划校验（不执行）：

```bash
python app/manage_scripts/rag_doc_lifecycle_admin.py --base-url http://127.0.0.1:8000 --plan-file app/manage_scripts/examples/rag_doc_lifecycle_plan.json --dry-run
```
