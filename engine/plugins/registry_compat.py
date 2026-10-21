class Registry:
    def __init__(self):
        self._items = {}

    def register(self, obj):
        key = (obj.get_plugin_type(), obj.get_name())
        self._items[key] = obj
        return obj

    def get(self, ptype, name):
        return self._items.get((ptype, name))


registry = Registry()
