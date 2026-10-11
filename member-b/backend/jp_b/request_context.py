"""Per-request correlation id (CONTRACT 4.7): set by the request middleware, forwarded to A."""
from contextvars import ContextVar
from typing import Optional

request_id_var: ContextVar[Optional[str]] = ContextVar("jp_b_request_id", default=None)
