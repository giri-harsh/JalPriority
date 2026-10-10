"""Local filesystem storage backend. OWNER: Agent DATA.

INTERFACE (frozen by LEAD; extend, don't break):

    class StorageBackend(Protocol):
        def read_json(self, relpath: str) -> dict | list | None      # None when missing
        def write_json(self, relpath: str, data: dict | list) -> None # deterministic: sort_keys? no - indent=2, "\\n" newlines, UTF-8
        def exists(self, relpath: str) -> bool
        def list_json(self, prefix: str) -> list[str]                 # relpaths under prefix, sorted

    make_storage(settings) -> StorageBackend   # local -> LocalStorage(settings.a_data_dir); s3 -> storage_s3 (M12) with local fallback
"""
from __future__ import annotations

from typing import Any, Protocol


class StorageBackend(Protocol):
    def read_json(self, relpath: str) -> Any | None: ...
    def write_json(self, relpath: str, data: Any) -> None: ...
    def exists(self, relpath: str) -> bool: ...
    def list_json(self, prefix: str) -> list[str]: ...


class LocalStorage:
    """STUB - Agent DATA implements."""

    def __init__(self, root):
        self.root = root

    def read_json(self, relpath: str):  # pragma: no cover
        raise NotImplementedError

    def write_json(self, relpath: str, data) -> None:  # pragma: no cover
        raise NotImplementedError

    def exists(self, relpath: str) -> bool:  # pragma: no cover
        raise NotImplementedError

    def list_json(self, prefix: str) -> list[str]:  # pragma: no cover
        raise NotImplementedError


def make_storage(settings) -> StorageBackend:
    """STUB factory - Agent DATA completes (S3 branch is M12; must fall back to local if S3 is unusable)."""
    return LocalStorage(settings.a_data_dir)
