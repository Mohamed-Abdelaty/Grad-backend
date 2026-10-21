"""Phase 3: Extensibility Proof
1. Create a brand new plugin file: engine/plugins/transforms/uppercase_transform.py without editing any core engine or API files.
2. Query GET /plugins and verify auto-discovery of 'uppercase' transform and its schema.
3. Execute a pipeline using 'uppercase' transform and verify output correctness.
4. Clean up / delete the throwaway plugin file.
"""
from __future__ import annotations

import csv
import sys
import uuid
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from starlette.testclient import TestClient

from app.main import app
from engine.config import PipelineDocument
from engine.executor import Executor
from engine.registry import _REGISTRY

PLUGIN_FILE = Path("engine/plugins/transforms/uppercase_transform.py")
OUT_CSV = Path("outputs/uppercase_out.csv")

PLUGIN_CODE = '''"""Custom throwaway transform plugin for extensibility proof."""
from __future__ import annotations
from typing import Any
from engine.plugins.base import BaseTransform, Batch
from engine.registry import register_transform

@register_transform("uppercase")
class UppercaseTransform(BaseTransform):
    name = "uppercase"

    @classmethod
    def config_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["field"],
            "properties": {"field": {"type": "string"}},
            "additionalProperties": False,
        }

    def validate_config(self) -> None:
        if not self.config.get("field"):
            raise ValueError("Field is required")

    def process(self, batch: Batch) -> Batch:
        field = self.config["field"]
        out = []
        for row in batch:
            r = dict(row)
            if field in r and isinstance(r[field], str):
                r[field] = r[field].upper()
            out.append(r)
        return out
'''

def run():
    print("=== EXTENSIBILITY PROOF: THROWAWAY PLUGIN ===")

    if OUT_CSV.exists():
        OUT_CSV.unlink()

    try:
        # 1. Write the new plugin file
        print(f"  [1] Writing new plugin to {PLUGIN_FILE}...")
        PLUGIN_FILE.write_text(PLUGIN_CODE, encoding="utf-8")

        # 2. Test GET /plugins auto-discovery
        with TestClient(app) as client:
            resp = client.get("/plugins?category=transform")
            assert resp.status_code == 200
            plugins = resp.json()
            upper_plugin = next((p for p in plugins if p["name"] == "uppercase"), None)
            assert upper_plugin is not None, "Newly dropped plugin was not auto-discovered in GET /plugins"
            assert upper_plugin["config_schema"]["required"] == ["field"]
            print(f"  [2] Auto-discovered plugin in GET /plugins: {upper_plugin['name']}")

        # 3. Execute pipeline using the new plugin
        pipe_doc = {
            "pipeline": {
                "id": str(uuid.uuid4()),
                "name": "uppercase-test",
                "version": 1,
                "schedule": None,
                "nodes": [
                    {"id": "src", "type": "source", "plugin": "csv", "config": {"path": "samples/data/orders.csv"}},
                    {"id": "tr_upper", "type": "transform", "plugin": "uppercase", "config": {"field": "status"}},
                    {"id": "snk", "type": "sink", "plugin": "csv", "config": {"path": str(OUT_CSV)}},
                ],
                "edges": [
                    {"from": "src", "to": "tr_upper"},
                    {"from": "tr_upper", "to": "snk"},
                ],
                "retry": {"max_attempts": 1, "backoff_seconds": 1},
            }
        }
        doc = PipelineDocument.model_validate(pipe_doc)
        executor = Executor(doc.pipeline)
        result = executor.run()
        assert result.metrics["tr_upper"].records_out == 15
        assert OUT_CSV.exists()

        with open(OUT_CSV, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            statuses = [row["status"] for row in reader]
            assert all(s in ("PAID", "PENDING") for s in statuses), f"Expected uppercased statuses, got: {statuses}"
        print("  [3] Executed pipeline with new plugin: 15 rows successfully transformed to uppercase.")

    finally:
        # 4. Clean up plugin file and memory registry
        if PLUGIN_FILE.exists():
            PLUGIN_FILE.unlink()
        _REGISTRY.pop(("transform", "uppercase"), None)
        if OUT_CSV.exists():
            OUT_CSV.unlink()
        print("  [4] Cleaned up throwaway plugin file and registry.")

    print("=== EXTENSIBILITY PROOF PASSED ===\n")

if __name__ == "__main__":
    run()
