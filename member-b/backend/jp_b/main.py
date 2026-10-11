from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
import secrets
from jp_b.request_context import request_id_var
import logging
from contextvars import ContextVar

from jp_b.config import settings
from jp_b.logging_setup import setup_logging
from jp_b.api.errors import (
    APIError, api_error_handler,
    validation_exception_handler,
    global_exception_handler
)
from jp_b.api.routes_health import router as health_router
from jp_b.api.routes_auth import router as auth_router
from jp_b.api.routes_scenarios import router as scenarios_router
from jp_b.api.routes_ledger import router as ledger_router
from jp_b.api.routes_plans import router as plans_router
from jp_b.api.routes_decisions import router as decisions_router
from jp_b.api.routes_comparisons import router as comparisons_router
from jp_b.api.routes_replans import router as replans_router
from jp_b.api.routes_weights import router as weights_router
from jp_b.api.routes_events import router as events_router
from jp_b.api.routes_audit import router as audit_router

setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="JalPriority AI - Member B")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.b_cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    req_id = request.headers.get("X-Request-ID")
    if not req_id:
        req_id = f"req_{secrets.token_hex(6)}"
    request.state.request_id = req_id
    token = request_id_var.set(req_id)

    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers["X-Request-ID"] = req_id
    return response

app.add_exception_handler(APIError, api_error_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, global_exception_handler)

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(scenarios_router)
app.include_router(ledger_router)
app.include_router(plans_router)
app.include_router(decisions_router)
app.include_router(comparisons_router)
app.include_router(replans_router)
app.include_router(weights_router)
app.include_router(events_router)
app.include_router(audit_router)


from jp_b.api.deps import get_current_user
from fastapi import Depends, HTTPException

@app.api_route("/b/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"], dependencies=[Depends(get_current_user)])
def catch_all(path: str):
    raise HTTPException(status_code=404, detail="Not Found")
