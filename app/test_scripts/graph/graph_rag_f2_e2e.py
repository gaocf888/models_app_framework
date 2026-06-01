from __future__ import annotations

"""
GraphRAG F2 联调脚本（计划文档阶段 F2）。

前置条件（应用侧 .env）：
- GRAPH_RAG_ENABLED=true
- NEO4J_* 已配置且 Neo4j 可连通
- 可选 GRAPH_RAG_INGEST_ON_RAG=true（验证 RAG 联动写图）
- 可选 GRAPH_RAG_DELETE_ON_RAG=true（验证 RAG 删文档同步删图）
- vLLM 可用（LLM 抽取模式）

用法：
  set SERVICE_API_KEY=your-key
  python app/test_scripts/graph/graph_rag_f2_e2e.py --base-url http://127.0.0.1:8000
"""

import argparse
import json
import os
import sys
import time
from typing import Any

import httpx


def _headers() -> dict[str, str]:
    key = os.getenv("SERVICE_API_KEY") or os.getenv("SERVICE_API_KEYS", "").split(",")[0].strip()
    if not key:
        raise RuntimeError("请设置环境变量 SERVICE_API_KEY")
    return {"Authorization": f"Bearer {key}"}


def _assert_ok(resp: httpx.Response, step: str) -> dict[str, Any]:
    if resp.status_code >= 400:
        raise RuntimeError(f"{step} failed: status={resp.status_code}, body={resp.text}")
    data = resp.json()
    if isinstance(data, dict) and data.get("ok") is False:
        raise RuntimeError(f"{step} failed: {json.dumps(data, ensure_ascii=False)}")
    return data


def _poll_rebuild_job(client: httpx.Client, base: str, job_id: str, timeout_s: int = 600) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        data = _assert_ok(client.get(f"{base}/graph/jobs/{job_id}", headers=_headers()), f"poll job {job_id}")
        job = data.get("job") or {}
        status = str(job.get("status") or "")
        print(f"[poll] job={job_id} status={status}")
        if status in {"SUCCESS", "FAILED"}:
            if status == "FAILED":
                raise RuntimeError(f"rebuild job failed: {json.dumps(job, ensure_ascii=False)}")
            return job
        time.sleep(2)
    raise RuntimeError(f"rebuild job timeout: {job_id}")


def run(base_url: str, skip_rag_ingest: bool = False) -> None:
    base = base_url.rstrip("/")
    ns = "graph_f2_e2e"
    doc_name = "graph_f2_doc"
    dataset_id = "graph_f2_dataset"

    with httpx.Client(timeout=120, headers=_headers()) as client:
        health = client.get(f"{base}/graph/health")
        health_data = health.json()
        print("[info] graph health:", json.dumps(health_data, ensure_ascii=False))
        if not health_data.get("enabled"):
            print("[SKIP] GRAPH_RAG_ENABLED=false，跳过 Graph F2 联调")
            return

        # 清理
        client.delete(f"{base}/graph/documents/{doc_name}", params={"namespace": ns})
        client.post(f"{base}/rag/documents/delete", json={"doc_name": doc_name, "namespace": ns})

        if not skip_rag_ingest:
            doc_content = "\n\n".join(
                [
                    "锅炉过热是火电厂常见故障之一。",
                    "过热会导致管壁损伤，需要按规程降温处理。",
                ]
            )
            ingest_payload = {
                "documents": [
                    {
                        "dataset_id": dataset_id,
                        "namespace": ns,
                        "doc_name": doc_name,
                        "replace_if_exists": True,
                        "source_type": "text",
                        "content": doc_content,
                    }
                ]
            }
            job_resp = _assert_ok(client.post(f"{base}/rag/jobs/ingest", json=ingest_payload), "rag ingest job")
            rag_job_id = job_resp.get("job_id")
            print(f"[OK] rag ingest submitted job_id={rag_job_id} (若 GRAPH_RAG_INGEST_ON_RAG=true 将联动写图)")
            if rag_job_id:
                deadline = time.time() + 300
                while time.time() < deadline:
                    st = _assert_ok(client.get(f"{base}/rag/jobs/{rag_job_id}"), "rag job poll")
                    status = str((st.get("job") or {}).get("status") or "")
                    if status in {"SUCCESS", "FAILED", "PARTIAL"}:
                        if status == "FAILED":
                            raise RuntimeError(f"rag ingest failed: {json.dumps(st, ensure_ascii=False)}")
                        break
                    time.sleep(2)

        rebuild_payload = {
            "mode": "incremental",
            "namespace": ns,
            "doc_names": [doc_name],
            "async_mode": True,
        }
        rebuild_resp = _assert_ok(client.post(f"{base}/graph/rebuild", json=rebuild_payload), "graph rebuild async")
        job_id = rebuild_resp.get("job_id")
        if not job_id:
            raise RuntimeError("async rebuild missing job_id")
        job = _poll_rebuild_job(client, base, job_id)
        print("[OK] graph async rebuild:", json.dumps(job.get("result") or {}, ensure_ascii=False))

        stats = _assert_ok(client.get(f"{base}/graph/stats", params={"namespace": ns}), "graph stats")
        print("[OK] graph stats:", json.dumps(stats.get("stats") or {}, ensure_ascii=False))

        debug = _assert_ok(
            client.post(f"{base}/graph/query/debug", json={"question": "锅炉过热", "namespace": ns}),
            "graph debug query",
        )
        facts = (debug.get("result") or {}).get("facts") or []
        print(f"[OK] graph debug query facts={len(facts)}")

        _assert_ok(client.post(f"{base}/rag/documents/delete", json={"doc_name": doc_name, "namespace": ns}), "rag delete")
        print("[OK] rag delete (若 GRAPH_RAG_DELETE_ON_RAG=true 将同步删图)")

    print("[DONE] GraphRAG F2 e2e finished")


def main() -> int:
    parser = argparse.ArgumentParser(description="GraphRAG F2 integration e2e")
    parser.add_argument("--base-url", default=os.getenv("APP_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--skip-rag-ingest", action="store_true", help="跳过 RAG 摄入，仅测 rebuild/stats")
    args = parser.parse_args()
    try:
        run(args.base_url, skip_rag_ingest=args.skip_rag_ingest)
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
