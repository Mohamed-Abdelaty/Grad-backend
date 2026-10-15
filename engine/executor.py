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
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import engine.plugins  # noqa: F401  (registers built-ins)
from engine.config import PipelineConfig, PipelineDocument
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

    Raises:
        ConfigValidationError: with field-level ``details`` on any failure.
    """
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
    ) -> None:
        self.pipeline = pipeline
        self._callback = event_callback
        self.batch_size = max(1, batch_size)
        self.max_backoff = max_backoff_seconds
        self._events: list[ExecutionEvent] = []

    # -- helpers ---------------------------------------------------------
    def _emit(self, event: ExecutionEvent) -> None:
        self._events.append(event)
        if self._callback is not None:
            with contextlib.suppress(Exception):
                self._callback(event)  # callbacks must never break execution

    def _sleep_interruptible(self, seconds: float, cancellation: CancellationToken | None) -> None:
        deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < deadline:
            if cancellation is not None and cancellation.is_cancelled:
                raise CancellationError()
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    # -- validation ------------------------------------------------------
    def validate(self) -> None:
        """Validate plugin existence + plugin configs. Raises typed errors."""
        for node in self.pipeline.nodes:
            try:
                plugin_cls = lookup(node.type, node.plugin)
            except UnknownPluginError:
                raise
            except EngineError:
                raise
            plugin = plugin_cls(node.config)
            if not isinstance(plugin, (BaseSource, BaseTransform, BaseSink)):
                raise ConfigValidationError(
                    f"Node '{node.id}': plugin has wrong category base class"
                )
            try:
                plugin.validate_config()
            except PluginConfigError as exc:
                # Attach the real node id (plugins use "?" as placeholder).
                raise PluginConfigError(node.id, str(exc).split(": ", 1)[-1]) from exc
            except ValueError as exc:
                raise PluginConfigError(node.id, str(exc)) from exc
            # Category sanity: source nodes must be sources, etc.
            expected = {"source": BaseSource, "transform": BaseTransform, "sink": BaseSink}[
                node.type
            ]
            if not isinstance(plugin, expected):
                raise ConfigValidationError(
                    f"Node '{node.id}': plugin '{node.plugin}' is not a {node.type} plugin"
                )

    def topological_order(self) -> list[str]:
        """Return node ids in execution order; raise on cycles."""
        indegree = {n.id: 0 for n in self.pipeline.nodes}
        adjacency: dict[str, list[str]] = {n.id: [] for n in self.pipeline.nodes}
        for edge in self.pipeline.edges:
            adjacency[edge.source].append(edge.target)
            indegree[edge.target] += 1
        queue = sorted(nid for nid, deg in indegree.items() if deg == 0)
        order: list[str] = []
        while queue:
            current = queue.pop(0)
            order.append(current)
            for nxt in sorted(adjacency[current]):
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    queue.append(nxt)
        if len(order) != len(self.pipeline.nodes):
            raise ConfigValidationError("Pipeline graph must be acyclic (cycle detected)")
        return order

    # -- execution -------------------------------------------------------
    def run(self, cancellation: CancellationToken | None = None) -> RunResult:
        """Execute the pipeline; always cleans up opened resources."""
        self.validate()
        order = self.topological_order()
        node_map = self.pipeline.node_map()
        plugins: dict[str, BaseSource | BaseTransform | BaseSink] = {}
        for node_id in order:
            node = node_map[node_id]
            plugins[node_id] = lookup(node.type, node.plugin)(node.config)
        opened: list[str] = []
        metrics: dict[str, NodeMetrics] = {nid: NodeMetrics(node_id=nid) for nid in order}
        outputs: dict[str, Batch] = {}
        cancelled = False
        try:
            for node_id in order:
                if cancellation is not None and cancellation.is_cancelled:
                    cancelled = True
                    self._emit(
                        ExecutionEvent(type="cancelled", node_id=node_id, message="Run cancelled")
                    )
                    break
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
                    outputs[node_id] = out
                except CancellationError:
                    cancelled = True
                    self._emit(
                        ExecutionEvent(type="cancelled", node_id=node_id, message="Run cancelled")
                    )
                    break
                except NodeExecutionError as exc:
                    metric.duration_seconds = time.monotonic() - started
                    self._emit(
                        ExecutionEvent(
                            type="node_failed",
                            node_id=node_id,
                            message=str(exc),
                            attempt=metric.attempts,
                        )
                    )
                    raise
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
            out.extend(dict(r) for r in batch)
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
        metric.records_in += len(merged)
        for batch_index, i in enumerate(range(0, len(merged), self.batch_size)):
            if cancellation is not None and cancellation.is_cancelled:
                raise CancellationError()
            chunk = merged[i : i + self.batch_size]
            written = plugin.write(chunk)
            metric.records_out += written
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
        return []
