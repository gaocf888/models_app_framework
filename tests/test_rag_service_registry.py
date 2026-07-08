"""RAG 基座进程内单例注册表测试。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.rag.service_registry import (
    clear_rag_service_registry,
    get_embedding_service,
    get_hybrid_rag_service,
    get_nl2sql_rag_service,
    get_rag_service,
    get_vector_store_provider,
)


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    clear_rag_service_registry()
    yield
    clear_rag_service_registry()


class TestRAGServiceRegistry:
    @patch("app.rag.embedding_service.EmbeddingService")
    def test_get_embedding_service_singleton(self, mock_cls: MagicMock) -> None:
        inst = MagicMock(name="embed")
        mock_cls.return_value = inst

        a = get_embedding_service()
        b = get_embedding_service()

        assert a is b
        mock_cls.assert_called_once()

    @patch("app.rag.vector_store.VectorStoreProvider")
    def test_get_vector_store_provider_singleton(self, mock_cls: MagicMock) -> None:
        inst = MagicMock(name="store")
        mock_cls.return_value = inst

        a = get_vector_store_provider()
        b = get_vector_store_provider()

        assert a is b
        mock_cls.assert_called_once()

    @patch("app.rag.rag_service.RAGService")
    @patch("app.rag.service_registry.get_vector_store_provider")
    @patch("app.rag.service_registry.get_embedding_service")
    def test_get_rag_service_singleton(
        self,
        mock_get_embed: MagicMock,
        mock_get_store: MagicMock,
        mock_rag_cls: MagicMock,
    ) -> None:
        embed = MagicMock(name="embed")
        store = MagicMock(name="store")
        rag = MagicMock(name="rag")
        mock_get_embed.return_value = embed
        mock_get_store.return_value = store
        mock_rag_cls.return_value = rag

        a = get_rag_service()
        b = get_rag_service()

        assert a is b
        mock_rag_cls.assert_called_once_with(
            embedding_service=embed,
            store_provider=store,
        )

    @patch("app.nl2sql.rag_service.NL2SQLRAGService")
    @patch("app.rag.service_registry.get_rag_service")
    def test_get_nl2sql_rag_service_singleton(
        self,
        mock_get_rag: MagicMock,
        mock_nl2sql_cls: MagicMock,
    ) -> None:
        rag = MagicMock(name="rag")
        nl2sql = MagicMock(name="nl2sql_rag")
        mock_get_rag.return_value = rag
        mock_nl2sql_cls.return_value = nl2sql

        a = get_nl2sql_rag_service()
        b = get_nl2sql_rag_service()

        assert a is b
        mock_nl2sql_cls.assert_called_once_with(rag_service=rag)

    @patch("app.rag.hybrid_rag_service.HybridRAGService")
    @patch("app.rag.service_registry.get_rag_service")
    def test_get_hybrid_rag_service_singleton(
        self,
        mock_get_rag: MagicMock,
        mock_hybrid_cls: MagicMock,
    ) -> None:
        rag = MagicMock(name="rag")
        hybrid = MagicMock(name="hybrid")
        mock_get_rag.return_value = rag
        mock_hybrid_cls.return_value = hybrid

        a = get_hybrid_rag_service()
        b = get_hybrid_rag_service()

        assert a is b
        mock_hybrid_cls.assert_called_once_with(rag_service=rag)

    @patch("app.rag.embedding_service.EmbeddingService")
    def test_clear_registry_allows_new_instance(self, mock_cls: MagicMock) -> None:
        first = MagicMock(name="embed1")
        second = MagicMock(name="embed2")
        mock_cls.side_effect = [first, second]

        assert get_embedding_service() is first
        clear_rag_service_registry()
        assert get_embedding_service() is second
        assert mock_cls.call_count == 2

    def test_get_rag_service_without_nested_deadlock(self) -> None:
        """get_rag_service 在持锁路径上嵌套调用 get_embedding_service，不可死锁（须 RLock）。"""
        import threading

        from app.rag.service_registry import get_rag_service

        errors: list[str] = []

        def _worker() -> None:
            try:
                get_rag_service()
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))

        with patch("app.rag.embedding_service.EmbeddingService", return_value=MagicMock(name="embed")):
            with patch("app.rag.vector_store.VectorStoreProvider", return_value=MagicMock(name="store")):
                with patch("app.rag.rag_service.RAGService", side_effect=lambda **kw: MagicMock(name="rag")):
                    t = threading.Thread(target=_worker)
                    t.start()
                    t.join(timeout=5.0)

        assert not t.is_alive(), "get_rag_service deadlocked (check registry lock is RLock)"
        assert not errors, errors

    @patch("app.rag.service_registry.get_vector_store_provider")
    @patch("app.rag.service_registry.get_embedding_service")
    def test_rag_service_explicit_injection_bypasses_registry(
        self,
        mock_get_embed: MagicMock,
        mock_get_store: MagicMock,
    ) -> None:
        from app.rag.rag_service import RAGService

        fake_embed = MagicMock(name="fake_embed")
        fake_store = MagicMock(name="fake_store")
        svc = RAGService(embedding_service=fake_embed, store_provider=fake_store)

        assert svc._embedding_service is fake_embed
        assert svc._store_provider is fake_store
        mock_get_embed.assert_not_called()
        mock_get_store.assert_not_called()
