from fastapi import Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
import secrets
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

class APIError(Exception):
    def __init__(self, code: str, message: str, status_code: int, details: Dict[str, Any] = None):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}

async def api_error_handler(request: Request, exc: APIError):
    request_id = request.state.request_id if hasattr(request.state, "request_id") else "req_unknown"
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
                "request_id": request_id
            }
        }
    )

async def validation_exception_handler(request: Request, exc: RequestValidationError):
    request_id = request.state.request_id if hasattr(request.state, "request_id") else "req_unknown"
    errors = exc.errors()
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Request validation failed.",
                "details": {"errors": [{"loc": err["loc"], "msg": err["msg"]} for err in errors]},
                "request_id": request_id
            }
        }
    )

async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception", extra={"request_id": getattr(request.state, "request_id", None)})
    request_id = request.state.request_id if hasattr(request.state, "request_id") else "req_unknown"
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An internal server error occurred.",
                "details": {},
                "request_id": request_id
            }
        }
    )
