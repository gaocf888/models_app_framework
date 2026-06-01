import json
import unittest
from unittest.mock import MagicMock, patch

from app.core.config import GraphRAGConfig, GraphSchemaConfig
from app.graph.extraction.llm_extractor import LLMGraphExtractor, _extract_json_block


class TestLLMGraphExtractor(unittest.TestCase):
    def test_extract_json_block_from_fence(self):
        raw = '```json\n{"entities": [], "relations": []}\n```'
        data = _extract_json_block(raw)
        self.assertEqual([], data.get("entities"))

    @patch("app.graph.extraction.llm_extractor.LLMGraphExtractor._call_llm")
    def test_parse_llm_payload(self, mock_call):
        mock_call.return_value = json.dumps(
            {
                "entities": [{"id": "boiler", "name": "锅炉", "type": "Equipment"}],
                "relations": [],
            },
            ensure_ascii=False,
        )
        cfg = GraphRAGConfig(enabled=True)
        ext = LLMGraphExtractor(cfg)
        payload = ext.extract("锅炉过热", schema=GraphSchemaConfig(enabled=False))
        self.assertEqual(1, len(payload.entities))
        self.assertEqual("boiler", payload.entities[0].id)


class TestGraphIngestionLLMPath(unittest.TestCase):
    @patch("app.graph.ingestion.Neo4jGraphClient.execute_cypher")
    @patch("app.graph.ingestion.LLMGraphExtractor.extract")
    def test_ingest_uses_llm_extractor(self, mock_extract, mock_cypher):
        from app.graph.extraction.types import ExtractedEntity, ExtractedGraphPayload
        from app.graph.ingestion import GraphIngestionService

        mock_extract.return_value = ExtractedGraphPayload(
            entities=[ExtractedEntity(type="Concept", id="a", name="A", properties={})],
            relations=[],
        )
        cfg = GraphRAGConfig(enabled=True, uri="bolt://x", username="u", password="p", extraction_mode="llm")
        svc = GraphIngestionService(cfg)
        svc.ingest_from_chunks(dataset_id="ds", texts=["hello"], namespace="ns", doc_name="doc")
        self.assertTrue(mock_extract.called)
        self.assertTrue(mock_cypher.called)


if __name__ == "__main__":
    unittest.main()
