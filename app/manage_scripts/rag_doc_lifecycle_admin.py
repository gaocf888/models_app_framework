from __future__ import annotations

"""
RAG 文档生命周期管理脚本（运维工具）。

定位说明：
- 这是“管理执行脚本”，用于批量执行运维动作（upsert/delete）；
- 不是“测试脚本”，不负责做检索效果断言或一致性门禁判定。

典型用途：
- 按计划文件批量上库/重灌文档；
- 批量删除文档；
- 产出执行报告用于审计与问题排查。
"""

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any


def _assert_ok(resp: Any, step: str) -> dict[str, Any]:
    if resp.status_code >= 400:
        raise RuntimeError(f"{step} failed: status={resp.status_code}, body={resp.text}")
    data = resp.json()
    if not data.get("ok", False):
        raise RuntimeError(f"{step} failed: {json.dumps(data, ensure_ascii=False)}")
    return data


def _wait_job(client: Any, base: str, job_id: str, timeout_s: float) -> dict[str, Any]:
    start = time.time()
    while True:
        data = _assert_ok(client.get(f"{base}/rag/jobs/{job_id}"), "job_status")
        job = data.get("job") or {}
        status = (job.get("status") or "").upper()
        if status in {"SUCCESS", "FAILED", "PARTIAL"}:
            return job
        if time.time() - start > timeout_s:
            raise RuntimeError(f"job timeout: job_id={job_id}, last_status={status}")
        time.sleep(0.3)


def _load_plan(plan_file: str) -> dict[str, Any]:
    # 管理脚本以“计划文件驱动”执行，便于重复运行与变更审计。
    payload = json.loads(Path(plan_file).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("plan file must be a JSON object")
    actions = payload.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError("plan file must contain non-empty 'actions' array")
    return payload


def _run_upsert(client: Any, base: str, action: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    # upsert 使用异步 jobs 接口，脚本在此统一轮询到终态，确保动作有明确结果。
    required = ["dataset_id", "doc_name", "namespace", "content"]
    for key in required:
        if not action.get(key):
            raise ValueError(f"upsert action missing required field: {key}")
    payload = {
        "operator": action.get("operator") or "rag-doc-lifecycle-admin",
        "chunk_size": int(action.get("chunk_size") or 80),
        "chunk_overlap": int(action.get("chunk_overlap") or 20),
        "min_chunk_size": int(action.get("min_chunk_size") or 20),
        "idempotency_key": action.get("idempotency_key"),
        "documents": [
            {
                "dataset_id": action["dataset_id"],
                "doc_name": action["doc_name"],
                "doc_version": action.get("doc_version") or "v1",
                "tenant_id": action.get("tenant_id"),
                "namespace": action["namespace"],
                "content": action["content"],
                "source_type": action.get("source_type") or "text",
                "source_uri": action.get("source_uri"),
                "description": action.get("description"),
                "replace_if_exists": bool(action.get("replace_if_exists", True)),
                "metadata": action.get("metadata") or {},
            }
        ],
    }
    submit = _assert_ok(client.post(f"{base}/rag/jobs/ingest", json=payload), "job_submit")
    job_id = submit.get("job_id")
    if not job_id:
        raise RuntimeError("job_submit failed: missing job_id")
    job = _wait_job(client, base, job_id=job_id, timeout_s=timeout_s)
    return {"job_id": job_id, "status": job.get("status"), "job": job}


def _run_delete(client: Any, base: str, action: dict[str, Any]) -> dict[str, Any]:
    # delete 直接调用管理接口，返回删除条数供执行报告记录。
    doc_name = action.get("doc_name")
    namespace = action.get("namespace")
    if not doc_name:
        raise ValueError("delete action missing required field: doc_name")
    data = _assert_ok(
        client.post(
            f"{base}/rag/documents/delete",
            json={"doc_name": doc_name, "namespace": namespace},
        ),
        "delete_document",
    )
    return {"deleted": int(data.get("deleted", 0))}


def run(
    base_url: str,
    plan_file: str,
    dry_run: bool = False,
    fail_fast: bool = False,
    timeout_s: float = 30.0,
    report_out: str | None = None,
) -> dict[str, Any]:
    """
    执行管理计划并返回结构化执行报告。

    报告语义：
    - status=success: 所有 action 执行成功；
    - status=failed: 至少一个 action 执行失败（或 fail-fast 中止）。
    """
    import httpx

    base = base_url.rstrip("/")
    plan = _load_plan(plan_file)
    actions = plan.get("actions") or []
    report: dict[str, Any] = {
        "status": "success",
        "base_url": base,
        "plan_file": plan_file,
        "dry_run": dry_run,
        "actions_total": len(actions),
        "actions": [],
    }
    if dry_run:
        # dry-run 只验证计划可解析，不触发任何外部写操作。
        for idx, action in enumerate(actions, start=1):
            report["actions"].append(
                {"index": idx, "operation": action.get("operation"), "status": "skipped_dry_run", "error": None}
            )
        return report

    with httpx.Client(timeout=60) as client:
        for idx, action in enumerate(actions, start=1):
            op = (action.get("operation") or "").lower().strip()
            row = {"index": idx, "operation": op, "status": "success", "error": None, "result": None}
            try:
                if op == "upsert":
                    row["result"] = _run_upsert(client, base, action, timeout_s=timeout_s)
                    if (row["result"].get("status") or "").upper() != "SUCCESS":
                        raise RuntimeError(f"upsert job not successful: {row['result'].get('status')}")
                elif op == "delete":
                    row["result"] = _run_delete(client, base, action)
                else:
                    raise ValueError(f"unsupported operation: {op}")
            except Exception as e:  # noqa: BLE001
                row["status"] = "failed"
                row["error"] = str(e)
                report["status"] = "failed"
                if fail_fast:
                    report["actions"].append(row)
                    break
            report["actions"].append(row)

    if report_out:
        out = Path(report_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] report written: {out}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG document lifecycle admin script")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Service base URL")
    parser.add_argument("--plan-file", required=True, help="JSON plan file path")
    parser.add_argument("--dry-run", action="store_true", help="Only parse and print plan, do not call APIs")
    parser.add_argument("--fail-fast", action="store_true", help="Stop on first failed action")
    parser.add_argument("--timeout-s", type=float, default=30.0, help="Async job polling timeout seconds")
    parser.add_argument("--report-out", default=None, help="Optional path to write json execution report")
    args = parser.parse_args()
    try:
        result = run(
            base_url=args.base_url,
            plan_file=args.plan_file,
            dry_run=args.dry_run,
            fail_fast=args.fail_fast,
            timeout_s=args.timeout_s,
            report_out=args.report_out,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") == "success" else 1
    except Exception as e:  # noqa: BLE001
        print(f"[FAILED] {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
