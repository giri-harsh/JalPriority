"""AWS Lambda entry point (M12, stretch): the same FastAPI app behind API Gateway via Mangum.

Handler: ``jp_a.lambda_handler.handler`` (see ``infra/lambda.Dockerfile`` and ``infra/template.yaml``).

A Lambda container image is read-only apart from ``/tmp``. If the data directory is not writable (for example when
``A_STORAGE_MODE=s3`` fell back to local storage) it is copied to ``/tmp`` before the settings are read, so derived
scenarios can still be written. Importing this module builds the app; it needs no credentials and no network.
"""
from __future__ import annotations

import os
import shutil
from collections.abc import MutableMapping
from pathlib import Path

LAMBDA_DATA_COPY = "/tmp/a-data"  # noqa: S108 - Lambda's only writable location


def ensure_writable_data_dir(env: MutableMapping[str, str] | None = None, copy_to: str = LAMBDA_DATA_COPY) -> str | None:
    """Point ``A_DATA_DIR`` at a writable copy when the configured directory is read-only.

    Returns the new path when a copy was made, else ``None``. Never raises: on any problem the original setting stays.
    """
    environ = os.environ if env is None else env
    configured = environ.get("A_DATA_DIR")
    if not configured:
        return None
    source = Path(configured)
    try:
        if not source.is_dir() or os.access(source, os.W_OK):
            return None
        target = Path(copy_to)
        if not target.exists():
            shutil.copytree(source, target)
        environ["A_DATA_DIR"] = str(target)
        return str(target)
    except OSError:
        return None


ensure_writable_data_dir()

from mangum import Mangum  # noqa: E402 - must come after the data directory is prepared

from .main import app  # noqa: E402

# lifespan="auto": the app's startup hook loads the model once per cold start.
handler = Mangum(app, lifespan="auto")
