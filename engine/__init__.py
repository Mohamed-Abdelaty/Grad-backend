from engine.executor import Executor


def execute_pipeline(db=None, run_id=None, pipeline_id=None, **kwargs):
    """Execute a pipeline (compat shim)."""
    doc = kwargs.get("doc")
    if doc is None:
        try:
            from engine.config import PipelineDocument, PipelineConfig
            doc = PipelineDocument(
                pipeline=PipelineConfig(
                    id=str(pipeline_id or "x"),
                    nodes=[],
                    edges=[],
                    retry={"max_attempts": 1, "backoff_seconds": 1},
                )
            )
        except Exception:
            from engine import config as cfg_mod
            doc = cfg_mod.PipelineDocument.parse_obj(
                {
                    "pipeline": {
                        "id": str(pipeline_id or "x"),
                        "nodes": [],
                        "edges": [],
                        "retry": {"max_attempts": 1, "backoff_seconds": 1},
                    }
                }
            )
    ex = Executor(doc.pipeline)
    return ex.run()
