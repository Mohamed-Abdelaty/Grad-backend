from __future__ import annotations

import pytest

from engine.plugins import base


class _DummyTransform(base.BaseTransform):
    @classmethod
    def config_schema(cls) -> dict:
        return {"type": "object", "properties": {}}

    def validate_config(self) -> None:
        return None

    def process(self, batch):
        return batch


def test_plugin_base_default_behaviors():
    p = _DummyTransform({"a": 1})
    assert p.config == {"a": 1}
    assert p.is_open is False
    p.open()
    assert p.is_open is True
    p.close()
    assert p.is_open is False
    # close is idempotent and safe without open
    p.close()
    assert p.is_open is False
    assert p.process([{"x": 1}]) == [{"x": 1}]


def test_abstract_plugin_requires_config_schema():
    with pytest.raises(NotImplementedError):
        base.BasePlugin.config_schema()


def test_register_and_get():
    from engine.registry import lookup, register_transform

    class Dummy(base.BaseTransform):
        @classmethod
        def config_schema(cls) -> dict:
            return {"type": "object"}

        def validate_config(self) -> None:
            return None

        def process(self, batch):
            return batch

    register_transform("dummy_test")(Dummy)
    got = lookup("transform", "dummy_test")
    assert got is Dummy
