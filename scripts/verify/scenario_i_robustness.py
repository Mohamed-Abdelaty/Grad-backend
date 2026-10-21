"""Scenario I: Robustness & Edge Cases
1. Empty CSV (0 rows or header-only) -> completes cleanly with 0 rows, no crash.
2. UTF-8 BOM CSV -> BOM stripped from column headers, expressions evaluate correctly.
3. Unicode characters (emojis, accented characters, CJK) -> preserved across transforms and sinks.
4. Missing input file -> fails cleanly with descriptive error attributed to source node.
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

from engine.config import PipelineDocument
from engine.errors import NodeExecutionError
from engine.executor import Executor

TMP_DIR = Path("tmp_robustness")

def setup_tmp():
    TMP_DIR.mkdir(parents=True, exist_ok=True)

def cleanup_tmp():
    import shutil
    if TMP_DIR.exists():
        shutil.rmtree(TMP_DIR)

def test_empty_csv():
    print("  1. Testing Empty CSV Handling...")
    empty_path = TMP_DIR / "empty.csv"
    out_path = TMP_DIR / "out_empty.csv"
    empty_path.write_text("id,val\n", encoding="utf-8")

    cfg = {
        "pipeline": {
            "id": str(uuid.uuid4()),
            "name": "empty-test",
            "version": 1,
            "schedule": None,
            "nodes": [
                {"id": "src", "type": "source", "plugin": "csv", "config": {"path": str(empty_path)}},
                {"id": "snk", "type": "sink", "plugin": "csv", "config": {"path": str(out_path)}},
            ],
            "edges": [{"from": "src", "to": "snk"}],
            "retry": {"max_attempts": 1, "backoff_seconds": 1},
        }
    }
    doc = PipelineDocument.model_validate(cfg)
    executor = Executor(doc.pipeline)
    res = executor.run()
    assert res.metrics["src"].records_out == 0
    assert res.metrics["snk"].records_out == 0
    assert out_path.exists()
    print("  [PASS] Empty CSV handled cleanly without errors (0 records).")

def test_bom_csv():
    print("  2. Testing UTF-8 BOM CSV Handling...")
    bom_path = TMP_DIR / "bom.csv"
    out_path = TMP_DIR / "out_bom.csv"
    # Write UTF-8 with BOM (\ufeff)
    with open(bom_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["amount", "name"])
        w.writerow([150, "Alice"])
        w.writerow([50, "Bob"])

    cfg = {
        "pipeline": {
            "id": str(uuid.uuid4()),
            "name": "bom-test",
            "version": 1,
            "schedule": None,
            "nodes": [
                {"id": "src", "type": "source", "plugin": "csv", "config": {"path": str(bom_path)}},
                {"id": "flt", "type": "transform", "plugin": "filter", "config": {"expression": "amount > 100"}},
                {"id": "snk", "type": "sink", "plugin": "csv", "config": {"path": str(out_path)}},
            ],
            "edges": [{"from": "src", "to": "flt"}, {"from": "flt", "to": "snk"}],
            "retry": {"max_attempts": 1, "backoff_seconds": 1},
        }
    }
    doc = PipelineDocument.model_validate(cfg)
    executor = Executor(doc.pipeline)
    res = executor.run()
    # If BOM was not stripped, 'amount' would be '\ufeffamount' and filter on 'amount' would fail!
    assert res.metrics["src"].records_out == 2
    assert res.metrics["flt"].records_out == 1
    assert res.metrics["snk"].records_out == 1
    print("  [PASS] UTF-8 BOM stripped transparently, expression evaluated field cleanly.")

def test_unicode():
    print("  3. Testing Unicode & Special Characters...")
    unicode_path = TMP_DIR / "unicode.csv"
    out_path = TMP_DIR / "out_unicode.csv"
    with open(unicode_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["city", "comment"])
        w.writerow(["Montréal", "Café crème délicieux ☕"])
        w.writerow(["Tokyo (東京)", "こんにちは世界 🌸"])
        w.writerow(["Cairo (القاهرة)", "أهلاً وسهلاً"])

    cfg = {
        "pipeline": {
            "id": str(uuid.uuid4()),
            "name": "unicode-test",
            "version": 1,
            "schedule": None,
            "nodes": [
                {"id": "src", "type": "source", "plugin": "csv", "config": {"path": str(unicode_path)}},
                {"id": "snk", "type": "sink", "plugin": "csv", "config": {"path": str(out_path)}},
            ],
            "edges": [{"from": "src", "to": "snk"}],
            "retry": {"max_attempts": 1, "backoff_seconds": 1},
        }
    }
    doc = PipelineDocument.model_validate(cfg)
    executor = Executor(doc.pipeline)
    res = executor.run()
    assert res.metrics["snk"].records_out == 3
    # Read back and compare
    with open(out_path, encoding="utf-8") as f:
        content = f.read()
        assert "Café crème délicieux ☕" in content
        assert "こんにちは世界 🌸" in content
        assert "أهلاً وسهلاً" in content
    print("  [PASS] Multilingual Unicode and emoji characters perfectly preserved.")

def test_missing_file():
    print("  4. Testing Missing File Error Handling...")
    missing_path = TMP_DIR / "does_not_exist_99999.csv"
    cfg = {
        "pipeline": {
            "id": str(uuid.uuid4()),
            "name": "missing-file-test",
            "version": 1,
            "schedule": None,
            "nodes": [
                {"id": "src_missing", "type": "source", "plugin": "csv", "config": {"path": str(missing_path)}},
                {"id": "snk", "type": "sink", "plugin": "console", "config": {}},
            ],
            "edges": [{"from": "src_missing", "to": "snk"}],
            "retry": {"max_attempts": 1, "backoff_seconds": 1},
        }
    }
    doc = PipelineDocument.model_validate(cfg)
    executor = Executor(doc.pipeline)
    try:
        executor.run()
        assert False, "Should have raised NodeExecutionError for missing file"
    except NodeExecutionError as exc:
        assert exc.node_id == "src_missing"
        assert "FileNotFoundError" in str(exc) or "not found" in str(exc)
        print(f"  [PASS] Missing file raised typed NodeExecutionError attributed to node '{exc.node_id}'.")

def run():
    print("=== SCENARIO I: ROBUSTNESS & EDGE CASES ===")
    setup_tmp()
    try:
        test_empty_csv()
        test_bom_csv()
        test_unicode()
        test_missing_file()
    finally:
        cleanup_tmp()
    print("=== SCENARIO I PASSED SUCCESSFULLY ===\n")

if __name__ == "__main__":
    run()
