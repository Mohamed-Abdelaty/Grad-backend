"""Structured JSON logging with request/run identifiers."""

from __future__ import annotations

import json
import logging
import sys
import uuid
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "service": "chaosflow-backend",
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
            "pipeline_id": getattr(record, "pipeline_id", None),
            "run_id": getattr(record, "run_id", None),
        }
        if record.exc_info and record.exc_info[0] is not None:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps({k: v for k, v in payload.items() if v is not None})


def configure_logging(level: str = "INFO") -> logging.Logger:
    """Configure the root logger for JSON output (idempotent)."""
    root = logging.getLogger()
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
    root.setLevel(level.upper())
    return logging.getLogger("chaosflow")


def new_request_id() -> str:
    """Generate a request identifier for log correlation."""
    return uuid.uuid4().hex[:12]

from os import getenv
def configure_logging(level: str | None = None) -> None:
    import logging.config
    import os
    lvl = (level or getenv('LOG_LEVEL') or 'INFO').upper()
    log_dir = getenv('LOG_DIR') or '.'
    os.makedirs(log_dir, exist_ok=True)
    cfg = {
        'version': 1,
        'formatters': {
            'default': {'format': '%(asctime)s %(levelname)s %(name)s %(message)s'},
        },
        'handlers': {
            'console': {'class': 'logging.StreamHandler', 'formatter': 'default'},
            'file': {'class': 'logging.FileHandler', 'formatter': 'default', 'filename': os.path.join(log_dir, 'app.log'), 'mode': 'a'},
        },
        'root': {'level': lvl, 'handlers': ['console', 'file']},
    }
    logging.config.dictConfig(cfg)

def get_logger(name: str):
    return logging.getLogger(name)
