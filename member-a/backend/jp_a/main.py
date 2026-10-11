"""FastAPI app factory. Run: ``uvicorn jp_a.main:app --port 8001`` (all routes live under /a/v1)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse

from . import __version__
from .api import routes_health, routes_model, routes_plans, routes_predictions, routes_scenarios
from .api.deps import Container, build_container
from .api.errors import install_error_handlers
from .api.middleware import RequestContextMiddleware
from .config import Settings, get_settings
from .logging_setup import log_event, setup_logging

API_PREFIX = "/a/v1"


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.shared_log_level)
    local = settings.shared_env == "local"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # A loads the model at startup so the first request is warm (INTEGRATION section 4).
        try:
            app.state.container.model_repo.load()
        except Exception as exc:  # noqa: BLE001 - startup must survive a missing artefact; routes return 503
            log_event("model_load_failed", f"model not loaded at startup: {exc!r}", level=logging.WARNING)
        log_event("startup", f"jp-a {__version__} ready", port=settings.a_port, auth_disabled=settings.a_auth_disabled)
        yield

    app = FastAPI(
        title="JalPriority AI - A (need -> plan)",
        version=__version__,
        default_response_class=ORJSONResponse,
        lifespan=lifespan,
        docs_url=f"{API_PREFIX}/docs" if local else None,
        openapi_url=f"{API_PREFIX}/openapi.json" if local else None,
        redoc_url=None,
    )
    app.state.container = container or build_container(settings)

    install_error_handlers(app)

    api = APIRouter(prefix=API_PREFIX)
    api.include_router(routes_health.router)
    api.include_router(routes_scenarios.router)
    api.include_router(routes_predictions.router)
    api.include_router(routes_plans.router)
    api.include_router(routes_model.router)
    app.include_router(api)

    # CORS first, request-context last => request-context is the outermost layer (header on every response).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestContextMiddleware)
    return app


app = create_app()
