"""Runtime (tolerant) request models. Strict (extra=forbid) twins live in ``domain.entities`` and are
what the contract tests parse. Runtime parsing ignores unknown keys (CONTRACT 4.9)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from ..domain.entities import (  # noqa: F401 - re-exported for routes
    DisruptionRequest,
    HealthResponse,
    ScenarioList,
    SolveRequest,
)


class SolveRequestIn(SolveRequest):
    model_config = ConfigDict(extra="ignore", protected_namespaces=())


class DisruptionRequestIn(BaseModel):
    """``type`` is a plain string here so unknown types yield DISRUPTION_INVALID (422), not a generic validation error."""

    model_config = ConfigDict(extra="ignore")

    type: str
    params: dict[str, Any] = {}
