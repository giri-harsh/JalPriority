"""Pure-ASGI request-context middleware: X-Request-ID reuse/generate/echo + request logging +
INTERNAL_ERROR envelope for unhandled exceptions (so the id header is present on 500s too)."""
from __future__ import annotations

import logging
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..domain.entities import new_request_id
from ..logging_setup import log_event, request_id_var
from .errors import error_payload

import orjson


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = None
        for name, value in scope.get("headers", []):
            if name == b"x-request-id":
                incoming = value.decode("latin-1").strip()[:64] or None
                break
        request_id = incoming or new_request_id()
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_holder = {"status": 500, "started": False}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                status_holder["started"] = True
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"]
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:  # noqa: BLE001 - last-resort envelope
            log_event("unhandled_exception", repr(exc), level=logging.ERROR)
            if not status_holder["started"]:
                body = orjson.dumps(error_payload("INTERNAL_ERROR", "Internal server error.", {}, request_id))
                await send_wrapper(
                    {
                        "type": "http.response.start",
                        "status": 500,
                        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
                    }
                )
                await send_wrapper({"type": "http.response.body", "body": body})
                status_holder["status"] = 500
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            path = scope.get("path", "")
            if path != "/a/v1/health":
                log_event(
                    "request",
                    f"{scope.get('method', '')} {path} {status_holder['status']}",
                    duration_ms=duration_ms,
                    status=status_holder["status"],
                )
            request_id_var.reset(token)
