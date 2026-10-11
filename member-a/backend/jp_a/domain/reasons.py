"""Reason codes (CONTRACT 5.10) and the rules that choose them (MEMBER_A F6). Pure functions, no I/O.

Rules, evaluated on the 4 dp wire values so a client re-checking a response reaches the same verdict:
  HIGH_PREDICTED_SHORTFALL   U >= 0.3
  HIGH_DEBT                  D^ >= 0.5 and D > 0
  HIGH_VULNERABILITY         V >= 0.6
  HEAT_ALERT                 alert >= 2 and heat_exposure >= 0.7, only when none of the three above applies
                             (CONTRACT_ISSUES CI-1: the binding 5.6 example lists no HEAT_ALERT for areas that
                             already have another driver)
  LOWER_RELATIVE_PRIORITY    only for an area with unmet litres: p in the bottom third (lowest ceil(N/3)
                             priorities, ties included, never the top priority), or as the residual unmet cause.
                             A fully served area never carries it.
  exactly one of FULLY_SERVED / PARTIALLY_SERVED / NOT_SERVED / NO_SHORTFALL
  one unmet cause when unmet > 0 (see ``unmet_cause``)
  TIME_LIMITED_SOLUTION      on every area when the plan is ``time_limited`` or ``heuristic``

Order inside ``reasons[]`` (matches the 5.6 example): drivers in enum order, LOWER_RELATIVE_PRIORITY, the
served status, the unmet cause, TIME_LIMITED_SOLUTION. No code appears twice.
"""
from __future__ import annotations

from dataclasses import dataclass

from .entities import Reason
from .priority import in_bottom_third

REASON_PARAM_KEYS: dict[str, tuple[str, ...]] = {
    "HIGH_PREDICTED_SHORTFALL": ("predicted_shortfall_litres", "urgency"),
    "HIGH_DEBT": ("debt_litres",),
    "HIGH_VULNERABILITY": ("vulnerability_index",),
    "HEAT_ALERT": ("alert_level",),
    "LOWER_RELATIVE_PRIORITY": ("priority_weight",),
    "FULLY_SERVED": ("allocated_litres",),
    "PARTIALLY_SERVED": ("allocated_litres", "predicted_shortfall_litres"),
    "NOT_SERVED": ("predicted_shortfall_litres",),
    "NO_SHORTFALL": (),
    "SUPPLY_EXHAUSTED": ("supply_available_litres", "unmet_litres"),
    "TANKER_CAPACITY_LIMIT": ("tanker_capacity_available_litres", "unmet_litres"),
    "ROUTE_LIMIT": ("distance_from_depot_km",),
    "TRAVEL_COST_TRADEOFF": ("distance_from_depot_km", "travel_weight"),
    "TIME_LIMITED_SOLUTION": ("time_limit_s",),
    "FCFS_ORDER": ("request_time", "position"),
    "NEAREST_FIRST_ORDER": ("distance_from_depot_km", "position"),
}
DRIVER_CODES = ("HIGH_PREDICTED_SHORTFALL", "HIGH_DEBT", "HIGH_VULNERABILITY", "HEAT_ALERT")
SERVED_CODES = ("FULLY_SERVED", "PARTIALLY_SERVED", "NOT_SERVED", "NO_SHORTFALL")
UNSERVED_REASON_CODES = (
    "SUPPLY_EXHAUSTED",
    "TANKER_CAPACITY_LIMIT",
    "ROUTE_LIMIT",
    "TRAVEL_COST_TRADEOFF",
    "LOWER_RELATIVE_PRIORITY",
)
TIME_LIMITED_STATUSES = ("time_limited", "heuristic")

HIGH_SHORTFALL_MIN_URGENCY = 0.3
HIGH_DEBT_MIN_NORMALIZED = 0.5
HIGH_VULNERABILITY_MIN = 0.6
HEAT_ALERT_MIN_LEVEL = 2
HEAT_EXPOSURE_MIN = 0.7
KM_DP = 2


@dataclass(frozen=True)
class AreaFacts:
    """Everything the rules and templates need about one area of a finished plan."""

    area_id: str
    rank: int
    priority_weight: float
    urgency: float
    debt_litres: int
    debt_normalized: float
    vulnerability: float
    vulnerability_available: bool
    heat_exposure: float
    predicted_need_litres: int
    expected_piped_litres: int
    shortfall_litres: int
    allocated_litres: int
    depot_distance_km: float
    depot_metres: int  # the optimizer's (rounded-up) view of the depot distance

    @property
    def unmet_litres(self) -> int:
        return max(0, self.shortfall_litres - self.allocated_litres)


@dataclass(frozen=True)
class TankerFacts:
    """State of one AVAILABLE tanker after planning, in the optimizer's units (100 L, metres)."""

    tanker_id: str
    capacity_units: int
    spare_units: int
    route_limit_metres: int
    route_used_metres: int  # star-route usage: sum of 2 * d(depot, i) over visited areas
    visits: frozenset[str]

    def can_ever_reach(self, area: AreaFacts) -> bool:
        return self.capacity_units > 0 and 2 * area.depot_metres <= self.route_limit_metres

    def can_add(self, area: AreaFacts) -> bool:
        """More water could go to ``area`` on this trip without breaking capacity or the route bound."""
        if self.spare_units <= 0:
            return False
        return area.area_id in self.visits or self.route_used_metres + 2 * area.depot_metres <= self.route_limit_metres


@dataclass(frozen=True)
class PlanFacts:
    """Plan-wide facts shared by every area's explanation."""

    supply_litres: int
    supply_units: int
    tanker_capacity_litres: int
    capacity_units: int
    allocated_units: int
    tankers: tuple[TankerFacts, ...]
    travel_weight: float
    alert_level: int
    max_debt_litres: int
    top_priority: float
    bottom_third_cutoff: float | None
    n_areas: int
    solver_status: str
    time_limit_s: float | None
    route_dropped: frozenset[str] = frozenset()  # areas whose stop was cut to keep a sequenced trip in its limit

    @property
    def time_limited(self) -> bool:
        return self.solver_status in TIME_LIMITED_STATUSES


def driver_codes(area: AreaFacts, plan: PlanFacts) -> list[str]:
    """Priority drivers, in enum order."""
    codes = []
    if area.urgency >= HIGH_SHORTFALL_MIN_URGENCY:
        codes.append("HIGH_PREDICTED_SHORTFALL")
    if area.debt_normalized >= HIGH_DEBT_MIN_NORMALIZED and area.debt_litres > 0:
        codes.append("HIGH_DEBT")
    if area.vulnerability >= HIGH_VULNERABILITY_MIN:
        codes.append("HIGH_VULNERABILITY")
    if not codes and plan.alert_level >= HEAT_ALERT_MIN_LEVEL and area.heat_exposure >= HEAT_EXPOSURE_MIN:
        codes.append("HEAT_ALERT")
    return codes


def is_bottom_third(area: AreaFacts, plan: PlanFacts) -> bool:
    return in_bottom_third(area.priority_weight, plan.bottom_third_cutoff, plan.top_priority)


def served_code(area: AreaFacts) -> str:
    if area.shortfall_litres == 0:
        return "NO_SHORTFALL"
    if area.allocated_litres == 0:
        return "NOT_SERVED"
    if area.unmet_litres == 0:
        return "FULLY_SERVED"
    return "PARTIALLY_SERVED"


def unmet_cause(area: AreaFacts, plan: PlanFacts) -> str | None:
    """The single cause of an area's unmet litres (``None`` when nothing is unmet).

    Checked in order: no water at all, no tanker at all, an area no tanker can reach even alone (or whose stop was cut in route repair), the total water bound binding, all available capacity
    used, no tanker that could add the area within its route bound, an extra stop not worth its travel cost,
    and finally lower relative priority. The optimizer caps each area at its shortfall rounded UP to whole
    100 L units, so unmet litres are always a real shortage (never sub-100 L rounding residue).
    """
    if area.unmet_litres == 0:
        return None
    if plan.supply_units == 0:
        return "SUPPLY_EXHAUSTED"
    if plan.capacity_units == 0:
        return "TANKER_CAPACITY_LIMIT"
    if area.area_id in plan.route_dropped or not any(t.can_ever_reach(area) for t in plan.tankers):
        return "ROUTE_LIMIT"
    if plan.allocated_units >= plan.supply_units:
        return "SUPPLY_EXHAUSTED"
    if plan.allocated_units >= plan.capacity_units:
        return "TANKER_CAPACITY_LIMIT"
    if not any(t.can_add(area) for t in plan.tankers):
        return "ROUTE_LIMIT"
    visiting_with_room = any(area.area_id in t.visits and t.spare_units > 0 for t in plan.tankers)
    if plan.travel_weight > 0 and area.depot_distance_km > 0 and not visiting_with_room:
        return "TRAVEL_COST_TRADEOFF"
    return "LOWER_RELATIVE_PRIORITY"


def reason_codes(area: AreaFacts, plan: PlanFacts) -> list[str]:
    """Ordered reason codes for an area (see the module docstring)."""
    cause = unmet_cause(area, plan)
    codes = driver_codes(area, plan)
    if cause is not None and (is_bottom_third(area, plan) or cause == "LOWER_RELATIVE_PRIORITY"):
        codes.append("LOWER_RELATIVE_PRIORITY")
    codes.append(served_code(area))
    if cause is not None and cause not in codes:
        codes.append(cause)
    if plan.time_limited:
        codes.append("TIME_LIMITED_SOLUTION")
    return codes


def reason_params(code: str, area: AreaFacts, plan: PlanFacts) -> dict[str, int | float | None]:
    """The exact CONTRACT 5.10 params for ``code``."""
    distance_km = round(area.depot_distance_km, KM_DP)
    values: dict[str, int | float | None] = {
        "predicted_shortfall_litres": area.shortfall_litres,
        "urgency": area.urgency,
        "debt_litres": area.debt_litres,
        "vulnerability_index": area.vulnerability,
        "alert_level": plan.alert_level,
        "priority_weight": area.priority_weight,
        "allocated_litres": area.allocated_litres,
        "supply_available_litres": plan.supply_litres,
        "unmet_litres": area.unmet_litres,
        "tanker_capacity_available_litres": plan.tanker_capacity_litres,
        "distance_from_depot_km": distance_km,
        "travel_weight": plan.travel_weight,
        "time_limit_s": plan.time_limit_s,
    }
    return {key: values[key] for key in REASON_PARAM_KEYS[code]}


def build_reasons(area: AreaFacts, plan: PlanFacts) -> list[Reason]:
    return [Reason(code=code, params=reason_params(code, area, plan)) for code in reason_codes(area, plan)]
