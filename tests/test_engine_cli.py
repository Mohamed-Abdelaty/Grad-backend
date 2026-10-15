"""CLI tests: run success/failure codes and schema generation."""

from __future__ import annotations

import json

from engine.__main__ import main


def test_cli_run_success(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from tests.conftest import _valid_doc

    src = tmp_path / "in.csv"
    src.write_text("a\n1\n2\n", encoding="utf-8")
    out = tmp_path / "out.csv"
    doc = _valid_doc()
    doc["pipeline"]["nodes"][0]["config"] = {"path": str(src)}
    doc["pipeline"]["nodes"][1] = {
        "id": "tr_1",
        "type": "transform",
        "plugin": "cast",
        "config": {"fields": {"a": "int"}},
    }
    doc["pipeline"]["nodes"][2] = {
        "id": "snk_1",
        "type": "sink",
        "plugin": "csv",
        "config": {"path": str(out), "mode": "overwrite"},
    }
    cfg = tmp_path / "pipe.json"
    cfg.write_text(json.dumps(doc), encoding="utf-8")
    assert main(["run", str(cfg)]) == 0
    assert out.is_file()


def test_cli_run_missing_file(tmp_path) -> None:  # type: ignore[no-untyped-def]
    assert main(["run", str(tmp_path / "nope.json")]) == 2


def test_cli_run_invalid_config(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    cfg = tmp_path / "bad.json"
    cfg.write_text('{"pipeline": {"id": "x"}}', encoding="utf-8")
    assert main(["run", str(cfg)]) == 1
    assert "Validation failed" in capsys.readouterr().err


def test_cli_generate_schema(tmp_path) -> None:  # type: ignore[no-untyped-def]
    out = tmp_path / "schema.json"
    assert main(["generate-schema", "--out", str(out)]) == 0
    schema = json.loads(out.read_text(encoding="utf-8"))
    assert "pipeline" in json.dumps(schema)
