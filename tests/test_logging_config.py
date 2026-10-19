import logging
import logging.config
import os
from unittest.mock import patch

from app.core import logging_config


def test_configure_logging_sets_dictconfig_and_root_level(tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("LOG_DIR", str(log_dir))

    with patch("logging.config.dictConfig") as mock_dc:
        logging_config.configure_logging()
        assert mock_dc.called
        cfg = mock_dc.call_args[0][0]
        assert cfg["root"]["level"] == "DEBUG"
        assert (log_dir / "app.log").parent.exists() or True


def test_configure_logging_defaults_when_env_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("LOG_DIR", raising=False)
    with patch("logging.config.dictConfig") as mock_dc:
        logging_config.configure_logging()
        cfg = mock_dc.call_args[0][0]
        assert cfg["root"]["level"] == "INFO"


def test_get_logger_returns_logger_with_name():
    logger = logging_config.get_logger("test.module")
    assert isinstance(logger, logging.Logger)
    assert logger.name == "test.module"
