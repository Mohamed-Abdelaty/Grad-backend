"""Regenerate the published pipeline JSON Schema artifact.

The schema is derived from the canonical Pydantic models, so it can never
drift from validation::

    python scripts/generate_schema.py

Output: ``engine/schema/pipeline.schema.json``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.config import PipelineDocument  # noqa: E402


def main() -> None:
    """Generate the schema artifact."""
    out = ROOT / "engine" / "schema" / "pipeline.schema.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    schema = PipelineDocument.model_json_schema()
    out.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
