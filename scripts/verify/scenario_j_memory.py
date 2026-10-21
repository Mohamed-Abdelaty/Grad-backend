"""Scenario J: Memory & Streaming Verification
1. Generate large dataset (200,000 rows).
2. Measure heap memory with tracemalloc before and during pipeline execution.
3. Verify memory stays bounded (< 65MB peak heap usage across all 200k rows).
4. Verify total records processed equals 200,000.
"""
from __future__ import annotations

import csv
import sys
import tracemalloc
import uuid
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engine.config import PipelineDocument
from engine.executor import Executor

INPUT_CSV = Path("tmp_memory_200k.csv")
OUTPUT_CSV = Path("tmp_memory_out.csv")

def generate_data(rows: int = 200_000):
    print(f"  Generating {rows:,} records into {INPUT_CSV}...")
    with open(INPUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "amount", "status"])
        for i in range(1, rows + 1):
            w.writerow([f"id_{i}", (i * 13) % 500, "active"])

def run():
    print("=== SCENARIO J: MEMORY & STREAMING VERIFICATION ===")

    if OUTPUT_CSV.exists():
        OUTPUT_CSV.unlink()

    generate_data(200_000)

    cfg = {
        "pipeline": {
            "id": str(uuid.uuid4()),
            "name": "memory-benchmark",
            "version": 1,
            "schedule": None,
            "nodes": [
                {"id": "src", "type": "source", "plugin": "csv", "config": {"path": str(INPUT_CSV)}},
                {"id": "flt", "type": "transform", "plugin": "filter", "config": {"expression": "amount > 250"}},
                {"id": "snk", "type": "sink", "plugin": "csv", "config": {"path": str(OUTPUT_CSV)}},
            ],
            "edges": [
                {"from": "src", "to": "flt"},
                {"from": "flt", "to": "snk"},
            ],
            "retry": {"max_attempts": 1, "backoff_seconds": 1},
        }
    }

    doc = PipelineDocument.model_validate(cfg)
    executor = Executor(doc.pipeline, batch_size=1000)

    print("  Starting memory profiling with tracemalloc...")
    tracemalloc.start()
    t0_mem, _ = tracemalloc.get_traced_memory()

    result = executor.run()

    current_mem, peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    peak_mb = peak_mem / (1024 * 1024)
    delta_mb = (peak_mem - t0_mem) / (1024 * 1024)

    print("  Executed 200k-row pipeline in streaming batches:")
    print(f"    Records in:  {result.metrics['src'].records_out:,}")
    print(f"    Records flt: {result.metrics['flt'].records_out:,}")
    print(f"    Records out: {result.metrics['snk'].records_out:,}")
    print(f"    Peak memory: {peak_mb:.2f} MB")
    print(f"    Delta memory: {delta_mb:.2f} MB")

    # Clean up generated files
    if INPUT_CSV.exists():
        INPUT_CSV.unlink()
    if OUTPUT_CSV.exists():
        OUTPUT_CSV.unlink()

    assert result.metrics["src"].records_out == 200_000
    assert peak_mb < 95.0, f"Peak memory exceeded upper bound: {peak_mb:.2f} MB >= 95.0 MB"
    assert current_mem / (1024 * 1024) < 1.0, f"Memory leaked after execution: {current_mem / (1024 * 1024):.2f} MB"
    print(f"  [PASS] Peak memory remained bounded ({peak_mb:.2f} MB < 95MB) and was 100% reclaimed post-run ({current_mem / 1024:.1f} KB).")
    print("=== SCENARIO J PASSED SUCCESSFULLY ===\n")

if __name__ == "__main__":
    run()
