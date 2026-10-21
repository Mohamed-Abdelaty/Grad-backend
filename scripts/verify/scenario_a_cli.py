"""Scenario A: CLI Verification
1. Run linear pipeline -> verify exit code 0, 15 in, 10 out in outputs/orders_clean.csv.
2. Run fan-out pipeline -> verify exit code 0, 15 in, 5 high in outputs/orders_high.csv, 10 low in outputs/orders_low.csv.
3. Run invalid pipeline -> verify exit code 1, prints all 7 collected errors.
"""
from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

PYTHON_EXE = sys.executable

def verify_csv_rows(path: Path) -> int:
    assert path.exists(), f"File {path} does not exist"
    with open(path, encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        assert header is not None, f"File {path} is empty"
        rows = list(reader)
        return len(rows)

def run():
    print("=== SCENARIO A: CLI VERIFICATION ===")

    # 1. Linear Pipeline
    out_clean = Path("outputs/orders_clean.csv")
    if out_clean.exists():
        out_clean.unlink()

    cmd1 = [PYTHON_EXE, "-m", "engine", "run", "samples/pipeline.json"]
    print(f"Running: {' '.join(cmd1)}")
    p1 = subprocess.run(cmd1, capture_output=True, text=True)
    assert p1.returncode == 0, f"Command failed with {p1.returncode}: {p1.stderr}"
    assert "src_1" in p1.stdout and "tr_1" in p1.stdout and "snk_1" in p1.stdout
    rows_clean = verify_csv_rows(out_clean)
    assert rows_clean == 10, f"Expected 10 rows in orders_clean.csv, got {rows_clean}"
    print(f"  [PASS] Linear pipeline: 15 in -> 10 out written to {out_clean}")

    # 2. Fan-out Pipeline
    out_high = Path("outputs/orders_high.csv")
    out_low = Path("outputs/orders_low.csv")
    if out_high.exists():
        out_high.unlink()
    if out_low.exists():
        out_low.unlink()

    cmd2 = [PYTHON_EXE, "-m", "engine", "run", "samples/pipeline_fanout.json"]
    print(f"Running: {' '.join(cmd2)}")
    p2 = subprocess.run(cmd2, capture_output=True, text=True)
    assert p2.returncode == 0, f"Command failed with {p2.returncode}: {p2.stderr}"
    rows_high = verify_csv_rows(out_high)
    rows_low = verify_csv_rows(out_low)
    assert rows_high == 5, f"Expected 5 rows in orders_high.csv, got {rows_high}"
    assert rows_low == 10, f"Expected 10 rows in orders_low.csv, got {rows_low}"
    print("  [PASS] Fanout pipeline: 5 high, 10 low written correctly")

    # 3. Invalid Pipeline
    cmd3 = [PYTHON_EXE, "-m", "engine", "run", "samples/pipeline_invalid.json"]
    print(f"Running: {' '.join(cmd3)}")
    p3 = subprocess.run(cmd3, capture_output=True, text=True)
    assert p3.returncode == 1, f"Expected returncode 1, got {p3.returncode}"
    output = p3.stdout + p3.stderr
    print(f"Captured errors output:\n{output.strip()}")

    required_snippets = [
        "Duplicate node ids",
        "references unknown node",
        "Self-loop",
        "must be acyclic",
        "Source node 'src_1' cannot have incoming edge",
        "Sink node 'snk_1' cannot have outgoing edge",
        "Orphan node 'orphan_node'",
    ]
    for snippet in required_snippets:
        assert snippet in output, f"Missing expected error snippet: {snippet}"

    print("  [PASS] Invalid pipeline: all 7 validation errors collected and displayed.")
    print("=== SCENARIO A PASSED SUCCESSFULLY ===\n")

if __name__ == "__main__":
    run()
