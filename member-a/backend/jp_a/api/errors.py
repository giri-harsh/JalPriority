"""Error envelope (CONTRACT section 4.5). Raise ``AppError`` subclasses from services and routes."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import ORJSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..logging_setup import log_event, request_id_var

import logging


def error_payload(code: str, message: str, details: dict[str, Any] | None, request_id: str | None) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": request_id or request_id_var.get() or "req_000000000000",
        }
    }


class AppError(Exception):
    code = "INTERNAL_ERROR"
    status_code = 500

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.details = details or {}


class ContractValidationError(AppError):
    code, status_code = "VALIDATION_ERROR", 422


class UnauthorizedError(AppError):
    code, status_code = "UNAUTHORIZED", 401


class ForbiddenError(AppError):
    code, status_code = "FORBIDDEN", 403


class ScenarioNotFoundError(AppError):
    code, status_code = "SCENARIO_NOT_FOUND", 404

    def __init__(self, scenario_id: str):
        super().__init__(f"Scenario {scenario_id} does not exist.", details={"scenario_id": scenario_id})


class DisruptionInvalidError(AppError):
    code, status_code = "DISRUPTION_INVALID", 422


class DisruptionNotApplicableError(AppError):
    code, status_code = "DISRUPTION_NOT_APPLICABLE", 409


class SolverError(AppError):
    code, status_code = "SOLVER_ERROR", 500


class ModelUnavailableError(AppError):
    code, status_code = "MODEL_UNAVAILABLE", 503


def build_error_response(
    status_code: int, code: str, message: str, details: dict | None, request_id: str | None
) -> ORJSONResponse:
    headers = {"X-Request-ID": request_id} if request_id else None
    return ORJSONResponse(status_code=status_code, content=error_payload(code, message, details, request_id), headers=headers)


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None) or request_id_var.get()


_HTTP_CODES = {
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    422: "VALIDATION_ERROR",
}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        level = logging.ERROR if exc.status_code >= 500 else logging.INFO
        log_event("app_error", exc.message, level=level, code=exc.code, status=exc.status_code)
        return build_error_response(exc.status_code, exc.code, exc.message, exc.details, _rid(request))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        errors = [{"loc": [str(p) if not isinstance(p, int) else p for p in e.get("loc", ())], "msg": str(e.get("msg", ""))} for e in exc.errors()]
        return build_error_response(422, "VALIDATION_ERROR", "Request validation failed.", {"errors": errors}, _rid(request))

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code = _HTTP_CODES.get(exc.status_code, "INTERNAL_ERROR" if exc.status_code >= 500 else "VALIDATION_ERROR")
        return build_error_response(exc.status_code, code, str(exc.detail), {}, _rid(request))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):  # pragma: no cover - middleware normally catches first
        log_event("unhandled_exception", repr(exc), level=logging.ERROR)
        return build_error_response(500, "INTERNAL_ERROR", "Internal server error.", {}, _rid(request))
