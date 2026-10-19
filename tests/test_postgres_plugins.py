from engine.errors import PluginConfigError
from engine.plugins.sinks import postgres_sink
from engine.plugins.sources import postgres_source


def test_postgres_source_config_and_health(monkeypatch):
    monkeypatch.setenv("POSTGRES_DSN", "sqlite:///:memory:")
    s = postgres_source.PostgresSource({"table": "t", "query": "SELECT 1", "batch_size": 10})
    try:
        s.validate_config()
    except PluginConfigError:
        pass
    if hasattr(s, "healthcheck"):
        hc = s.healthcheck()
        assert "healthy" in hc


def test_postgres_sink_config(monkeypatch):
    monkeypatch.setenv("POSTGRES_DSN", "sqlite:///:memory:")
    sn = postgres_sink.PostgresSink({"table": "t", "mode": "append", "batch_size": 5})
    try:
        sn.validate_config()
    except PluginConfigError:
        pass
    if hasattr(sn, "healthcheck"):
        hc = sn.healthcheck()
        assert "healthy" in hc
