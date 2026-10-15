import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.config import PipelineDocument  # noqa: E402

artifact = Path("engine/schema/pipeline.schema.json")
on_disk = json.loads(artifact.read_text(encoding="utf-8"))
fresh = PipelineDocument.model_json_schema()
assert on_disk == fresh, "schema artifact is stale; run python scripts/generate_schema.py"
doc = json.loads(Path("examples/pipeline.json").read_text(encoding="utf-8"))
PipelineDocument.model_validate(doc)  # example validates against the contract
print(f"schema OK ({len(json.dumps(fresh))} bytes), example pipeline validates")
