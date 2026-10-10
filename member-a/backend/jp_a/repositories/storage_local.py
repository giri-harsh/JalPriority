"""Local filesystem storage backend. OWNER: Agent DATA.

INTERFACE (frozen by LEAD; extend, don't break):

    class StorageBackend(Protocol):
        def read_json(self, relpath: str) -> dict | list | None      # None when missing
        def write_json(self, relpath: str, data: dict | list) -> None # deterministic: indent=2, "\\n" newlines, UTF-8
        def exists(self, relpath: str) -> bool
        def list_json(self, prefix: str) -> list[str]                 # relpaths under prefix, sorted

    make_storage(settings) -> StorageBackend   # local -> LocalStorage(settings.a_data_dir); s3 -> storage_s3 (M12) with local fallback

Relpaths always use forward slashes and must stay inside the storage root (absolute paths and ``..``
segments are rejected), so ids taken from URLs can never escape the data directory.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from ..logging_setup import log_event

_REPLACE_ATTEMPTS = 5
_REPLACE_BACKOFF_S = 0.05


class StorageBackend(Protocol):
    def read_json(self, relpath: str) -> Any | None: ...
    def write_json(self, relpath: str, data: Any) -> None: ...
    def exists(self, relpath: str) -> bool: ...
    def list_json(self, prefix: str) -> list[str]: ...


def json_text(data: Any) -> str:
    """Canonical JSON text used for every file A writes: indent=2, UTF-8 (no ASCII escaping), trailing LF.

    Key order is the insertion order of ``data`` (Pydantic ``model_dump`` follows the CONTRACT field order).
    """
    return json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


class LocalStorage:
    """JSON files under ``root`` (normally ``A_DATA_DIR``)."""

    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root)

    def _path(self, relpath: str) -> Path:
        rel = PurePosixPath(str(relpath).replace("\\", "/"))
        if not rel.parts or rel.is_absolute() or any(p in ("..", "") for p in rel.parts) or ":" in str(rel):
            raise ValueError(f"invalid storage path: {relpath!r}")
        return self.root.joinpath(*rel.parts)

    def read_json(self, relpath: str) -> Any | None:
        path = self._path(relpath)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        return json.loads(text)

    def write_json(self, relpath: str, data: Any) -> None:
        """Atomic write: a temp file in the target directory, then ``os.replace``."""
        path = self._path(relpath)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(json_text(data))
            self._replace(tmp_name, path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    @staticmethod
    def _replace(src: str, dst: Path) -> None:
        # On Windows os.replace fails while another thread briefly holds the target open for reading.
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(src, dst)
                return
            except PermissionError:
                if attempt == _REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(_REPLACE_BACKOFF_S * (attempt + 1))

    def exists(self, relpath: str) -> bool:
        return self._path(relpath).is_file()

    def list_json(self, prefix: str) -> list[str]:
        """Sorted relpaths of the ``*.json`` files directly inside the ``prefix`` directory."""
        directory = self._path(prefix)
        if not directory.is_dir():
            return []
        base = str(PurePosixPath(*PurePosixPath(prefix.replace("\\", "/")).parts))
        return sorted(f"{base}/{p.name}" for p in directory.glob("*.json") if p.is_file())


def make_storage(settings) -> StorageBackend:
    """``local`` -> LocalStorage(A_DATA_DIR). ``s3`` is milestone M12: until then it falls back to local."""
    if settings.a_storage_mode == "s3":
        log_event(
            "storage_fallback",
            "A_STORAGE_MODE=s3 is not available yet; using local storage",
            level=logging.WARNING,
            data_dir=str(settings.a_data_dir),
        )
    return LocalStorage(settings.a_data_dir)
