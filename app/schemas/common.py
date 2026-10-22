"""Shared pagination and error response models."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Paginated list envelope used by all list endpoints."""

    items: list[T]
    total: int
    page: int
    page_size: int


class ErrorDetail(BaseModel):
    """Machine-readable error payload."""

    code: str
    message: str
    details: object | None = None

    model_config = {
        "json_schema_extra": {
            "example": {
                "code": "not_found",
                "message": "Resource not found",
                "details": None,
            }
        }
    }


class ErrorResponse(BaseModel):
    """Canonical error response body."""

    error: ErrorDetail

    model_config = {
        "json_schema_extra": {
            "example": {
                "error": {
                    "code": "not_found",
                    "message": "Resource not found",
                    "details": None,
                }
            }
        }
    }


def page_params(page: int = 1, page_size: int = 20) -> dict[str, int]:
    """Clamp raw pagination query params into valid ranges."""
    return {
        "page": max(1, page),
        "page_size": min(100, max(1, page_size)),
    }
