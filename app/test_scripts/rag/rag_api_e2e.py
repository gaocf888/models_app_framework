from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

import httpx


def _assert_ok(resp: httpx.Response, step: str) -> dict[str, Any]:
    if resp.status_code >= 400:
        raise RuntimeError(f"{step} failed: status={resp.status_code}, body={resp.text}")
    data = resp.json()
    if not data.get("ok", False):
        raise RuntimeError(f"{step} failed: {json.dumps(data, ensure_ascii=False)}")
    return data


def run(base_url: str, test_migration: bool = False, migration_dim: int = 512) -> None:
    base = base_url.rstrip("/")
    ns = "test_rag_e2e"
    doc_name = "e2e_doc_demo"
    dataset_id = "e2e_dataset_demo"
    raw_doc_name = "e2e_raw_doc_demo"
    raw_dataset_id = "e2e_raw_dataset_demo"
    job_doc_name = "e2e_job_doc_demo"
    job_dataset_id = "e2e_job_dataset_demo"

    with httpx.Client(timeout=60) as client:
        # 0) 清理历史数据，确保脚本可重复执行
        client.post(
            f"{base}/rag/documents/delete",
            json={"doc_name": doc_name, "namespace": ns},
        )
        client.post(
            f"{base}/rag/documents/delete",
            json={"doc_name": raw_doc_name, "namespace": ns},
        )
        client.post(
            f"{base}/rag/documents/delete",
            json={"doc_name": job_doc_name, "namespace": ns},
        )

        # 1) ingest
        ingest_payload = {
            "dataset_id": dataset_id,
            "namespace": ns,
            "doc_name": doc_name,
            "replace_if_exists": True,
            "texts": [
                "苹果公司在 2007 年发布了第一代 iPhone。",
                "iPhone 是由 Apple 设计并销售的智能手机产品线。",
            ],
        }
        _assert_ok(client.post(f"{base}/rag/ingest/texts", json=ingest_payload), "ingest")
        print("[OK] ingest")

        # 2) query
        query_payload = {"query": "谁在 2007 年发布了第一代 iPhone？", "namespace": ns, "scene": "llm_inference"}
        query_data = _assert_ok(client.post(f"{base}/rag/query", json=query_payload), "query")
        if query_data.get("count", 0) <= 0:
            raise RuntimeError("query failed: no snippets returned")
        print("[OK] query")

        # 3) update (same doc_name, replace_if_exists=true)
        update_payload = {
            "dataset_id": dataset_id,
            "namespace": ns,
            "doc_name": doc_name,
            "replace_if_exists": True,
            "texts": [
                "苹果公司在 2010 年发布了 iPad。",
                "iPad 是苹果公司的平板电脑产品线。",
            ],
        }
        _assert_ok(client.post(f"{base}/rag/ingest/texts", json=update_payload), "update")
        print("[OK] update")

        query_updated_payload = {"query": "iPad 是什么产品线？", "namespace": ns, "scene": "llm_inference"}
        query_updated_data = _assert_ok(client.post(f"{base}/rag/query", json=query_updated_payload), "query_after_update")
        snippets = query_updated_data.get("snippets") or []
        if not any("iPad" in s for s in snippets):
            raise RuntimeError("update verification failed: iPad snippet not found")
        print("[OK] update verification")

        # 4) delete
        delete_data = _assert_ok(
            client.post(
                f"{base}/rag/documents/delete",
                json={"doc_name": doc_name, "namespace": ns},
            ),
            "delete",
        )
        if int(delete_data.get("deleted", 0)) <= 0:
            raise RuntimeError("delete verification failed: deleted count is 0")
        print("[OK] delete")

        query_after_delete = _assert_ok(client.post(f"{base}/rag/query", json=query_updated_payload), "query_after_delete")
        if int(query_after_delete.get("count", 0)) != 0:
            raise RuntimeError("delete verification failed: snippets still exist")
        print("[OK] delete verification")

        # 5) raw ingest (server-side normalization + chunking)
        raw_ingest_payload = {
            "dataset_id": raw_dataset_id,
            "doc_name": raw_doc_name,
            "namespace": ns,
            "replace_if_exists": True,
            "content": (
                "RAG 的知识摄入通常包括文档清洗、切块、向量化和索引写入。\n\n"
                "这段文本用于验证 raw_document 接口会自动执行清洗和切块。"
            ),
            "chunk_size": 80,
            "chunk_overlap": 20,
            "min_chunk_size": 20,
        }
        raw_ingest_data = _assert_ok(
            client.post(f"{base}/rag/ingest/raw_document", json=raw_ingest_payload),
            "raw_ingest",
        )
        if int(raw_ingest_data.get("chunk_count", 0)) <= 0:
            raise RuntimeError("raw_ingest verification failed: chunk_count is 0")
        print("[OK] raw ingest")

        raw_query_payload = {"query": "什么是知识摄入？", "namespace": ns, "scene": "llm_inference"}
        raw_query_data = _assert_ok(client.post(f"{base}/rag/query", json=raw_query_payload), "raw_query")
        if int(raw_query_data.get("count", 0)) <= 0:
            raise RuntimeError("raw_query failed: no snippets returned")
        print("[OK] raw query")

        # 6) raw update
        raw_update_payload = {
            "dataset_id": raw_dataset_id,
            "doc_name": raw_doc_name,
            "namespace": ns,
            "replace_if_exists": True,
            "content": "更新后内容：文档处理还会包含去重、异常字符处理和切块重叠策略。",
            "chunk_size": 60,
            "chunk_overlap": 10,
            "min_chunk_size": 20,
        }
        _assert_ok(client.post(f"{base}/rag/ingest/raw_document", json=raw_update_payload), "raw_update")
        raw_update_query = {"query": "更新后内容提到了什么？", "namespace": ns, "scene": "llm_inference"}
        raw_update_data = _assert_ok(client.post(f"{base}/rag/query", json=raw_update_query), "raw_query_after_update")
        raw_snippets = raw_update_data.get("snippets") or []
        if not any("更新后内容" in s for s in raw_snippets):
            raise RuntimeError("raw_update verification failed: updated snippet not found")
        print("[OK] raw update verification")

        # 7) raw delete
        raw_delete_data = _assert_ok(
            client.post(
                f"{base}/rag/documents/delete",
                json={"doc_name": raw_doc_name, "namespace": ns},
            ),
            "raw_delete",
        )
        if int(raw_delete_data.get("deleted", 0)) <= 0:
            raise RuntimeError("raw_delete verification failed: deleted count is 0")
        raw_query_after_delete = _assert_ok(client.post(f"{base}/rag/query", json=raw_update_query), "raw_query_after_delete")
        if int(raw_query_after_delete.get("count", 0)) != 0:
            raise RuntimeError("raw_delete verification failed: snippets still exist")
        print("[OK] raw delete verification")

        # 8) job ingest (async orchestrator)
        job_payload = {
            "operator": "e2e-script",
            "chunk_size": 90,
            "chunk_overlap": 20,
            "min_chunk_size": 20,
            "documents": [
                {
                    "dataset_id": job_dataset_id,
                    "doc_name": job_doc_name,
                    "namespace": ns,
                    "content": "异步摄入任务验证文本。用于检查 jobs ingest 与状态查询接口是否正常。",
                    "source_type": "text",
                    "replace_if_exists": True,
                    "metadata": {"case": "job_e2e"},
                }
            ],
        }
        job_submit = _assert_ok(client.post(f"{base}/rag/jobs/ingest", json=job_payload), "job_submit")
        job_id = job_submit.get("job_id")
        if not job_id:
            raise RuntimeError("job_submit failed: missing job_id")
        print("[OK] job submit")

        final_status = None
        for _ in range(20):
            st_resp = _assert_ok(client.get(f"{base}/rag/jobs/{job_id}"), "job_status")
            final_status = ((st_resp.get("job") or {}).get("status") or "").upper()
            if final_status in {"SUCCESS", "FAILED", "PARTIAL"}:
                break
            time.sleep(0.3)
        if final_status != "SUCCESS":
            raise RuntimeError(f"job_status failed: final_status={final_status}")
        print("[OK] job status")

        job_query_payload = {"query": "什么接口用于状态查询？", "namespace": ns, "scene": "llm_inference"}
        job_query_data = _assert_ok(client.post(f"{base}/rag/query", json=job_query_payload), "job_query")
        if int(job_query_data.get("count", 0)) <= 0:
            raise RuntimeError("job_query failed: no snippets returned")
        print("[OK] job query")

        job_del = _assert_ok(
            client.post(
                f"{base}/rag/documents/delete",
                json={"doc_name": job_doc_name, "namespace": ns},
            ),
            "job_delete",
        )
        if int(job_del.get("deleted", 0)) <= 0:
            raise RuntimeError("job_delete failed: deleted count is 0")
        print("[OK] job delete")

        if test_migration:
            # 9) migration run / rollback
            mig_run = _assert_ok(
                client.post(
                    f"{base}/rag/migrations/chunks/run",
                    json={"embedding_dim": migration_dim},
                ),
                "migration_run",
            )
            new_index = mig_run.get("new_index")
            old_indices = mig_run.get("old_indices") or []
            print("[OK] migration run")
            # 仅在存在旧索引时验证回滚
            if old_indices:
                rollback_target = old_indices[0]
                _assert_ok(
                    client.post(
                        f"{base}/rag/migrations/chunks/rollback",
                        json={"previous_index": rollback_target},
                    ),
                    "migration_rollback",
                )
                print("[OK] migration rollback")
                # 切回新索引，避免影响后续环境
                _assert_ok(
                    client.post(
                        f"{base}/rag/migrations/chunks/rollback",
                        json={"previous_index": new_index},
                    ),
                    "migration_restore",
                )
                print("[OK] migration restore")

    print("RAG API E2E passed.")


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG API end-to-end test script")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Service base URL")
    parser.add_argument("--test-migration", action="store_true", help="Enable migration run/rollback checks")
    parser.add_argument("--migration-dim", type=int, default=512, help="Embedding dimension for migration index")
    args = parser.parse_args()
    try:
        run(args.base_url, test_migration=args.test_migration, migration_dim=args.migration_dim)
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"[FAILED] {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

