"""CLI: ``python -m engine run pipeline.json``.

Uses the same :class:`engine.executor.Executor` implementation as the REST
API. Exit codes: 0 = success, 1 = validation/execution failure,
2 = usage error, 130 = cancelled (SIGINT).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import signal
import sys
from pathlib import Path
from typing import Any

from engine.errors import CancellationError, EngineError
from engine.executor import CancellationToken, ExecutionEvent, Executor, validate_pipeline_dict


def load_document(path: Path) -> dict[str, Any]:
    """Load and JSON-parse ``path``; raise a clear error on failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Error: file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Error: invalid JSON in {path}: {exc}") from exc


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m engine", description="ChaosFlow pipeline engine CLI"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="Validate and execute a pipeline JSON file")
    run_p.add_argument("pipeline", type=Path, help='Path to pipeline.json ({"pipeline": {...}})')
    run_p.add_argument("--batch-size", type=int, default=500, help="Records per batch event")
    schema_p = sub.add_parser("generate-schema", help="Print/persist the canonical JSON Schema")
    schema_p.add_argument("--out", type=Path, default=None, help="Write schema to this path")
    return parser


def cmd_run(args: argparse.Namespace) -> int:
    """Execute the ``run`` subcommand."""
    try:
        payload = load_document(args.pipeline)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    try:
        doc = validate_pipeline_dict(payload)
    except EngineError as exc:
        print(f"Validation failed: {exc}", file=sys.stderr)
        return 1
    token = CancellationToken()

    def _on_sigint(signum: int, frame: Any) -> None:
        _ = signum, frame
        token.cancel()

    with contextlib.suppress(ValueError, OSError):
        # No-op outside the main thread.
        signal.signal(signal.SIGINT, _on_sigint)  # type: ignore[arg-type]

    def on_event(event: ExecutionEvent) -> None:
        if event.type == "node_started":
            print(f"[{event.node_id}] started")
        elif event.type == "batch_processed":
            print(
                f"[{event.node_id}] batch {event.batch_index}: {event.records_in} in / {event.records_out} out"
            )
        elif event.type == "node_finished":
            print(f"[{event.node_id}] finished ({event.records_in} in / {event.records_out} out)")
        elif event.type == "node_failed":
            print(f"[{event.node_id}] FAILED: {event.message}", file=sys.stderr)
        elif event.type == "retry_attempt":
            print(f"[{event.node_id}] retry: {event.message}")
        elif event.type == "cancelled":
            print("Run cancelled", file=sys.stderr)

    executor = Executor(doc.pipeline, event_callback=on_event, batch_size=args.batch_size)
    try:
        result = executor.run(token)
    except CancellationError:
        print("Run cancelled", file=sys.stderr)
        return 130
    except EngineError as exc:
        print(f"Execution failed: {exc}", file=sys.stderr)
        return 1
    print("Run completed:")
    for node_id, metric in result.metrics.items():
        print(
            f"  {node_id}: in={metric.records_in} out={metric.records_out} "
            f"attempts={metric.attempts} errors={metric.error_count} "
            f"duration={metric.duration_seconds:.3f}s"
        )
    return 0


def cmd_generate_schema(out: Path | None) -> int:
    """Execute the ``generate-schema`` subcommand."""
    from engine.config import PipelineDocument

    schema = PipelineDocument.model_json_schema()
    text = json.dumps(schema, indent=2) + "\n"
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"Wrote JSON Schema to {out}")
    else:
        print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return cmd_run(args)
    if args.command == "generate-schema":
        return cmd_generate_schema(args.out)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
