import unittest
from unittest.mock import MagicMock, patch

from app.core.config import GraphRAGConfig
from app.graph.extraction.types import ExtractedEntity, ExtractedGraphPayload
from app.graph.ingestion import GraphIngestionService


class TestGraphIngestion(unittest.TestCase):
    @patch("app.graph.ingestion.Neo4jGraphClient.execute_cypher")
    def test_llm_mode_writes_entities_and_relations(self, mock_cypher):
        cfg = GraphRAGConfig(
            enabled=True,
            uri="bolt://localhost:7687",
            username="neo4j",
            password="x",
            extraction_mode="llm",
        )
        svc = GraphIngestionService(cfg)
        payload = ExtractedGraphPayload(
            entities=[ExtractedEntity(type="Concept", id="a", name="A", properties={})],
            relations=[],
        )
        svc._llm_extractor = MagicMock()
        svc._llm_extractor.extract_batch.return_value = [payload]

        svc.ingest_from_chunks(
            dataset_id="ds1",
            texts=["hello"],
            namespace="ns",
            doc_name="doc1",
            doc_version="v1",
        )
        self.assertTrue(mock_cypher.called)
        svc._llm_extractor.extract_batch.assert_called_once()

    @patch("app.graph.ingestion.Neo4jGraphClient.execute_cypher")
    def test_rule_mode_adds_cooccur(self, mock_cypher):
        cfg = GraphRAGConfig(
            enabled=True,
            uri="bolt://localhost:7687",
            username="neo4j",
            password="x",
            extraction_mode="rule",
        )
        svc = GraphIngestionService(cfg)
        svc._rule_extractor = MagicMock()
        svc._rule_extractor.extract.return_value = ExtractedGraphPayload(
            entities=[
                ExtractedEntity(type="Concept", id="a", name="A", properties={}),
                ExtractedEntity(type="Concept", id="b", name="B", properties={}),
            ],
            relations=[],
        )
        svc.ingest_from_chunks(
            dataset_id="ds1",
            texts=["锅炉过热故障"],
            namespace="ns",
            doc_name="doc1",
        )
        calls = [str(c.args[0]) for c in mock_cypher.call_args_list]
        self.assertTrue(any("CO_OCCUR" in q for q in calls))

    @patch("app.graph.ingestion.Neo4jGraphClient.execute_cypher")
    def test_disabled_is_noop(self, mock_cypher):
        cfg = GraphRAGConfig(enabled=False)
        svc = GraphIngestionService(cfg)
        svc.ingest_from_chunks(dataset_id="ds", texts=["x"], namespace="ns")
        mock_cypher.assert_not_called()


if __name__ == "__main__":
    unittest.main()
