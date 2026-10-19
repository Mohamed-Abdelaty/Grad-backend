class Plugin:
    def __init__(self, plugin_type=None, name=None, config=None):
        self._type = plugin_type or "unknown"
        self._name = name or "plugin"
        self.config = config or {}

    def get_name(self):
        return self._name

    def get_plugin_type(self):
        return self._type

    def healthcheck(self):
        return {"healthy": True}

    def validate_config(self, config=None):
        return {}

    def process(self, *args, **kwargs):
        raise NotImplementedError
