"""DAG executor: validates, orders, runs, retries, measures, cancels.

Execution model (batch, in-process):
- The pipeline graph is topologically sorted (Kahn's algorithm); a cycle
  raises :class:`ConfigValidationError` even though the Pydantic contract
  already rejects cycles structurally (defense in depth).
- Each node runs exactly once per run, in topological order. A node's full
  output (list of records) is materialized before downstream nodes start.
  This keeps join/aggregate semantics simple; the documented limitation is
  memory usage ~ dataset size (streaming pagination is future work).
- Records flow in chunks of ``batch_size`` so ``batch_processed`` events,
  progress reporting, and cancellation checks are fine-grained.
- Fresh plugin instances are created per :meth:`Executor.run`, so concurrent
  runs never share mutable plugin state.
- ``max_attempts`` INCLUDES the initial attempt (attempt 1 = first try).
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import engine.plugins  # noqa: F401  (registers built-ins)
from engine.config import PipelineConfig, PipelineDocument, collect_all_pipeline_errors
from engine.errors import (
    CancellationError,
    ConfigValidationError,
    EngineError,
    NodeExecutionError,
    PluginConfigError,
    UnknownPluginError,
)
from engine.plugins.base import BaseSink, BaseSource, BaseTransform, Batch
from engine.registry import lookup

EventCallback = Callable[["ExecutionEvent"], None]


@dataclass
class ExecutionEvent:
    """Structured execution event emitted through the callback."""

    type: str  # node_started|batch_processed|node_finished|node_failed|retry_attempt|cancelled|run_finished
    node_id: str | None = None
    message: str = ""
    batch_index: int | None = None
    records_in: int = 0
    records_out: int = 0
    attempt: int = 1
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class NodeMetrics:
    """Per-node execution metrics."""

    node_id: str
    records_in: int = 0
    records_out: int = 0
    duration_seconds: float = 0.0
    error_count: int = 0
    attempts: int = 0


@dataclass
class RunResult:
    """Outcome of :meth:`Executor.run`."""

    metrics: dict[str, NodeMetrics]
    events: list[ExecutionEvent]
    cancelled: bool = False


class CancellationToken:
    """Cooperative cancellation flag shared with the background worker."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Request cancellation."""
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        """Whether cancellation was requested."""
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        """Raise :class:`CancellationError` if cancellation was requested."""
        if self._event.is_set():
            raise CancellationError()


def validate_pipeline_dict(payload: dict[str, Any]) -> PipelineDocument:
    """Validate a raw ``{"pipeline": ...}`` document structurally + per-plugin.

    Collects all errors so callers see complete diagnostics instead of failing fast.
    """
    errors = collect_all_pipeline_errors(payload)
    if errors:
        if len(errors) == 1:
            err = errors[0]
            if "Unknown " in err and " plugin" in err:
                parts = err.split("Unknown ")[1].split(" plugin: '")
                cat = parts[0]
                pname = parts[1].rstrip("'")
                raise UnknownPluginError(cat, pname)
            if err.startswith("Node '") and ": " in err:
                node_id = err.split("Node '")[1].split("'")[0]
                msg = err.split(": ", 1)[1]
                raise PluginConfigError(node_id, msg)
            raise ConfigValidationError(err, details={"error": err})
        raise ConfigValidationError(
            f"Pipeline validation failed with {len(errors)} error(s):\n"
            + "\n".join(f"- {e}" for e in errors),
            details={"errors": errors},
            errors=errors,
        )

    try:
        doc = PipelineDocument.model_validate(payload)
    except Exception as exc:
        raise ConfigValidationError(
            f"Invalid pipeline document: {exc}", details={"error": str(exc)}
        ) from exc
    # Semantic plugin-config validation with node ids attached.
    executor = Executor(doc.pipeline)
    executor.validate()
    return doc


class Executor:
    """Reusable DAG executor (no FastAPI / SQLAlchemy / HTTP imports)."""

    def __init__(
        self,
        pipeline: PipelineConfig,
        event_callback: EventCallback | None = None,
        batch_size: int = 500,
        max_backoff_seconds: float = 60.0,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self.pipeline = pipeline
        self._callback = event_callback
        self.batch_size = max(1, batch_size)
        self.max_backoff = max_backoff_seconds
        self._sleep_fn = sleep_fn
        self._events: list[ExecutionEvent] = []
        self.last_metrics: dict[str, NodeMetrics] = {}

    # -- helpers ---------------------------------------------------------
    def _emit(self, event: ExecutionEvent) -> None:
        self._events.append(event)
        if self._callback is not None:
            with contextlib.suppress(Exception):
                self._callback(event)  # callbacks must never break execution

    def _sleep_interruptible(self, seconds: float, cancellation: CancellationToken | None) -> None:
        if self._sleep_fn is not None:
            self._sleep_fn(seconds)
            if cancellation is not None and cancellation.is_cancelled:
                raise CancellationError()
            return
        deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < deadline:
            if cancellation is not None and cancellation.is_cancelled:
                raise CancellationError()
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    # -- validation ------------------------------------------------------
    def validate(self) -> None:
        """Validate plugin existence + plugin configs. Raises typed errors."""
        errors: list[str] = []
        unknown_err: UnknownPluginError | None = None
        plugin_err: PluginConfigError | None = None

        for node in self.pipeline.nodes:
            try:
                plugin_cls = lookup(node.type, node.plugin)
            except UnknownPluginError as exc:
                errors.append(str(exc))
                if unknown_err is None:
                    unknown_err = exc
                continue
            except EngineError as exc:
                errors.append(str(exc))
                continue
            plugin = plugin_cls(node.config)
            if not isinstance(plugin, (BaseSource, BaseTransform, BaseSink)):
                errors.append(f"Node '{node.id}': plugin has wrong category base class")
                continue
            try:
                plugin.validate_config()
            except PluginConfigError as exc:
                msg = str(exc).split(": ", 1)[-1]
                errors.append(f"Node '{node.id}': {msg}")
                if plugin_err is None:
                    plugin_err = PluginConfigError(node.id, msg)
            except ValueError as exc:
                errors.append(f"Node '{node.id}': {exc}")
                if plugin_err is None:
                    plugin_err = PluginConfigError(node.id, str(exc))
            expected = {"source": BaseSource, "transform": BaseTransform, "sink": BaseSink}[
                node.type
            ]
            if not isinstance(plugin, expected):
                errors.append(
                    f"Node '{node.id}': plugin '{node.plugin}' is not a {node.type} plugin"
                )

        if len(errors) == 1:
            if unknown_err is not None:
                raise unknown_err
            if plugin_err is not None:
                raise plugin_err
            raise ConfigValidationError(errors[0])
        elif len(errors) > 1:
            raise ConfigValidationError(
                f"Plugin validation failed with {len(errors)} error(s): " + "; ".join(errors),
                details={"errors": errors},
                errors=errors,
            )

    def topological_order(self) -> list[str]:
        """Return node ids in execution order; raise on cycles."""
        indegree = {n.id: 0 for n in self.pipeline.nodes}
        adjacency: dict[str, list[str]] = {n.id: [] for n in self.pipeline.nodes}
        for edge in self.pipeline.edges:
            adjacency[edge.source].append(edge.target)
            indegree[edge.target] += 1
        queue = deque(sorted(nid for nid, deg in indegree.items() if deg == 0))
        order: list[str] = []
        while queue:
            current = queue.popleft()
            order.append(current)
            for nxt in sorted(adjacency[current]):
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    queue.append(nxt)
        if len(order) != len(self.pipeline.nodes):
            raise ConfigValidationError("Pipeline graph must be acyclic (cycle detected)")
        return order

    def _get_descendants(self, node_id: str) -> set[str]:
        """Return all downstream descendants of node_id in the DAG."""
        descendants: set[str] = set()
        queue = deque(self.pipeline.successors(node_id))
        while queue:
            curr = queue.popleft()
            if curr not in descendants:
                descendants.add(curr)
                queue.extend(self.pipeline.successors(curr))
        return descendants

    # -- execution -------------------------------------------------------
    def run(self, cancellation: CancellationToken | None = None) -> RunResult:
        """Execute the pipeline; always cleans up opened resources with failure isolation."""
        self.validate()
        order = self.topological_order()
        node_map = self.pipeline.node_map()
        plugins: dict[str, BaseSource | BaseTransform | BaseSink] = {}
        for node_id in order:
            node = node_map[node_id]
            plugins[node_id] = lookup(node.type, node.plugin)(node.config)
        opened: list[str] = []
        metrics: dict[str, NodeMetrics] = {nid: NodeMetrics(node_id=nid) for nid in order}
        self.last_metrics = metrics
        outputs: dict[str, Batch] = {}
        consumer_counts = {nid: len(self.pipeline.successors(nid)) for nid in order}
        skipped_nodes: set[str] = set()
        failed_nodes: dict[str, NodeExecutionError] = {}
        cancelled = False
        try:
            for node_id in order:
                if cancellation is not None and cancellation.is_cancelled:
                    cancelled = True
                    self._emit(
                        ExecutionEvent(type="cancelled", node_id=node_id, message="Run cancelled")
                    )
                    break

                if node_id in skipped_nodes:
                    self._emit(
                        ExecutionEvent(
                            type="node_skipped",
                            node_id=node_id,
                            message=f"Node '{node_id}' skipped because an upstream dependency failed",
                        )
                    )
                    continue

                node = node_map[node_id]
                plugin = plugins[node_id]
                metric = metrics[node_id]
                self._emit(ExecutionEvent(type="node_started", node_id=node_id))
                started = time.monotonic()
                try:
                    out = self._run_node_with_retry(
                        node_id=node_id,
                        plugin=plugin,
                        inputs={
                            pred: outputs.get(pred, [])
                            for pred in self.pipeline.predecessors(node_id)
                        },
                        metric=metric,
                        opened=opened,
                        cancellation=cancellation,
                    )
                    for pred in self.pipeline.predecessors(node_id):
                        consumer_counts[pred] -= 1
                        if consumer_counts[pred] <= 0:
                            outputs.pop(pred, None)
                    if consumer_counts.get(node_id, 0) > 0:
                        outputs[node_id] = out
                    del out
                except CancellationError:
                    cancelled = True
                    self._emit(
                        ExecutionEvent(type="cancelled", node_id=node_id, message="Run cancelled")
                    )
                    break
                except NodeExecutionError as exc:
                    metric.duration_seconds = time.monotonic() - started
                    failed_nodes[node_id] = exc
                    self._emit(
                        ExecutionEvent(
                            type="node_failed",
                            node_id=node_id,
                            message=str(exc),
                            attempt=metric.attempts,
                        )
                    )
                    # Downstream dependents are marked skipped, independent branches continue
                    skipped_nodes.update(self._get_descendants(node_id))
                    continue
                metric.duration_seconds = time.monotonic() - started
                self._emit(
                    ExecutionEvent(
                        type="node_finished",
                        node_id=node_id,
                        records_in=metric.records_in,
                        records_out=metric.records_out,
                        attempt=metric.attempts,
                    )
                )
        finally:
            for node_id in reversed(opened):
                with contextlib.suppress(Exception):
                    plugins[node_id].close()
        if cancelled:
            raise CancellationError()
        if failed_nodes:
            first_fail_node = next(iter(failed_nodes))
            first_exc = failed_nodes[first_fail_node]
            first_exc.metrics = metrics
            self._emit(ExecutionEvent(type="run_failed", node_id=first_fail_node, message=str(first_exc)))
            raise first_exc
        self._emit(ExecutionEvent(type="run_finished", message="Run completed"))
        return RunResult(metrics=metrics, events=list(self._events), cancelled=False)

    def _run_node_with_retry(
        self,
        *,
        node_id: str,
        plugin: BaseSource | BaseTransform | BaseSink,
        inputs: dict[str, Batch],
        metric: NodeMetrics,
        opened: list[str],
        cancellation: CancellationToken | None,
    ) -> Batch:
        retry = self.pipeline.retry
        last_error: Exception | None = None
        for attempt in range(1, retry.max_attempts + 1):
            metric.attempts = attempt
            if cancellation is not None and cancellation.is_cancelled:
                raise CancellationError()
            try:
                return self._run_node_once(
                    node_id=node_id,
                    plugin=plugin,
                    inputs=inputs,
                    metric=metric,
                    opened=opened,
                    cancellation=cancellation,
                    attempt=attempt,
                )
            except CancellationError:
                raise
            except (PluginConfigError, UnknownPluginError):
                raise
            except NodeExecutionError:
                raise
            except Exception as exc:
                last_error = exc
                metric.error_count += 1
                if attempt < retry.max_attempts:
                    delay = min(
                        self.pipeline.retry.backoff_seconds * (2 ** (attempt - 1)),
                        self.max_backoff,
                    )
                    self._emit(
                        ExecutionEvent(
                            type="retry_attempt",
                            node_id=node_id,
                            message=f"Attempt {attempt} failed ({type(exc).__name__}): {exc}. "
                            f"Retrying in {delay:.2f}s",
                            attempt=attempt,
                        )
                    )
                    self._sleep_interruptible(delay, cancellation)
                continue
        assert last_error is not None
        raise NodeExecutionError(
            node_id,
            f"{type(last_error).__name__}: {last_error}",
            error_type=type(last_error).__name__,
            attempts=metric.attempts,
        )

    def _run_node_once(
        self,
        *,
        node_id: str,
        plugin: BaseSource | BaseTransform | BaseSink,
        inputs: dict[str, Batch],
        metric: NodeMetrics,
        opened: list[str],
        cancellation: CancellationToken | None,
        attempt: int,
    ) -> Batch:
        node = self.pipeline.node_map()[node_id]
        if isinstance(plugin, BaseSource):
            return self._run_source(node_id, plugin, metric, opened, cancellation, attempt)
        if isinstance(plugin, BaseTransform):
            return self._run_transform(
                node_id,
                node_type=node.type,
                plugin=plugin,
                inputs=inputs,
                metric=metric,
                cancellation=cancellation,
                attempt=attempt,
            )
        return self._run_sink(node_id, plugin, inputs, metric, opened, cancellation, attempt)

    def _open_once(self, node_id: str, plugin: BaseSource | BaseSink, opened: list[str]) -> None:
        if node_id not in opened:
            plugin.open()
            opened.append(node_id)

    def _run_source(
        self,
        node_id: str,
        plugin: BaseSource,
        metric: NodeMetrics,
        opened: list[str],
        cancellation: CancellationToken | None,
        attempt: int,
    ) -> Batch:
        self._open_once(node_id, plugin, opened)
        out: Batch = []
        for batch_index, batch in enumerate(plugin.read()):
            if cancellation is not None and cancellation.is_cancelled:
                raise CancellationError()
            metric.records_in += len(batch)
            metric.records_out += len(batch)
            out.extend(batch)
            self._emit(
                ExecutionEvent(
                    type="batch_processed",
                    node_id=node_id,
                    batch_index=batch_index,
                    records_in=len(batch),
                    records_out=len(batch),
                    attempt=attempt,
                )
            )
        return out

    def _run_transform(
        self,
        node_id: str,
        node_type: str,
        plugin: BaseTransform,
        inputs: dict[str, Batch],
        metric: NodeMetrics,
        cancellation: CancellationToken | None,
        attempt: int,
    ) -> Batch:
        _ = node_type
        out: Batch = []
        if getattr(plugin, "multi_input", False):
            if cancellation is not None and cancellation.is_cancelled:
                raise CancellationError()
            total_in = sum(len(v) for v in inputs.values())
            result = plugin.process_many({k: list(v) for k, v in inputs.items()})
            metric.records_in += total_in
            metric.records_out += len(result)
            out.extend(result)
            self._emit(
                ExecutionEvent(
                    type="batch_processed",
                    node_id=node_id,
                    batch_index=0,
                    records_in=total_in,
                    records_out=len(result),
                    attempt=attempt,
                )
            )
            return out
        merged: Batch = []
        for records in inputs.values():
            merged.extend(records)
        metric.records_in += len(merged)
        for batch_index, i in enumerate(range(0, len(merged), self.batch_size)):
            if cancellation is not None and cancellation.is_cancelled:
                raise CancellationError()
            chunk = merged[i : i + self.batch_size]
            result = plugin.process(chunk)
            metric.records_out += len(result)
            out.extend(result)
            self._emit(
                ExecutionEvent(
                    type="batch_processed",
                    node_id=node_id,
                    batch_index=batch_index,
                    records_in=len(chunk),
                    records_out=len(result),
                    attempt=attempt,
                )
            )
        finish = getattr(plugin, "finish", None)
        if callable(finish) and hasattr(plugin, "_buffer"):
            # Stateful transforms (aggregate) emit their final rows here.
            final_rows = finish()
            if final_rows:
                metric.records_out += len(final_rows)
                out.extend(final_rows)
                self._emit(
                    ExecutionEvent(
                        type="batch_processed",
                        node_id=node_id,
                        batch_index=len(merged),
                        records_in=0,
                        records_out=len(final_rows),
                        attempt=attempt,
                    )
                )
        return out

    def _run_sink(
        self,
        node_id: str,
        plugin: BaseSink,
        inputs: dict[str, Batch],
        metric: NodeMetrics,
        opened: list[str],
        cancellation: CancellationToken | None,
        attempt: int,
    ) -> Batch:
        self._open_once(node_id, plugin, opened)
        merged: Batch = []
        for records in inputs.values():
            merged.extend(records)
        metric.records_in = len(merged)
        written_total = 0
        try:
            for batch_index, i in enumerate(range(0, len(merged), self.batch_size)):
                if cancellation is not None and cancellation.is_cancelled:
                    raise CancellationError()
                chunk = merged[i : i + self.batch_size]
                written = plugin.write(chunk)
                written_total += written
                self._emit(
                    ExecutionEvent(
                        type="batch_processed",
                        node_id=node_id,
                        batch_index=batch_index,
                        records_in=len(chunk),
                        records_out=written,
                        attempt=attempt,
                    )
                )
            metric.records_out = written_total
        except Exception:
            if node_id in opened:
                with contextlib.suppress(Exception):
                    plugin.close()
                opened.remove(node_id)
            raise
        return []
