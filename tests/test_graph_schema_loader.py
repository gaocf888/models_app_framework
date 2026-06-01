import tempfile
import unittest
from pathlib import Path

from app.graph.schema_loader import load_graph_schema


class TestGraphSchemaLoader(unittest.TestCase):
    def test_missing_file_returns_disabled_schema(self):
        schema = load_graph_schema("/nonexistent/graph_schema.yaml")
        self.assertFalse(schema.enabled)
        self.assertEqual(0, len(schema.nodes))

    def test_enabled_false_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "schema.yaml"
            p.write_text(
                "enabled: false\nnodes:\n  Equipment:\n    name: Equipment\n    labels: [Entity]\n    key_fields: [name]\n",
                encoding="utf-8",
            )
            schema = load_graph_schema(p)
            self.assertFalse(schema.enabled)
            self.assertIn("Equipment", schema.nodes)

    def test_enabled_true_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "schema.yaml"
            p.write_text(
                """
enabled: true
nodes:
  Fault:
    name: Fault
    labels: [Entity, Fault]
    key_fields: [name]
relations:
  HAS_FAULT:
    name: HAS_FAULT
    type: HAS_FAULT
    from_node: Equipment
    to_node: Fault
""".strip(),
                encoding="utf-8",
            )
            schema = load_graph_schema(p, fail_fast=True)
            self.assertTrue(schema.enabled)
            self.assertIn("Fault", schema.nodes)
            self.assertIn("HAS_FAULT", schema.relations)

    def test_invalid_yaml_fail_fast(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bad.yaml"
            p.write_text("enabled: [not-a-bool-structure", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_graph_schema(p, fail_fast=True)


if __name__ == "__main__":
    unittest.main()
