"""RAG 原始文档上传（长期 URL）单测。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api import rag_admin
from app.rag.doc_upload import (
    _build_permanent_url,
    guess_source_and_content_type,
    upload_rag_document,
)


def test_guess_source_and_content_type():
    st, ct = guess_source_and_content_type("锅炉说明书.pdf", None)
    assert st == "pdf"
    assert "pdf" in ct
    st2, _ = guess_source_and_content_type("a.docx", "application/octet-stream")
    assert st2 == "docx"
    with pytest.raises(ValueError):
        guess_source_and_content_type("a.exe", None)


def test_build_permanent_url_prefers_public_base():
    url = _build_permanent_url(
        public_base_url="http://192.168.90.62:9000/rag-docs",
        endpoint="models-app-minio:9000",
        secure=False,
        bucket="rag-docs",
        object_name="docs/ns/a.pdf",
    )
    assert url == "http://192.168.90.62:9000/rag-docs/docs/ns/a.pdf"
    assert "?" not in url


def test_build_permanent_url_fallback_endpoint():
    url = _build_permanent_url(
        public_base_url="",
        endpoint="models-app-minio:9000",
        secure=False,
        bucket="rag-docs",
        object_name="docs/a.pdf",
    )
    assert url == "http://models-app-minio:9000/rag-docs/docs/a.pdf"


def test_upload_rag_document_puts_object_and_returns_long_lived_url():
    mock_client = MagicMock()
    mock_client.bucket_exists.return_value = True

    chat_cfg = SimpleNamespace(
        image_minio_endpoint="minio:9000",
        image_minio_access_key="ak",
        image_minio_secret_key="sk",
        image_minio_secure=False,
        image_minio_auto_create_bucket=True,
    )
    ingest_cfg = SimpleNamespace(
        doc_minio_bucket="rag-docs",
        doc_object_key_prefix="docs/",
        doc_public_base_url="http://files.example.com/rag-docs",
        doc_ensure_public_read=True,
        doc_upload_max_mb=10,
    )
    app_cfg = SimpleNamespace(chatbot=chat_cfg, rag=SimpleNamespace(ingestion=ingest_cfg))

    with (
        patch("app.rag.doc_upload.get_app_config", return_value=app_cfg),
        patch("app.rag.doc_upload._build_minio_client", return_value=mock_client),
    ):
        result = upload_rag_document(
            file_name="锅炉说明书.pdf",
            content=b"%PDF-1.4 demo",
            content_type="application/pdf",
            namespace="Power_plant_knowledge",
            doc_name="锅炉说明书",
        )

    assert result.source_type == "pdf"
    assert result.bucket == "rag-docs"
    assert result.url.startswith("http://files.example.com/rag-docs/")
    assert "?" not in result.url
    assert "锅炉说明书" in result.object_name or "pdf" in result.object_name
    mock_client.put_object.assert_called_once()
    mock_client.set_bucket_policy.assert_called_once()


def test_upload_endpoint_rejects_empty():
    class _EmptyFile:
        filename = "a.pdf"
        content_type = "application/pdf"

        async def read(self):
            return b""

    with pytest.raises(HTTPException) as ctx:
        asyncio.run(rag_admin.upload_rag_document_endpoint(file=_EmptyFile()))
    assert ctx.value.status_code == 400
