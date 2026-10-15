"""REST API source with pagination, timeouts, and bounded requests."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from urllib.parse import urlparse

import httpx

from engine.errors import PluginConfigError
from engine.plugins.base import BaseSource, Batch
from engine.registry import register_source


@register_source("rest_api")
class RestApiSource(BaseSource):
    """Pull records from an HTTP JSON endpoint.

    Config::

        {"url": ..., "method": "GET", "headers": {...}, "params": {...},
         "timeout_seconds": 10, "max_pages": 10, "max_records": 10000,
         "records_path": "items",   # optional dotted path into the JSON body
         "pagination": {"type": "page", "page_param": "page",
                        "size_param": "page_size", "page_size": 100,
                        "start_page": 1}}

    Pagination types: ``none`` (single request), ``page`` (increment a page
    query param until an empty page or ``max_pages``). Every request has a
    timeout and the total number of requests/records is bounded by
    ``max_pages``/``max_records``. HTTP errors raise a descriptive error.
    """

    name = "rest_api"

    @classmethod
    def config_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["url"],
            "properties": {
                "url": {"type": "string"},
                "method": {"type": "string", "enum": ["GET", "POST"], "default": "GET"},
                "headers": {"type": "object", "default": {}},
                "params": {"type": "object", "default": {}},
                "timeout_seconds": {"type": "number", "minimum": 0.1, "default": 10},
                "max_pages": {"type": "integer", "minimum": 1, "default": 10},
                "max_records": {"type": "integer", "minimum": 1, "default": 10000},
                "records_path": {"type": "string"},
                "batch_size": {"type": "integer", "minimum": 1, "default": 500},
                "pagination": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["none", "page"], "default": "none"},
                        "page_param": {"type": "string", "default": "page"},
                        "size_param": {"type": "string", "default": "page_size"},
                        "page_size": {"type": "integer", "minimum": 1, "default": 100},
                        "start_page": {"type": "integer", "minimum": 0, "default": 1},
                    },
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        }

    def validate_config(self) -> None:
        url = self.config.get("url")
        if not url or not isinstance(url, str):
            raise PluginConfigError("?", "rest_api source requires 'url' (string)")
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise PluginConfigError("?", f"rest_api 'url' must be http(s): {url!r}")
        method = str(self.config.get("method", "GET")).upper()
        if method not in ("GET", "POST"):
            raise PluginConfigError("?", "rest_api 'method' must be GET or POST")
        for key in ("timeout_seconds",):
            val = self.config.get(key, 10)
            if not isinstance(val, (int, float)) or val <= 0:
                raise PluginConfigError("?", f"rest_api '{key}' must be a positive number")
        for key in ("max_pages", "max_records", "batch_size"):
            val = self.config.get(key, 1)
            if not isinstance(val, int) or val < 1:
                raise PluginConfigError("?", f"rest_api '{key}' must be a positive integer")

    def _extract_records(self, payload: Any) -> list[dict[str, Any]]:
        path = self.config.get("records_path")
        data = payload
        if path:
            for part in str(path).split("."):
                if not isinstance(data, dict):
                    raise ValueError(f"records_path '{path}' did not resolve to a list")
                data = data.get(part)
        if isinstance(data, dict):
            # Common envelope: {"items": [...], ...} -> pick first list value.
            for value in data.values():
                if isinstance(value, list):
                    data = value
                    break
        if not isinstance(data, list):
            raise ValueError("REST response did not contain a record list")
        records: list[dict[str, Any]] = []
        for item in data:
            if isinstance(item, dict):
                records.append(item)
        return records

    def _fetch_page(self, client: httpx.Client, params: dict[str, Any]) -> Any:
        method = str(self.config.get("method", "GET")).upper()
        timeout = float(self.config.get("timeout_seconds", 10))
        try:
            if method == "POST":
                resp = client.post(str(self.config["url"]), json=params, timeout=timeout)
            else:
                resp = client.get(str(self.config["url"]), params=params, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise TimeoutError(f"REST source timed out after {timeout}s: {exc}") from exc
        except httpx.RequestError as exc:
            raise ConnectionError(f"REST source request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"REST source returned HTTP {resp.status_code}: {resp.text[:500]}")
        try:
            return resp.json()
        except ValueError as exc:
            raise ValueError(f"REST source returned non-JSON body: {exc}") from exc

    def read(self) -> Iterator[Batch]:
        headers = dict(self.config.get("headers") or {})
        base_params = dict(self.config.get("params") or {})
        pagination = dict(self.config.get("pagination") or {})
        ptype = str(pagination.get("type", "none"))
        max_pages = int(self.config.get("max_pages", 10))
        max_records = int(self.config.get("max_records", 10000))
        batch_size = int(self.config.get("batch_size", 500))
        total = 0
        batch: Batch = []
        with httpx.Client(headers=headers) as client:
            if ptype == "none":
                records = self._extract_records(self._fetch_page(client, base_params))
                for record in records[:max_records]:
                    batch.append(record)
                    if len(batch) >= batch_size:
                        yield batch
                        batch = []
            elif ptype == "page":
                page_param = str(pagination.get("page_param", "page"))
                size_param = str(pagination.get("size_param", "page_size"))
                page = int(pagination.get("start_page", 1))
                page_size = int(pagination.get("page_size", 100))
                for _ in range(max_pages):
                    params = {**base_params, page_param: page, size_param: page_size}
                    records = self._extract_records(self._fetch_page(client, params))
                    if not records:
                        break
                    for record in records:
                        if total >= max_records:
                            break
                        batch.append(record)
                        total += 1
                        if len(batch) >= batch_size:
                            yield batch
                            batch = []
                    if total >= max_records or len(records) < page_size:
                        break
                    page += 1
            else:
                raise PluginConfigError("?", f"Unsupported pagination type: {ptype!r}")
        if batch:
            yield batch
