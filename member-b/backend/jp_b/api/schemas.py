from pydantic import BaseModel, ConfigDict, Field, PlainSerializer
from typing import Annotated, List, Optional, Dict, Any, Literal, Union
from datetime import datetime, timezone


def _iso_ms_z(value: datetime) -> str:
    """CONTRACT 4.3: UTC, millisecond precision, Z suffix (pydantic would drop '.000')."""
    dt = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


UtcDatetime = Annotated[datetime, PlainSerializer(_iso_ms_z, return_type=str, when_used="json")]

class Weights(BaseModel):
    urgency: float = Field(default=1.0, ge=0.0, le=5.0)
    debt: float = Field(default=1.0, ge=0.0, le=5.0)
    vulnerability: float = Field(default=0.5, ge=0.0, le=5.0)
    travel: float = Field(default=20.0, ge=0.0, le=500.0)

class HeatInfo(BaseModel):
    alert_level: int = Field(ge=0, le=3)
    max_temp_c: float
    heat_index_c: float

class SupplyInfo(BaseModel):
    tanker_water_available_litres: int

class DepotInfo(BaseModel):
    depot_id: str
    name: str
    lat: float
    lon: float

class TankerInfo(BaseModel):
    tanker_id: str
    capacity_litres: int
    status: Literal["available", "unavailable"]
    max_route_km: float

class AreaHistory(BaseModel):
    window_index: int
    need_litres: int
    received_litres: int
    max_temp_c: float
    alert_level: int

class AreaInfo(BaseModel):
    area_id: str
    name: str
    lat: float
    lon: float
    population: int
    vulnerability_index: float
    vulnerability_available: bool
    heat_exposure: float
    expected_piped_litres: int
    requested_litres: int
    request_time: UtcDatetime
    last_delivery_litres: int
    last_delivery_at: Optional[UtcDatetime]
    history: List[AreaHistory]

class DisruptionApplied(BaseModel):
    type: str
    params: Dict[str, Any]
    applied_at: UtcDatetime

class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    
    schema_version: str = "1.0"
    scenario_id: str
    parent_scenario_id: Optional[str]
    service_area_id: str
    service_area_name: str
    window_index: int
    window_start: UtcDatetime
    window_hours: int = 12
    is_synthetic: bool = True
    seed: int
    heat: HeatInfo
    supply: SupplyInfo
    depot: DepotInfo
    tankers: List[TankerInfo]
    areas: List[AreaInfo]
    distance_km: Dict[str, Dict[str, float]]
    disruptions_applied: List[DisruptionApplied]

class ScenarioSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    
    schema_version: str = "1.0"
    scenario_id: str
    parent_scenario_id: Optional[str]
    service_area_id: str
    window_index: int
    label: str
    n_areas: int
    supply_litres: int
    tankers_available: int
    alert_level: int
    is_synthetic: bool = True

class FeatureContribution(BaseModel):
    feature: str
    contribution_litres: float

class PredictionArea(BaseModel):
    area_id: str
    predicted_need_litres: int
    expected_piped_litres: int
    predicted_shortfall_litres: int
    baseline_last_need_litres: int
    top_features: List[FeatureContribution]

class PredictionSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    
    schema_version: str = "1.0"
    scenario_id: str
    model_version: str
    generated_at: UtcDatetime
    latency_ms: int
    run_mae_litres: Optional[float]
    is_simulation: bool = True
    areas: List[PredictionArea]

class ModelMetrics(BaseModel):
    mae_litres: float
    rmse_litres: float

class FeatureImportance(BaseModel):
    feature: str
    importance: float

class ModelInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    
    schema_version: str = "1.0"
    model_version: str
    trained_at: UtcDatetime
    train_rows: int
    test_rows: int
    target: str
    metrics: Dict[str, ModelMetrics]
    feature_importances: List[FeatureImportance]
    is_simulation: bool = True
    disclaimer: str

class SolverInfo(BaseModel):
    engine: Literal["ortools-cp-sat", "greedy-fallback", "baseline-fcfs", "baseline-nearest", "mock"]
    status: Literal["optimal", "feasible", "time_limited", "heuristic"]
    solve_time_ms: int
    time_limit_s: Optional[float]

class PredictionInfo(BaseModel):
    model_version: str
    latency_ms: int
    run_mae_litres: Optional[float]

class Totals(BaseModel):
    supply_available_litres: int
    tanker_capacity_available_litres: int
    shortfall_litres: int
    allocated_litres: int
    unmet_litres: int
    travel_km: float

class Delivery(BaseModel):
    tanker_id: str
    stop_order: int
    litres: int

class Reason(BaseModel):
    code: Literal[
        "HIGH_PREDICTED_SHORTFALL", "HIGH_DEBT", "HIGH_VULNERABILITY", "HEAT_ALERT",
        "LOWER_RELATIVE_PRIORITY", "FULLY_SERVED", "PARTIALLY_SERVED", "NOT_SERVED",
        "NO_SHORTFALL", "SUPPLY_EXHAUSTED", "TANKER_CAPACITY_LIMIT", "ROUTE_LIMIT",
        "TRAVEL_COST_TRADEOFF", "TIME_LIMITED_SOLUTION", "FCFS_ORDER", "NEAREST_FIRST_ORDER"
    ]
    params: Dict[str, Any]

class Allocation(BaseModel):
    area_id: str
    rank: int
    predicted_shortfall_litres: int
    allocated_litres: int
    unmet_litres: int
    coverage_ratio: float
    priority_weight: float
    urgency: float
    debt_litres_used: int
    debt_normalized: float
    vulnerability_used: float
    deliveries: List[Delivery]
    reasons: List[Reason]
    reason_text: str
    is_unserved_remainder: bool

class RouteStop(BaseModel):
    stop_order: int
    area_id: str
    litres: int

class Route(BaseModel):
    tanker_id: str
    status: Literal["assigned", "idle", "unavailable"]
    capacity_litres: int
    max_route_km: float
    load_litres: int
    route_km: float
    stops: List[RouteStop]
    path: List[str]

class Unserved(BaseModel):
    area_id: str
    unmet_litres: int
    reason_code: Literal["SUPPLY_EXHAUSTED", "TANKER_CAPACITY_LIMIT", "ROUTE_LIMIT", "TRAVEL_COST_TRADEOFF", "LOWER_RELATIVE_PRIORITY"]

class Violation(BaseModel):
    code: Literal["WATER_CAP_EXCEEDED", "TANKER_CAPACITY_EXCEEDED", "ROUTE_LIMIT_EXCEEDED", "UNAVAILABLE_TANKER_USED"]
    detail: str

class ConstraintCheck(BaseModel):
    water_cap_ok: bool
    capacity_ok: bool
    route_limit_ok: bool
    unavailable_tanker_ok: bool
    violations: List[Violation]

class PlanResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    
    schema_version: str = "1.0"
    result_id: str
    strategy: Literal["jalpriority", "fcfs", "nearest_first"]
    scenario_id: str
    service_area_id: str
    window_index: int
    generated_at: UtcDatetime
    weights: Weights
    solver: SolverInfo
    prediction: PredictionInfo
    totals: Totals
    allocations: List[Allocation]
    routes: List[Route]
    unserved: List[Unserved]
    constraint_check: ConstraintCheck

class Metrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    
    schema_version: str = "1.0"
    priority_weighted_unmet: float
    deprivation_debt_remaining_litres: int
    total_unmet_litres: int
    min_coverage_ratio: float
    total_travel_km: float
    constraint_violations: int
    replanning_time_ms: Optional[int]

class DecisionInfo(BaseModel):
    decision: Literal["approved", "edited", "rejected"]
    officer_id: str
    note: Optional[str]
    decided_at: UtcDatetime
    next_plan_id: Optional[str]

class Disruption(BaseModel):
    type: Literal["tanker_down", "supply_cut", "heat_alert"]
    params: Dict[str, Any]

class PlanRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    
    schema_version: str = "1.0"
    plan_id: str
    status: Literal["recommended", "approved", "edited", "rejected", "superseded"]
    created_at: UtcDatetime
    created_by: str
    scenario_id: str
    service_area_id: str
    window_index: int
    supersedes_plan_id: Optional[str] = None
    replan_of_plan_id: Optional[str] = None
    disruption: Optional[Disruption] = None
    is_synthetic: bool = True
    result: PlanResult
    metrics: Metrics
    decision: Optional[DecisionInfo] = None

class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")
    
    schema_version: str = "1.0"
    event_id: str
    name: str
    ts: UtcDatetime
    officer_id: str
    scenario_id: Optional[str]
    props: Dict[str, Any]

class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    token: str
    officer_id: str
    role: str
    expires_at: int

class SolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    weights: Weights
    debt_by_area: Dict[str, int]
    time_limit_s: Optional[float] = Field(default=8.0, ge=0.5, le=9.5)

class ScenarioSummaryList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenarios: List[ScenarioSummary]

class LedgerSeriesPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window_index: int
    debt_litres: int
    unmet_litres: int
    source: Literal["history", "approved_plan"]
    plan_id: Optional[str] = None

class LedgerArea(BaseModel):
    model_config = ConfigDict(extra="forbid")

    area_id: str
    name: str
    current_debt_litres: int
    series: List[LedgerSeriesPoint]

class LedgerTotalPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window_index: int
    debt_litres: int

class LedgerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_area_id: str
    decay: float
    cap_litres: Optional[int] = None
    windows: List[int]
    areas: List[LedgerArea]
    totals: List[LedgerTotalPoint]


class CreatePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    weights: Optional[Weights] = None
    time_limit_s: Optional[float] = Field(default=8.0, ge=0.5, le=9.5)


class PlanListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plans: List[PlanRecord]


class AuditEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audit_id: str
    ts: UtcDatetime
    officer_id: str
    action: str
    plan_id: Optional[str] = None
    scenario_id: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class AuditListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: List[AuditEntry]


class EventTrackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    scenario_id: Optional[str] = None
    props: Dict[str, Any] = Field(default_factory=dict)


class EventTrackResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str


class EventsListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: List[Event]


class WeightsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_area_id: str
    weights: Weights
    updated_at: UtcDatetime
    updated_by: str
    flags: Dict[str, bool]
    defaults: Weights
    debt_policy: Dict[str, Any]


class UpdateWeightsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_area_id: str
    weights: Weights


class PlanDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "edited", "rejected"]
    note: Optional[str] = None
    revised_weights: Optional[Weights] = None


class PlanDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: PlanRecord
    next_plan: Optional[PlanRecord] = None
    ledger_updated: bool


class ComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    weights: Optional[Weights] = None


class ComparisonResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    scenario_id: str
    weights: Weights
    generated_at: UtcDatetime
    is_simulation: bool = True
    plan_id: str
    results: Dict[str, PlanResult]
    metrics: Dict[str, Metrics]
    best_by_metric: Dict[str, str]


class ReplanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str
    disruption: Disruption


class AreaDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    area_id: str
    litres_before: int
    litres_after: int
    delta: int
    tankers_before: List[str]
    tankers_after: List[str]
    order_before: List[int]
    order_after: List[int]
    change: Literal["increased", "decreased", "unchanged", "newly_served", "dropped", "reassigned"]


class DiffSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    areas_changed: int
    litres_moved: int
    metrics_before: Metrics
    metrics_after: Metrics


class ReplanDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    areas: List[AreaDiff]
    summary: DiffSummary


class ReplanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    previous_plan_id: str
    derived_scenario_id: str
    disruption: Disruption
    replanning_time_ms: int
    new_plan: PlanRecord
    diff: ReplanDiff




