import uuid

from engine.executor import Executor


def execute_pipeline(db=None, run_id=None, pipeline_id=None, **kwargs):
    """Execute a pipeline (compat shim)."""
    doc = kwargs.get("doc")
    if doc is None:
        from engine.config import PipelineConfig, PipelineDocument
        pid = uuid.UUID(str(pipeline_id)) if pipeline_id and str(pipeline_id) != "x" else uuid.uuid4()
        doc = PipelineDocument(
            pipeline=PipelineConfig(
                id=pid,
                name="compat-pipeline",
                nodes=[
                    {"id": "src", "type": "source", "plugin": "csv", "config": {"path": "examples/data/orders.csv"}},
                    {"id": "snk", "type": "sink", "plugin": "console", "config": {}},
                ],
                edges=[{"from": "src", "to": "snk"}],
                retry={"max_attempts": 1, "backoff_seconds": 0},
            )
        )
    ex = Executor(doc.pipeline)
    return ex.run()
