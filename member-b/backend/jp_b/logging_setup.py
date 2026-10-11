import logging
import sys
import orjson
from datetime import datetime, timezone

from jp_b.config import settings

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_obj = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "service": "jp-b",
            "msg": record.getMessage(),
        }
        if hasattr(record, "request_id"):
            log_obj["request_id"] = record.request_id
        if hasattr(record, "event"):
            log_obj["event"] = record.event
        if hasattr(record, "duration_ms"):
            log_obj["duration_ms"] = record.duration_ms
        return orjson.dumps(log_obj).decode("utf-8")

def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, settings.shared_log_level.upper(), logging.INFO))
    
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    
    if logger.hasHandlers():
        logger.handlers.clear()
        
    logger.addHandler(handler)

    logging.getLogger("uvicorn.access").disabled = True
