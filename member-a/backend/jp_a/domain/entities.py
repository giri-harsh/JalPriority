"""Strict Pydantic models for every CONTRACT section 5 entity (+ section 6 request bodies).

All models are ``extra="forbid"``. Contract tests parse fixtures and live responses with these
models directly. Runtime *request* parsing uses the tolerant subclasses in ``api/schemas.py``.

Wire rules (CONTRACT section 4): snake_case, datetimes as ``YYYY-MM-DDTHH:MM:SS.mmmZ`` strings,
ids matching section 4.2 patterns, litres as ints, km as 2 dp floats, ratios as 4 dp floats.
"""
from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

SCHEMA_VERSION = "1.0"

# --------------------------------------------------------------------------- id / time helpers
ISO_DT_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"

DERIVED_SCENARIO_RE = re.compile(r"^scn_[a-z0-9_]+__d\d+_[0-9a-f]{6}$")
_SCENARIO_ROOT_RE = re.compile(r"^scn_[a-z0-9_]+$")

SERVICE_AREA_ID_RE = re.compile(r"^sa_\d{2}$")
AREA_ID_RE = re.compile(r"^ar_\d{2}$")
TANKER_ID_RE = re.compile(r"^tk_\d{2}$")
DEPOT_ID_RE = re.compile(r"^dp_\d{2}$")
RESULT_ID_RE = re.compile(r"^res_[0-9a-f]{12}$")
PLAN_ID_RE = re.compile(r"^pln_[0-9a-f]{12}$")
EVENT_ID_RE = re.compile(r"^evt_[0-9a-f]{12}$")
REQUEST_ID_RE = re.compile(r"^req_[0-9a-f]{12}$")


def is_valid_scenario_id(value: str) -> bool:
    """Seed ids: ``^scn_[a-z0-9_]+$`` without ``__``. Derived: ``<seed>__d<k>_<6 hex>`` with EXACTLY ONE ``__``
    (CI-5: the contract regex alone also accepts ``scn_a__b__d1_abcdef``) and a root that does not end in ``_``."""
    if DERIVED_SCENARIO_RE.match(value):
        root = value[: value.rindex("__d")]
        return value.count("__") == 1 and not root.endswith("_") and bool(_SCENARIO_ROOT_RE.match(root))
    return bool(_SCENARIO_ROOT_RE.match(value)) and "__" not in value


def _check_scenario_id(value: str) -> str:
    if not is_valid_scenario_id(value):
        raise ValueError(f"invalid scenario id: {value!r}")
    return value


def now_iso() -> str:
    """UTC, millisecond precision, Z suffix (CONTRACT 4.3)."""
    return to_iso(datetime.now(UTC))


def to_iso(dt: datetime) -> str:
    dt = dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def parse_iso(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


def new_result_id() -> str:
    return "res_" + secrets.token_hex(6)


def new_request_id() -> str:
    return "req_" + secrets.token_hex(6)


IsoDatetime = Annotated[str, StringConstraints(pattern=ISO_DT_PATTERN)]
ServiceAreaId = Annotated[str, StringConstraints(pattern=r"^sa_\d{2}$")]
AreaId = Annotated[str, StringConstraints(pattern=r"^ar_\d{2}$")]
TankerId = Annotated[str, StringConstraints(pattern=r"^tk_\d{2}$")]
DepotId = Annotated[str, StringConstraints(pattern=r"^dp_\d{2}$")]
ResultId = Annotated[str, StringConstraints(pattern=r"^res_[0-9a-f]{12}$")]
PlanId = Annotated[str, StringConstraints(pattern=r"^pln_[0-9a-f]{12}$")]
EventId = Annotated[str, StringConstraints(pattern=r"^evt_[0-9a-f]{12}$")]
ScenarioId = Annotated[str, AfterValidator(_check_scenario_id)]
SchemaVersion = Literal["1.0"]

# --------------------------------------------------------------------------- enums
TankerStatus = Literal["available", "unavailable"]
DisruptionType = Literal["tanker_down", "supply_cut", "heat_alert"]
Strategy = Literal["jalpriority", "fcfs", "nearest_first"]
PlanStatus = Literal["recommended", "approved", "edited", "rejected", "superseded"]
RouteStatus = Literal["assigned", "idle", "unavailable"]
SolverEngine = Literal["ortools-cp-sat", "greedy-fallback", "baseline-fcfs", "baseline-nearest", "mock"]
SolverStatus = Literal["optimal", "feasible", "time_limited", "heuristic"]
ReasonCode = Literal[
    "HIGH_PREDICTED_SHORTFALL", "HIGH_DEBT", "HIGH_VULNERABILITY", "HEAT_ALERT",
    "LOWER_RELATIVE_PRIORITY", "FULLY_SERVED", "PARTIALLY_SERVED", "NOT_SERVED",
    "NO_SHORTFALL", "SUPPLY_EXHAUSTED", "TANKER_CAPACITY_LIMIT", "ROUTE_LIMIT",
    "TRAVEL_COST_TRADEOFF", "TIME_LIMITED_SOLUTION", "FCFS_ORDER", "NEAREST_FIRST_ORDER",
]  # fmt: skip
UnservedReasonCode = Literal[
    "SUPPLY_EXHAUSTED", "TANKER_CAPACITY_LIMIT", "ROUTE_LIMIT", "TRAVEL_COST_TRADEOFF",
    "LOWER_RELATIVE_PRIORITY",
]  # fmt: skip
ViolationCode = Literal[
    "WATER_CAP_EXCEEDED", "TANKER_CAPACITY_EXCEEDED", "ROUTE_LIMIT_EXCEEDED", "UNAVAILABLE_TANKER_USED"
]
FeatureName = Literal[
    "max_temp_c", "heat_index_c", "alert_level", "heat_exposure", "population",
    "last_need_litres", "mean_need_last3_litres", "last_received_litres", "expected_piped_litres",
]  # fmt: skip
FEATURE_NAMES: tuple[str, ...] = (
    "max_temp_c", "heat_index_c", "alert_level", "heat_exposure", "population",
    "last_need_litres", "mean_need_last3_litres", "last_received_litres", "expected_piped_litres",
)  # fmt: skip
EventName = Literal[
    "scenario_loaded", "explanation_viewed", "prediction_generated", "plan_generated",
    "weights_changed", "disruption_applied", "replan_completed", "comparison_viewed", "plan_decision",
]  # fmt: skip
ParamValue = int | float | str | bool | None


class ContractModel(BaseModel):
    """Base: strict (extra=forbid). ``model_*`` field names (model_version) are legal here."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())


# --------------------------------------------------------------------------- 5.2 Weights
class Weights(ContractModel):
    urgency: float = Field(default=1.0, ge=0, le=5)
    debt: float = Field(default=1.0, ge=0, le=5)
    vulnerability: float = Field(default=0.5, ge=0, le=5)
    travel: float = Field(default=20.0, ge=0, le=500)


DEFAULT_WEIGHTS = Weights()


# --------------------------------------------------------------------------- 5.3 Scenario
class Heat(ContractModel):
    alert_level: int = Field(ge=0, le=3)
    max_temp_c: float
    heat_index_c: float


class Supply(ContractModel):
    tanker_water_available_litres: int = Field(ge=0)


class Depot(ContractModel):
    depot_id: DepotId
    name: str
    lat: float
    lon: float


class Tanker(ContractModel):
    tanker_id: TankerId
    capacity_litres: int = Field(gt=0)
    status: TankerStatus
    max_route_km: float = Field(gt=0)


class HistoryEntry(ContractModel):
    window_index: int = Field(ge=1)
    need_litres: int = Field(ge=0)
    received_litres: int = Field(ge=0)
    max_temp_c: float
    alert_level: int = Field(ge=0, le=3)


class Area(ContractModel):
    area_id: AreaId
    name: str
    lat: float
    lon: float
    population: int = Field(gt=0)
    vulnerability_index: float = Field(ge=0, le=1)
    vulnerability_available: bool
    heat_exposure: float = Field(ge=0, le=1)
    expected_piped_litres: int = Field(ge=0)
    requested_litres: int = Field(ge=0)
    request_time: IsoDatetime
    last_delivery_litres: int = Field(ge=0)
    last_delivery_at: IsoDatetime | None
    history: list[HistoryEntry]


class AppliedDisruption(ContractModel):
    type: DisruptionType
    params: dict[str, int | float | str]
    applied_at: IsoDatetime


class Scenario(ContractModel):
    schema_version: SchemaVersion = "1.0"
    scenario_id: ScenarioId
    parent_scenario_id: ScenarioId | None
    service_area_id: ServiceAreaId
    service_area_name: str
    window_index: int = Field(ge=1)
    window_start: IsoDatetime
    window_hours: int
    is_synthetic: Literal[True] = True
    seed: int
    heat: Heat
    supply: Supply
    depot: Depot
    tankers: list[Tanker]
    areas: list[Area]
    distance_km: dict[str, dict[str, float]]
    disruptions_applied: list[AppliedDisruption]


class ScenarioSummary(ContractModel):
    schema_version: SchemaVersion = "1.0"
    scenario_id: ScenarioId
    parent_scenario_id: ScenarioId | None
    service_area_id: ServiceAreaId
    window_index: int = Field(ge=1)
    label: str
    n_areas: int
    supply_litres: int
    tankers_available: int
    alert_level: int = Field(ge=0, le=3)
    is_synthetic: Literal[True] = True


# --------------------------------------------------------------------------- 5.4 PredictionSet
class FeatureContribution(ContractModel):
    feature: FeatureName
    contribution_litres: float


class AreaPrediction(ContractModel):
    area_id: AreaId
    predicted_need_litres: int = Field(ge=0)
    expected_piped_litres: int = Field(ge=0)
    predicted_shortfall_litres: int = Field(ge=0)
    baseline_last_need_litres: int = Field(ge=0)
    top_features: list[FeatureContribution] = Field(min_length=3, max_length=3)


class PredictionSet(ContractModel):
    schema_version: SchemaVersion = "1.0"
    scenario_id: ScenarioId
    model_version: str
    generated_at: IsoDatetime
    latency_ms: int = Field(ge=0)
    run_mae_litres: float | None
    is_simulation: Literal[True] = True
    areas: list[AreaPrediction]


# --------------------------------------------------------------------------- 5.5 ModelInfo
class ErrorMetrics(ContractModel):
    mae_litres: float
    rmse_litres: float


class ModelMetricsBlock(ContractModel):
    xgboost: ErrorMetrics
    last_observed_baseline: ErrorMetrics


class FeatureImportance(ContractModel):
    feature: FeatureName
    importance: float


class ModelInfo(ContractModel):
    schema_version: SchemaVersion = "1.0"
    model_version: str
    trained_at: IsoDatetime
    train_rows: int
    test_rows: int
    target: Literal["need_litres"] = "need_litres"
    metrics: ModelMetricsBlock
    feature_importances: list[FeatureImportance]
    is_simulation: Literal[True] = True
    disclaimer: str


# --------------------------------------------------------------------------- 6.6 SolveRequest
class SolveRequest(ContractModel):
    scenario_id: ScenarioId
    weights: Weights
    debt_by_area: dict[AreaId, Annotated[int, Field(ge=0)]]
    time_limit_s: float | None = Field(default=None, ge=0.5, le=9.5)


# --------------------------------------------------------------------------- 5.6 PlanResult
class SolverInfo(ContractModel):
    engine: SolverEngine
    status: SolverStatus
    solve_time_ms: int = Field(ge=0)
    time_limit_s: float | None


class PredictionRef(ContractModel):
    model_version: str
    latency_ms: int = Field(ge=0)
    run_mae_litres: float | None


class Totals(ContractModel):
    supply_available_litres: int
    tanker_capacity_available_litres: int
    shortfall_litres: int
    allocated_litres: int
    unmet_litres: int
    travel_km: float


class Delivery(ContractModel):
    tanker_id: TankerId
    stop_order: int = Field(ge=1)
    litres: int = Field(ge=0)


class Reason(ContractModel):
    code: ReasonCode
    params: dict[str, ParamValue]


class Allocation(ContractModel):
    area_id: AreaId
    rank: int = Field(ge=1)
    predicted_shortfall_litres: int
    allocated_litres: int
    unmet_litres: int
    coverage_ratio: float
    priority_weight: float
    urgency: float
    debt_litres_used: int
    debt_normalized: float
    vulnerability_used: float
    deliveries: list[Delivery]
    reasons: list[Reason] = Field(min_length=1)
    reason_text: str = Field(min_length=1)
    is_unserved_remainder: bool


class RouteStop(ContractModel):
    stop_order: int = Field(ge=1)
    area_id: AreaId
    litres: int = Field(ge=0)


class Route(ContractModel):
    tanker_id: TankerId
    status: RouteStatus
    capacity_litres: int
    max_route_km: float
    load_litres: int
    route_km: float
    stops: list[RouteStop]
    path: list[str]


class Unserved(ContractModel):
    area_id: AreaId
    unmet_litres: int = Field(gt=0)
    reason_code: UnservedReasonCode


class Violation(ContractModel):
    code: ViolationCode
    detail: str


class ConstraintCheck(ContractModel):
    water_cap_ok: bool
    capacity_ok: bool
    route_limit_ok: bool
    unavailable_tanker_ok: bool
    violations: list[Violation]


class PlanResult(ContractModel):
    schema_version: SchemaVersion = "1.0"
    result_id: ResultId
    strategy: Strategy
    scenario_id: ScenarioId
    service_area_id: ServiceAreaId
    window_index: int = Field(ge=1)
    generated_at: IsoDatetime
    weights: Weights
    solver: SolverInfo
    prediction: PredictionRef
    totals: Totals
    allocations: list[Allocation]
    routes: list[Route]
    unserved: list[Unserved]
    constraint_check: ConstraintCheck


# --------------------------------------------------------------------------- 5.7 / 5.8 / 5.9 (B-owned, fixtures + harness)
class Metrics(ContractModel):
    schema_version: SchemaVersion = "1.0"
    priority_weighted_unmet: float
    deprivation_debt_remaining_litres: int
    total_unmet_litres: int
    min_coverage_ratio: float
    total_travel_km: float
    constraint_violations: int
    replanning_time_ms: int | None


class Decision(ContractModel):
    decision: Literal["approved", "edited", "rejected"]
    officer_id: str
    note: str | None
    decided_at: IsoDatetime
    next_plan_id: PlanId | None


class PlanDisruption(ContractModel):
    type: DisruptionType
    params: dict[str, int | float | str]


class PlanRecord(ContractModel):
    schema_version: SchemaVersion = "1.0"
    plan_id: PlanId
    status: PlanStatus
    created_at: IsoDatetime
    created_by: str
    scenario_id: ScenarioId
    service_area_id: ServiceAreaId
    window_index: int = Field(ge=1)
    supersedes_plan_id: PlanId | None
    replan_of_plan_id: PlanId | None
    disruption: PlanDisruption | None
    is_synthetic: Literal[True] = True
    result: PlanResult
    metrics: Metrics
    decision: Decision | None


class Event(ContractModel):
    schema_version: SchemaVersion = "1.0"
    event_id: EventId
    name: EventName
    ts: IsoDatetime
    officer_id: str
    scenario_id: ScenarioId | None
    props: dict[str, Any]


# --------------------------------------------------------------------------- 5.12 disruption request / misc API bodies
class DisruptionRequest(ContractModel):
    type: DisruptionType
    params: dict[str, Any]


class HealthResponse(ContractModel):
    status: Literal["ok"] = "ok"
    service: Literal["jp-a"] = "jp-a"
    version: str
    model_version: str
    time: IsoDatetime


class ScenarioList(ContractModel):
    scenarios: list[ScenarioSummary]


class ErrorBody(ContractModel):
    code: str
    message: str
    details: dict[str, Any]
    request_id: str


class ErrorEnvelope(ContractModel):
    error: ErrorBody
