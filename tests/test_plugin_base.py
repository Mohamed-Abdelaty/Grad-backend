from engine.plugins import base


def test_plugin_base_default_behaviors():
    p = base.Plugin("t", "n")
    assert p.get_name() == "n"
    assert p.get_plugin_type() == "t"
    assert p.healthcheck() == {"healthy": True}
    assert p.validate_config({}) == {}
    try:
        p.process({})
        assert False
    except NotImplementedError:
        pass


def test_register_and_get():
    class Dummy(base.Plugin):
        def __init__(self):
            super().__init__("source", "dummy")
        def process(self, record):
            return record

    from engine import plugins
    plugins.registry.register(Dummy())
    got = plugins.registry.get("source", "dummy")
    assert got is not None
