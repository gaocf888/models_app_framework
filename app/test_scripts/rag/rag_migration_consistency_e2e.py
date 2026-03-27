from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import traceback
from typing import Any, Iterable


def _assert_ok(resp: Any, step: str) -> dict[str, Any]:
    if resp.status_code >= 400:
        raise RuntimeError(f"{step} failed: status={resp.status_code}, body={resp.text}")
    data = resp.json()
    if not data.get("ok", False):
        raise RuntimeError(f"{step} failed: {json.dumps(data, ensure_ascii=False)}")
    return data


def _normalize(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _overlap_ratio(before: Iterable[str], after: Iterable[str]) -> float:
    a = {_normalize(x) for x in before if _normalize(x)}
    b = {_normalize(x) for x in after if _normalize(x)}
    if not a:
        return 0.0
    return len(a.intersection(b)) / len(a)


def _load_cases(cases_file: str | None) -> dict[str, Any]:
    default_path = Path(__file__).with_name("migration_consistency_cases.json")
    target = Path(cases_file) if cases_file else default_path
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("cases file must be a JSON object")
    if not isinstance(payload.get("namespace"), str) or not payload.get("namespace"):
        raise ValueError("cases file must contain non-empty 'namespace'")
    if not isinstance(payload.get("dataset_id"), str) or not payload.get("dataset_id"):
        raise ValueError("cases file must contain non-empty 'dataset_id'")
    if not isinstance(payload.get("documents"), list) or not payload.get("documents"):
        raise ValueError("cases file must contain non-empty 'documents' array")
    if not isinstance(payload.get("queries"), list) or not payload.get("queries"):
        raise ValueError("cases file must contain non-empty 'queries' array")
    return payload


def _parse_query_case(item: Any, default_top_k: int) -> dict[str, Any]:
    if isinstance(item, str):
        return {"query": item, "scene": "llm_inference", "top_k": default_top_k}
    if not isinstance(item, dict):
        raise ValueError("query case must be string or object")
    query = item.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query case object must contain non-empty 'query'")
    scene = item.get("scene") or "llm_inference"
    case_top_k = int(item.get("top_k") or default_top_k)
    return {"query": query, "scene": scene, "top_k": case_top_k}


def _finalize_summary(report: dict[str, Any]) -> None:
    results = report.get("query_results") or []
    report["summary"] = {
        "total_checks": len(results),
        "failed_checks": len([x for x in results if not x.get("passed")]),
    }


def _write_reports(report: dict[str, Any], report_out: str | None, report_md_out: str | None) -> None:
    if report_out:
        output = Path(report_out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] report written: {output}")
    if report_md_out:
        md_output = Path(report_md_out)
        md_output.parent.mkdir(parents=True, exist_ok=True)
        md_output.write_text(_format_markdown_report(report), encoding="utf-8")
        print(f"[OK] markdown report written: {md_output}")


def _format_markdown_report(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    status = report.get("status", "unknown")
    lines = [
        "# RAG Migration Consistency Report",
        "",
        f"- **Status**: `{status}`",
        f"- Namespace: `{report.get('namespace')}`",
        f"- Dataset: `{report.get('dataset_id')}`",
        f"- Threshold: `{report.get('threshold')}`",
        f"- Migration Dim: `{report.get('migration_dim')}`",
        f"- Total Checks: `{summary.get('total_checks', 0)}`",
        f"- Failed Checks: `{summary.get('failed_checks', 0)}`",
        "",
    ]
    mig = report.get("migration")
    if isinstance(mig, dict) and mig:
        lines.extend(
            [
                "## Migration",
                "",
                f"- New Index: `{mig.get('new_index')}`",
                f"- Old Indices: `{mig.get('old_indices')}`",
                "",
            ]
        )
    if status == "failed":
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

    lines.extend(
        [
            "## Check Results",
            "",
            "| Phase | Scene | Query | Ratio | Passed |",
            "| --- | --- | --- | ---: | :---: |",
        ]
    )
    for item in report.get("query_results") or []:
        phase = item.get("phase", "")
        scene = item.get("scene", "")
        query = str(item.get("query", "")).replace("|", "\\|")
        ratio = float(item.get("ratio", 0.0))
        passed = "Y" if item.get("passed") else "N"
        lines.append(f"| {phase} | {scene} | {query} | {ratio:.4f} | {passed} |")
    return "\n".join(lines) + "\n"


def run(
    base_url: str,
    migration_dim: int = 512,
    threshold: float = 0.6,
    top_k: int = 4,
    cases_file: str | None = None,
    report_out: str | None = None,
    report_md_out: str | None = None,
) -> None:
    import httpx  # 延迟导入，避免仅查看 CLI 帮助时受环境依赖影响

    report: dict[str, Any] = {
        "namespace": None,
        "dataset_id": None,
        "threshold": threshold,
        "migration_dim": migration_dim,
        "query_results": [],
        "status": "running",
        "migration": None,
    }

    try:
        base = base_url.rstrip("/")
        report["failed_phase"] = "load_cases"
        payload = _load_cases(cases_file)
        ns = payload["namespace"]
        dataset_id = payload["dataset_id"]
        docs = payload["documents"]
        queries = [_parse_query_case(q, top_k) for q in payload["queries"]]
        report["namespace"] = ns
        report["dataset_id"] = dataset_id
        print(f"[INFO] loaded migration cases: docs={len(docs)}, queries={len(queries)}, namespace={ns}")

        with httpx.Client(timeout=60) as client:
            # 0) cleanup
            report["failed_phase"] = "cleanup_before"
            for d in docs:
                doc_name = d.get("doc_name")
                if not doc_name:
                    raise ValueError("each document in cases file must contain non-empty 'doc_name'")
                client.post(
                    f"{base}/rag/documents/delete",
                    json={"doc_name": doc_name, "namespace": ns},
                )

            # 1) ingest baseline docs
            report["failed_phase"] = "ingest_baseline"
            for i, doc in enumerate(docs, start=1):
                texts = doc.get("texts") or []
                if not isinstance(texts, list) or not texts:
                    raise ValueError(f"document '{doc.get('doc_name')}' must contain non-empty 'texts' array")
                ingest_payload = {
                    "dataset_id": doc.get("dataset_id") or dataset_id,
                    "namespace": doc.get("namespace") or ns,
                    "doc_name": doc.get("doc_name"),
                    "replace_if_exists": bool(doc.get("replace_if_exists", True)),
                    "texts": texts,
                }
                _assert_ok(client.post(f"{base}/rag/ingest/texts", json=ingest_payload), f"ingest_{i}")
            print("[OK] ingest baseline")

            # 2) baseline query
            report["failed_phase"] = "baseline_query"
            baseline: dict[str, list[str]] = {}
            for q_case in queries:
                q = q_case["query"]
                scene = q_case["scene"]
                case_top_k = q_case["top_k"]
                data = _assert_ok(
                    client.post(
                        f"{base}/rag/query",
                        json={"query": q, "namespace": ns, "scene": scene, "top_k": case_top_k},
                    ),
                    f"baseline_query:{scene}:{q}",
                )
                snippets = data.get("snippets") or []
                if not snippets:
                    raise RuntimeError(f"baseline query empty: {q}")
                baseline[f"{scene}::{q}"] = snippets
            print("[OK] baseline query")

            # 3) migration run
            report["failed_phase"] = "migration_run"
            mig = _assert_ok(
                client.post(f"{base}/rag/migrations/chunks/run", json={"embedding_dim": migration_dim}),
                "migration_run",
            )
            old_indices = mig.get("old_indices") or []
            new_index = mig.get("new_index")
            if not new_index:
                raise RuntimeError("migration_run failed: missing new_index")
            report["migration"] = {"old_indices": old_indices, "new_index": new_index}
            print("[OK] migration run")

            # 4) query after migration and compare overlap ratio
            report["failed_phase"] = "after_migration"
            for q_case in queries:
                q = q_case["query"]
                scene = q_case["scene"]
                case_top_k = q_case["top_k"]
                data = _assert_ok(
                    client.post(
                        f"{base}/rag/query",
                        json={"query": q, "namespace": ns, "scene": scene, "top_k": case_top_k},
                    ),
                    f"after_migration_query:{scene}:{q}",
                )
                after_snippets = data.get("snippets") or []
                ratio = _overlap_ratio(baseline[f"{scene}::{q}"], after_snippets)
                print(f"[INFO] query overlap ratio: scene='{scene}' query='{q}' => {ratio:.2f}")
                report["query_results"].append(
                    {
                        "query": q,
                        "scene": scene,
                        "phase": "after_migration",
                        "ratio": ratio,
                        "passed": ratio >= threshold,
                    }
                )
                if ratio < threshold:
                    raise RuntimeError(
                        f"migration consistency check failed: scene='{scene}', query='{q}', "
                        f"ratio={ratio:.2f}, threshold={threshold:.2f}"
                    )
            print("[OK] migration consistency check")

            # 5) rollback to old index (if exists), validate and restore
            if old_indices:
                report["failed_phase"] = "rollback"
                rollback_target = old_indices[0]
                _assert_ok(
                    client.post(
                        f"{base}/rag/migrations/chunks/rollback",
                        json={"previous_index": rollback_target},
                    ),
                    "migration_rollback",
                )
                time.sleep(0.2)
                report["failed_phase"] = "after_rollback"
                for q_case in queries:
                    q = q_case["query"]
                    scene = q_case["scene"]
                    case_top_k = q_case["top_k"]
                    data = _assert_ok(
                        client.post(
                            f"{base}/rag/query",
                            json={"query": q, "namespace": ns, "scene": scene, "top_k": case_top_k},
                        ),
                        f"after_rollback_query:{scene}:{q}",
                    )
                    rollback_snippets = data.get("snippets") or []
                    ratio = _overlap_ratio(baseline[f"{scene}::{q}"], rollback_snippets)
                    report["query_results"].append(
                        {
                            "query": q,
                            "scene": scene,
                            "phase": "after_rollback",
                            "ratio": ratio,
                            "passed": ratio >= threshold,
                        }
                    )
                    if ratio < threshold:
                        raise RuntimeError(
                            f"rollback consistency check failed: scene='{scene}', query='{q}', "
                            f"ratio={ratio:.2f}, threshold={threshold:.2f}"
                        )
                print("[OK] rollback consistency check")

                report["failed_phase"] = "migration_restore"
                _assert_ok(
                    client.post(
                        f"{base}/rag/migrations/chunks/rollback",
                        json={"previous_index": new_index},
                    ),
                    "migration_restore",
                )
                print("[OK] migration restore")

            # 6) cleanup
            report["failed_phase"] = "cleanup_after"
            for d in docs:
                _assert_ok(
                    client.post(
                        f"{base}/rag/documents/delete",
                        json={"doc_name": d["doc_name"], "namespace": ns},
                    ),
                    f"cleanup_delete:{d['doc_name']}",
                )
            print("[OK] cleanup")

        print("RAG migration consistency E2E passed.")
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
    parser = argparse.ArgumentParser(description="RAG migration consistency E2E script")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Service base URL")
    parser.add_argument("--migration-dim", type=int, default=512, help="Embedding dimension for migration run")
    parser.add_argument(
        "--consistency-threshold",
        type=float,
        default=0.6,
        help="Minimum overlap ratio required between baseline and post-migration snippets",
    )
    parser.add_argument("--top-k", type=int, default=4, help="Top K snippets per query")
    parser.add_argument(
        "--cases-file",
        default=None,
        help="Path to migration consistency cases json; default uses app/test_scripts/rag/migration_consistency_cases.json",
    )
    parser.add_argument("--report-out", default=None, help="Optional path to write json report for CI gate")
    parser.add_argument("--report-md-out", default=None, help="Optional path to write markdown report summary")
    args = parser.parse_args()
    try:
        run(
            base_url=args.base_url,
            migration_dim=args.migration_dim,
            threshold=args.consistency_threshold,
            top_k=args.top_k,
            cases_file=args.cases_file,
            report_out=args.report_out,
            report_md_out=args.report_md_out,
        )
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"[FAILED] {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
