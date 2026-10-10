"""CONTRACT section 5.12 - disruption semantics (pure functions)."""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from typing import Any

from ..api.errors import DisruptionInvalidError, DisruptionNotApplicableError
from .entities import AppliedDisruption, Scenario

SUPPORTED_TYPES = ("tanker_down", "supply_cut", "heat_alert")
FRACTION_MIN, FRACTION_MAX = 0.05, 0.9


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def root_scenario_id(scenario_id: str) -> str:
    """Seed id = the part before the first ``__``."""
    return scenario_id.split("__", 1)[0]


def validate_disruption(scenario: Scenario, dtype: str, params: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate ``(type, params)`` against ``scenario`` and return the normalised params.

    Raises DisruptionInvalidError (422) or DisruptionNotApplicableError (409).
    Extra param keys are dropped (tolerant at runtime).
    """
    params = dict(params or {})
    if dtype not in SUPPORTED_TYPES:
        raise DisruptionInvalidError(f"Unknown disruption type {dtype!r}.", details={"type": dtype, "supported": list(SUPPORTED_TYPES)})

    if dtype == "tanker_down":
        tid = params.get("tanker_id")
        if not isinstance(tid, str):
            raise DisruptionInvalidError("params.tanker_id (string) is required.", details={"params": params})
        tanker = next((t for t in scenario.tankers if t.tanker_id == tid), None)
        if tanker is None:
            raise DisruptionInvalidError(f"Unknown tanker {tid}.", details={"tanker_id": tid})
        if tanker.status == "unavailable":
            raise DisruptionNotApplicableError(f"Tanker {tid} is already unavailable.", details={"tanker_id": tid})
        return {"tanker_id": tid}

    if dtype == "supply_cut":
        f = params.get("fraction")
        if not _is_number(f) or not (FRACTION_MIN <= float(f) <= FRACTION_MAX) or math.isnan(float(f)):
            raise DisruptionInvalidError(
                f"params.fraction must be a number in [{FRACTION_MIN}, {FRACTION_MAX}].", details={"fraction": f if _is_number(f) else None}
            )
        return {"fraction": float(f)}

    # heat_alert
    lvl = params.get("alert_level")
    if isinstance(lvl, float) and lvl.is_integer():
        lvl = int(lvl)
    if not isinstance(lvl, int) or isinstance(lvl, bool) or not (0 <= lvl <= 3):
        raise DisruptionInvalidError("params.alert_level must be an integer in [0, 3].", details={"alert_level": lvl if _is_number(lvl) else None})
    if lvl == scenario.heat.alert_level:
        raise DisruptionNotApplicableError(f"Heat alert level is already {lvl}.", details={"alert_level": lvl})
    return {"alert_level": lvl}


def derived_scenario_id(root: str, applied: Iterable[Mapping[str, Any] | AppliedDisruption]) -> str:
    """``{root}__d{k}_{h}``; k = len(applied), h = sha1 over the canonical (type, params) list, first 6 hex."""
    items = []
    for a in applied:
        d = a.model_dump() if isinstance(a, AppliedDisruption) else dict(a)
        items.append({"type": d["type"], "params": d["params"]})
    digest = hashlib.sha1(json.dumps(items, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:6]
    return f"{root}__d{len(items)}_{digest}"


def apply_disruption(parent: Scenario, dtype: str, params: Mapping[str, Any], applied_at: str) -> Scenario:
    """Return the derived Scenario (``params`` must already be validated/normalised)."""
    child = parent.model_copy(deep=True)

    if dtype == "tanker_down":
        for t in child.tankers:
            if t.tanker_id == params["tanker_id"]:
                t.status = "unavailable"
    elif dtype == "supply_cut":
        s = child.supply.tanker_water_available_litres
        child.supply.tanker_water_available_litres = max(0, math.floor(s * (1 - params["fraction"]) / 100) * 100)
    elif dtype == "heat_alert":
        delta = params["alert_level"] - parent.heat.alert_level
        child.heat.alert_level = params["alert_level"]
        child.heat.max_temp_c = round(parent.heat.max_temp_c + 1.5 * delta, 1)
        child.heat.heat_index_c = round(parent.heat.heat_index_c + 2.5 * delta, 1)

    child.disruptions_applied = [*parent.disruptions_applied, AppliedDisruption(type=dtype, params=dict(params), applied_at=applied_at)]
    child.parent_scenario_id = parent.scenario_id
    child.scenario_id = derived_scenario_id(root_scenario_id(parent.scenario_id), child.disruptions_applied)
    return child
