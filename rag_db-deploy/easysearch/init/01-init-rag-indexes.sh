#!/bin/sh
set -eu

ES_URL="${ES_URL:-https://127.0.0.1:9200}"
ES_USER="${ES_USER:-admin}"
ES_PASS="${ES_PASS:-ChangeMe_123!}"

RAG_ES_INDEX_NAME="${RAG_ES_INDEX_NAME:-rag_knowledge_base}"
RAG_ES_INDEX_ALIAS="${RAG_ES_INDEX_ALIAS:-rag_knowledge_base}"
RAG_ES_INDEX_VERSION="${RAG_ES_INDEX_VERSION:-1}"
RAG_ES_DOCS_INDEX_NAME="${RAG_ES_DOCS_INDEX_NAME:-rag_docs}"
RAG_ES_DOCS_INDEX_ALIAS="${RAG_ES_DOCS_INDEX_ALIAS:-rag_docs_current}"
RAG_ES_DOCS_INDEX_VERSION="${RAG_ES_DOCS_INDEX_VERSION:-1}"
RAG_ES_JOBS_INDEX_NAME="${RAG_ES_JOBS_INDEX_NAME:-rag_jobs}"
RAG_ES_JOBS_INDEX_ALIAS="${RAG_ES_JOBS_INDEX_ALIAS:-rag_jobs_current}"
RAG_ES_JOBS_INDEX_VERSION="${RAG_ES_JOBS_INDEX_VERSION:-1}"

MAIN_INDEX="${RAG_ES_INDEX_NAME}_v${RAG_ES_INDEX_VERSION}"
DOCS_INDEX="${RAG_ES_DOCS_INDEX_NAME}_v${RAG_ES_DOCS_INDEX_VERSION}"
JOBS_INDEX="${RAG_ES_JOBS_INDEX_NAME}_v${RAG_ES_JOBS_INDEX_VERSION}"

echo "[init] waiting for ES API..."
for i in $(seq 1 90); do
  if curl -s -k -u "${ES_USER}:${ES_PASS}" "${ES_URL}/_cluster/health" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo "[init] creating main rag index and alias..."
curl -s -k -u "${ES_USER}:${ES_PASS}" -X PUT "${ES_URL}/${MAIN_INDEX}" \
  -H "Content-Type: application/json" \
  -d '{
    "mappings": {
      "properties": {
        "id": {"type": "keyword"},
        "text": {"type": "text"},
        "dataset_id": {"type": "keyword"},
        "doc_name": {"type": "keyword"},
        "doc_version": {"type": "keyword"},
        "namespace": {"type": "keyword"},
        "tenant_id": {"type": "keyword"},
        "chunk_id": {"type": "keyword"},
        "chunk_hash": {"type": "keyword"}
      }
    }
  }' >/dev/null || true

curl -s -k -u "${ES_USER}:${ES_PASS}" -X POST "${ES_URL}/_aliases" \
  -H "Content-Type: application/json" \
  -d "{\"actions\":[{\"add\":{\"index\":\"${MAIN_INDEX}\",\"alias\":\"${RAG_ES_INDEX_ALIAS}\"}}]}" >/dev/null || true

echo "[init] creating docs index and alias..."
curl -s -k -u "${ES_USER}:${ES_PASS}" -X PUT "${ES_URL}/${DOCS_INDEX}" \
  -H "Content-Type: application/json" \
  -d '{
    "mappings": {
      "properties": {
        "doc_name": {"type": "keyword"},
        "doc_version": {"type": "keyword"},
        "dataset_id": {"type": "keyword"},
        "namespace": {"type": "keyword"},
        "tenant_id": {"type": "keyword"},
        "created_at": {"type": "date"},
        "updated_at": {"type": "date"}
      }
    }
  }' >/dev/null || true

curl -s -k -u "${ES_USER}:${ES_PASS}" -X POST "${ES_URL}/_aliases" \
  -H "Content-Type: application/json" \
  -d "{\"actions\":[{\"add\":{\"index\":\"${DOCS_INDEX}\",\"alias\":\"${RAG_ES_DOCS_INDEX_ALIAS}\"}}]}" >/dev/null || true

echo "[init] creating jobs index and alias..."
curl -s -k -u "${ES_USER}:${ES_PASS}" -X PUT "${ES_URL}/${JOBS_INDEX}" \
  -H "Content-Type: application/json" \
  -d '{
    "mappings": {
      "properties": {
        "job_id": {"type": "keyword"},
        "job_type": {"type": "keyword"},
        "status": {"type": "keyword"},
        "dataset_id": {"type": "keyword"},
        "doc_name": {"type": "keyword"},
        "idempotency_key": {"type": "keyword"},
        "created_at": {"type": "date"},
        "updated_at": {"type": "date"}
      }
    }
  }' >/dev/null || true

curl -s -k -u "${ES_USER}:${ES_PASS}" -X POST "${ES_URL}/_aliases" \
  -H "Content-Type: application/json" \
  -d "{\"actions\":[{\"add\":{\"index\":\"${JOBS_INDEX}\",\"alias\":\"${RAG_ES_JOBS_INDEX_ALIAS}\"}}]}" >/dev/null || true

echo "[init] done."
