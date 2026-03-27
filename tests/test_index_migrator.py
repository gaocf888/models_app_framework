import unittest

from app.core.config import ElasticsearchConfig
from app.rag.migrations.index_migrator import IndexMigrator


class _FakeIndices:
    def __init__(self):
        self.exists_flag = False
        self.alias_map = {}
        self.created = []
        self.update_aliases_calls = []

    def exists(self, index):
        return self.exists_flag

    def create(self, index, body):
        self.created.append((index, body))
        self.exists_flag = True

    def get_alias(self, name):
        if name not in self.alias_map:
            raise RuntimeError("alias not found")
        return {idx: {"aliases": {name: {}}} for idx in self.alias_map[name]}

    def update_aliases(self, body):
        self.update_aliases_calls.append(body)
        actions = body.get("actions") or []
        for act in actions:
            if "remove" in act:
                alias = act["remove"]["alias"]
                index = act["remove"]["index"]
                self.alias_map.setdefault(alias, [])
                self.alias_map[alias] = [x for x in self.alias_map[alias] if x != index]
            if "add" in act:
                alias = act["add"]["alias"]
                index = act["add"]["index"]
                self.alias_map.setdefault(alias, [])
                if index not in self.alias_map[alias]:
                    self.alias_map[alias].append(index)


class _FakeEsClient:
    def __init__(self):
        self.indices = _FakeIndices()


class TestIndexMigrator(unittest.TestCase):
    def test_ensure_index_and_alias_switch(self):
        cfg = ElasticsearchConfig(
            hosts=["http://127.0.0.1:9200"],
            index_name="rag_chunks",
            index_alias="rag_chunks_current",
            index_version=2,
        )
        client = _FakeEsClient()
        client.indices.alias_map["rag_chunks_current"] = ["rag_chunks_v1"]
        migrator = IndexMigrator(es_cfg=cfg, client=client)

        mapping = {"mappings": {"properties": {"text": {"type": "text"}}}}
        result = migrator.ensure_index_and_alias(mapping=mapping)

        self.assertEqual("rag_chunks_v2", result.new_index)
        self.assertEqual("rag_chunks_current", result.alias)
        self.assertEqual(["rag_chunks_v1"], result.old_indices)
        self.assertEqual(1, len(client.indices.created))
        self.assertEqual(["rag_chunks_v2"], client.indices.alias_map["rag_chunks_current"])

    def test_rollback_alias(self):
        cfg = ElasticsearchConfig(
            hosts=["http://127.0.0.1:9200"],
            index_name="rag_chunks",
            index_alias="rag_chunks_current",
            index_version=2,
        )
        client = _FakeEsClient()
        client.indices.exists_flag = True
        client.indices.alias_map["rag_chunks_current"] = ["rag_chunks_v2"]
        migrator = IndexMigrator(es_cfg=cfg, client=client)

        migrator.rollback_alias(previous_index="rag_chunks_v1")

        self.assertEqual(["rag_chunks_v1"], client.indices.alias_map["rag_chunks_current"])
        self.assertGreaterEqual(len(client.indices.update_aliases_calls), 1)


if __name__ == "__main__":
    unittest.main()
