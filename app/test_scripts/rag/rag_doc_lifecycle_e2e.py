from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import traceback
from typing import Any


def _assert_ok(resp: Any, step: str) -> dict[str, Any]:
    if resp.status_code >= 400:
        raise RuntimeError(f"{step} failed: status={resp.status_code}, body={resp.text}")
    data = resp.json()
    if not data.get("ok", False):
        raise RuntimeError(f"{step} failed: {json.dumps(data, ensure_ascii=False)}")
    return data


def _wait_job_success(client: Any, base: str, job_id: str, timeout_s: float = 20.0) -> dict[str, Any]:
    start = time.time()
    while True:
        data = _assert_ok(client.get(f"{base}/rag/jobs/{job_id}"), "job_status")
        job = data.get("job") or {}
        status = (job.get("status") or "").upper()
        if status in {"SUCCESS", "FAILED", "PARTIAL"}:
            if status != "SUCCESS":
                raise RuntimeError(f"job not successful: status={status}, job={json.dumps(job, ensure_ascii=False)}")
            return job
        if time.time() - start > timeout_s:
            raise RuntimeError(f"job polling timeout: job_id={job_id}, last_status={status}")
        time.sleep(0.3)


def _submit_single_doc_job(
    client: Any,
    base: str,
    dataset_id: str,
    doc_name: str,
    doc_version: str,
    namespace: str,
    content: str,
) -> str:
    payload = {
        "operator": "rag-doc-lifecycle-e2e",
        "chunk_size": 80,
        "chunk_overlap": 20,
        "min_chunk_size": 20,
        "documents": [
            {
                "dataset_id": dataset_id,
                "doc_name": doc_name,
                "doc_version": doc_version,
                "tenant_id": "e2e_tenant",
                "namespace": namespace,
                "content": content,
                "source_type": "text",
                "replace_if_exists": True,
                "metadata": {"case": "doc_lifecycle_e2e", "doc_version": doc_version},
            }
        ],
    }
    data = _assert_ok(client.post(f"{base}/rag/jobs/ingest", json=payload), "job_submit")
    job_id = data.get("job_id")
    if not job_id:
        raise RuntimeError("job_submit failed: missing job_id")
    return job_id


def _query_count(client: Any, base: str, query: str, namespace: str) -> int:
    data = _assert_ok(
        client.post(
            f"{base}/rag/query",
            json={"query": query, "namespace": namespace, "scene": "llm_inference"},
        ),
        "query",
    )
    return int(data.get("count", 0))


def _finalize_summary(report: dict[str, Any]) -> None:
    checks = report.get("checks") or []
    report["summary"] = {
        "total_checks": len(checks),
        "failed_checks": len([x for x in checks if not x.get("passed")]),
    }


def _format_markdown_report(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# RAG Document Lifecycle E2E Report",
        "",
        f"- **Status**: `{report.get('status', 'unknown')}`",
        f"- Namespace: `{report.get('namespace')}`",
        f"- Dataset: `{report.get('dataset_id')}`",
        f"- Doc Name: `{report.get('doc_name')}`",
        f"- Total Checks: `{summary.get('total_checks', 0)}`",
        f"- Failed Checks: `{summary.get('failed_checks', 0)}`",
        "",
    ]
    if report.get("status") == "failed":
        lines.extend(
            [
                "## Failure",
                "",
                f"- Phase: `{report.get('failed_phase', 'unknown')}`",
                f"- Error Type: `{report.get('error_type', '')}`",
                "",
                "```",
                str(report.get("error", "")),
                "```",
                "",
            ]
        )
        tb = report.get("traceback")
        if tb:
            lines.extend(["### Traceback", "", "```", str(tb), "```", ""])

    lines.extend(["## Checks", "", "| Check | Passed | Detail |", "| --- | :---: | --- |"])
    for item in report.get("checks") or []:
        name = str(item.get("name", "")).replace("|", "\\|")
        passed = "Y" if item.get("passed") else "N"
        detail = str(item.get("detail", "")).replace("|", "\\|")
        lines.append(f"| {name} | {passed} | {detail} |")
    return "\n".join(lines) + "\n"


def _write_reports(report: dict[str, Any], report_out: str | None, report_md_out: str | None) -> None:
    if report_out:
        out = Path(report_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] report written: {out}")
    if report_md_out:
        md_out = Path(report_md_out)
        md_out.parent.mkdir(parents=True, exist_ok=True)
        md_out.write_text(_format_markdown_report(report), encoding="utf-8")
        print(f"[OK] markdown report written: {md_out}")


def _record(report: dict[str, Any], name: str, passed: bool, detail: str) -> None:
    report["checks"].append({"name": name, "passed": bool(passed), "detail": detail})


def run(base_url: str, report_out: str | None = None, report_md_out: str | None = None) -> None:
    import httpx

    report: dict[str, Any] = {
        "status": "running",
        "namespace": None,
        "dataset_id": None,
        "doc_name": None,
        "checks": [],
    }

    base = base_url.rstrip("/")
    ns = "test_rag_doc_lifecycle"
    doc_name = "doc_lifecycle_demo"
    dataset_id = "ds_lifecycle_demo"
    v1_marker = "版本一特征词：AlphaLifecycleV1"
    v2_marker = "版本二特征词：BetaLifecycleV2"
    report["namespace"] = ns
    report["dataset_id"] = dataset_id
    report["doc_name"] = doc_name

    try:
        with httpx.Client(timeout=60) as client:
            # 0) 清理历史数据（向量侧 + 图侧同步删除）
            report["failed_phase"] = "cleanup_before"
            _assert_ok(
                client.post(
                    f"{base}/rag/documents/delete",
                    json={"doc_name": doc_name, "namespace": ns},
                ),
                "cleanup_delete",
            )
            _record(report, "cleanup_before", True, "cleanup request accepted")
            print("[OK] cleanup")

            # 1) 先摄入 v1
            report["failed_phase"] = "ingest_v1"
            job_v1 = _submit_single_doc_job(
                client=client,
                base=base,
                dataset_id=dataset_id,
                doc_name=doc_name,
                doc_version="v1",
                namespace=ns,
                content=f"这是生命周期测试文档 v1。{v1_marker}。",
            )
            _wait_job_success(client, base, job_v1)
            _record(report, "ingest_v1", True, f"job_id={job_v1}")
            print("[OK] ingest v1")

            # 2) 校验 v1 可查
            report["failed_phase"] = "query_v1"
            c1 = _query_count(client, base, v1_marker, ns)
            if c1 <= 0:
                raise RuntimeError("v1 verification failed: marker not found")
            _record(report, "query_v1", True, f"count={c1}")
            print("[OK] query v1")

            # 3) 同 doc_name 摄入 v2（replace_if_exists=true）
            report["failed_phase"] = "ingest_v2"
            job_v2 = _submit_single_doc_job(
                client=client,
                base=base,
                dataset_id=dataset_id,
                doc_name=doc_name,
                doc_version="v2",
                namespace=ns,
                content=f"这是生命周期测试文档 v2。{v2_marker}。",
            )
            _wait_job_success(client, base, job_v2)
            _record(report, "ingest_v2", True, f"job_id={job_v2}")
            print("[OK] ingest v2")

            # 4) 校验 v2 可查，v1 不再命中（向量侧同名重灌生效）
            report["failed_phase"] = "replace_verification"
            c2 = _query_count(client, base, v2_marker, ns)
            c1_after = _query_count(client, base, v1_marker, ns)
            if c2 <= 0:
                raise RuntimeError("v2 verification failed: marker not found")
            if c1_after != 0:
                raise RuntimeError("replacement verification failed: v1 marker still exists")
            _record(report, "replace_verification", True, f"v2_count={c2}, v1_after={c1_after}")
            print("[OK] replace verification")

            # 5) 校验文档元数据中存在 v2 记录
            report["failed_phase"] = "metadata_verification"
            meta_data = _assert_ok(
                client.get(
                    f"{base}/rag/documents/meta",
                    params={"limit": 100, "offset": 0, "namespace": ns},
                ),
                "documents_meta",
            )
            docs = meta_data.get("documents") or []
            v2_ok = any(
                d.get("doc_name") == doc_name
                and d.get("doc_version") == "v2"
                and (d.get("status") or "").upper() == "SUCCESS"
                for d in docs
            )
            if not v2_ok:
                raise RuntimeError("documents_meta verification failed: v2 SUCCESS record not found")
            _record(report, "metadata_verification", True, "v2 SUCCESS record exists")
            print("[OK] metadata verification")

            # 6) 删除并校验查询为空（delete 接口会同步触发图侧清理）
            report["failed_phase"] = "delete_verification"
            del_data = _assert_ok(
                client.post(
                    f"{base}/rag/documents/delete",
                    json={"doc_name": doc_name, "namespace": ns},
                ),
                "delete_document",
            )
            deleted = int(del_data.get("deleted", 0))
            if deleted <= 0:
                raise RuntimeError("delete verification failed: deleted count is 0")
            c2_after = _query_count(client, base, v2_marker, ns)
            if c2_after != 0:
                raise RuntimeError("delete verification failed: snippets still exist")
            _record(report, "delete_verification", True, f"deleted={deleted}, v2_after={c2_after}")
            print("[OK] delete verification")

        print("RAG document lifecycle E2E passed.")
        report["status"] = "success"
        report.pop("failed_phase", None)
    except Exception as e:  # noqa: BLE001
        report["status"] = "failed"
        report["error"] = str(e)
        report["error_type"] = type(e).__name__
        report.setdefault("failed_phase", "unknown")
        report["traceback"] = traceback.format_exc()
        raise
    finally:
        if report.get("status") == "running":
            report["status"] = "failed"
            report.setdefault("error", "exited without marking success")
            report.setdefault("failed_phase", "unknown")
        _finalize_summary(report)
        if report_out or report_md_out:
            _write_reports(report, report_out, report_md_out)


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG document lifecycle end-to-end script")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Service base URL")
    parser.add_argument("--report-out", default=None, help="Optional path to write json report for CI gate")
    parser.add_argument("--report-md-out", default=None, help="Optional path to write markdown report summary")
    args = parser.parse_args()
    try:
        run(args.base_url, report_out=args.report_out, report_md_out=args.report_md_out)
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"[FAILED] {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
