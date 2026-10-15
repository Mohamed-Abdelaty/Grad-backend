"""Consistent API error format: {"error": {"code", "message", "details"}}."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(StarletteHTTPException):
    """Typed HTTP error with a stable machine-readable ``code``."""

    def __init__(self, status_code: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(status_code=status_code, detail=message)
        self.code = code
        self.details = details


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    """Build the canonical error response body (no secrets)."""
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return body


def register_exception_handlers(app: FastAPI) -> None:
    """Register consistent handlers for validation, API, and unknown errors."""

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        _ = request
        return JSONResponse(
            status_code=exc.status_code, content=error_body(exc.code, str(exc.detail), exc.details)
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        _ = request
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=error_body("validation_error", "Request validation failed", exc.errors()),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        _ = request
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body("http_error", str(exc.detail)),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        _ = request, exc
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_body("internal_error", "An unexpected error occurred"),
        )
