"""One JSON object per line on stdout (CONTRACT section 4.6). Never log tokens or passwords."""
from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

from . import SERVICE_NAME

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_RESERVED = set(logging.LogRecord("x", 0, "x", 0, "", (), None).__dict__) | {"message", "asctime"}
LOGGER_NAME = "jp_a"


def _iso_ms(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": _iso_ms(record.created),
            "level": record.levelname,
            "service": SERVICE_NAME,
            "request_id": getattr(record, "request_id", None) or request_id_var.get(),
            "event": getattr(record, "event", record.name.rsplit(".", 1)[-1]),
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key not in payload and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    # Rebind to the *current* sys.stdout on every call (idempotent; keeps pytest capture / uvicorn reload happy).
    for h in [h for h in logger.handlers if getattr(h, "_jp_json", False)]:
        logger.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler._jp_json = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(LOGGER_NAME if not name else f"{LOGGER_NAME}.{name}")


def log_event(event: str, msg: str, level: int = logging.INFO, **fields) -> None:
    """log_event("plan_solved", "solved scn_sa01_w06", duration_ms=412)"""
    get_logger(event).log(level, msg, extra={"event": event, **fields})
