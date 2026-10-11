from fastapi import APIRouter
from datetime import datetime, timezone
import orjson

router = APIRouter()

@router.get("/b/v1/health")
async def health_check():
    return {
        "status": "ok",
        "service": "jp-b",
        "version": "1.0.0",
        "time": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    }
