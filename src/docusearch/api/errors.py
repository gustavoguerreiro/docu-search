"""One error body for the whole API.

    {"error": {"code": "invalid_credentials", "message": "...", "details": [...]}}

`code` is stable and meant for programs, `message` for people. Validation failures list `loc`,
`msg` and `type` for each problem but never the offending value: FastAPI's default includes it,
which would echo a submitted password back in the response. Unexpected errors return a generic 500
with no internals; the exception is logged server-side.
"""

import logging
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

_STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    429: "too_many_requests",
}


class ErrorDetail(BaseModel):
    loc: list[str | int]
    msg: str
    type: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class ApiError(Exception):
    """An error the client is meant to see. Raise it from routes and dependencies."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers


def _response(
    status_code: int,
    code: str,
    message: str,
    *,
    details: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse({"error": error}, status_code=status_code, headers=headers)


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """The common error body as a response, for code that runs outside a route (middleware)."""
    return _response(status_code, code, message)


async def _api_error(_: Request, exc: Exception) -> JSONResponse:
    error = cast(ApiError, exc)
    return _response(error.status_code, error.code, error.message, headers=error.headers)


async def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    errors = cast(RequestValidationError, exc).errors()
    details = [
        {
            "loc": [str(part) if isinstance(part, str) else part for part in err["loc"]],
            "msg": str(err["msg"]),
            "type": str(err["type"]),
        }
        for err in errors
    ]
    return _response(422, "validation_error", "The request is not valid.", details=details)


async def _http_error(_: Request, exc: Exception) -> JSONResponse:
    error = cast(StarletteHTTPException, exc)
    code = _STATUS_CODES.get(error.status_code, "http_error")
    message = (
        error.detail if isinstance(error.detail, str) else "The request could not be processed."
    )
    return _response(error.status_code, code, message, headers=dict(error.headers or {}) or None)


async def _unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled exception", exc_info=exc)
    return _response(500, "internal_error", "An internal error occurred.")


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unexpected_error)
