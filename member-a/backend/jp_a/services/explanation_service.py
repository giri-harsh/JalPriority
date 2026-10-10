"""Plain-language explanations (MEMBER_A F6): reason codes from ``domain.reasons`` + fixed templates.

There is no free-form generation; every sentence below is a template filled with formatted numbers
(``12,000 L``, ``36%``, ``2.69``). A ``reason_text`` is built from up to three sentences:

  1. served status   "Served in full (6,000 L)." / "Partially served: 5,000 of 14,000 L (36%)." /
                     "Not served: 0 of 9,000 L." / "No tanker water needed: ..."
  2. why             served areas: the rank-1 variant "Highest priority (2.69): <drivers>." or
                     "Priority 2.21, driven by <drivers>." (or the rank when no driver applies);
                     areas with unmet litres: "<unmet> L remain unserved because <cause>; this area has
                     <drivers or profile> (priority 1.41)."
  3. time limit      only for ``time_limited`` / ``heuristic`` plans.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..domain.constraints import DELIVERY_UNIT_LITRES
from ..domain.entities import Reason
from ..domain.reasons import (
    DRIVER_CODES,
    AreaFacts,
    PlanFacts,
    build_reasons,
    is_unit_remainder,
    unmet_cause,
)

LOW_VULNERABILITY_MAX = 0.4


@dataclass(frozen=True)
class Explanation:
    reasons: list[Reason]
    reason_text: str
    unserved_reason_code: str | None  # the unmet cause, set whenever unmet_litres > 0


def explain_plan(areas: Sequence[AreaFacts], plan: PlanFacts) -> dict[str, Explanation]:
    """Reasons, text and unserved cause for every area of a plan, keyed by area id."""
    result = {}
    for area in areas:
        higher_served = any(o.rank < area.rank and o.allocated_litres > 0 for o in areas)
        result[area.area_id] = explain_area(area, plan, higher_priority_served=higher_served)
    return result


def explain_area(area: AreaFacts, plan: PlanFacts, *, higher_priority_served: bool) -> Explanation:
    reasons = build_reasons(area, plan)
    codes = [r.code for r in reasons]
    drivers = [c for c in codes if c in DRIVER_CODES]
    cause = unmet_cause(area, plan)

    sentences = [_status_sentence(area)]
    if cause is not None:
        sentences.append(_unmet_sentence(area, plan, cause, drivers, higher_priority_served))
    elif area.shortfall_litres > 0:
        sentences.append(_priority_sentence(area, plan, drivers))
    if plan.time_limited:
        sentences.append(_time_limit_sentence(plan))
    return Explanation(reasons=reasons, reason_text=" ".join(sentences), unserved_reason_code=cause)


# --------------------------------------------------------------------------- formatting
def _litres(value: int) -> str:
    return f"{value:,}"


def _priority(value: float) -> str:
    return f"{value:.2f}"


def _km(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _percent(part: float, whole: float) -> int:
    return int(100.0 * part / whole + 0.5) if whole else 0


def _join(items: Sequence[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


# --------------------------------------------------------------------------- sentences
def _status_sentence(area: AreaFacts) -> str:
    if area.shortfall_litres == 0:
        return (
            f"No tanker water needed: expected piped supply ({_litres(area.expected_piped_litres)} L) "
            f"covers the predicted need ({_litres(area.predicted_need_litres)} L)."
        )
    if area.allocated_litres == 0:
        return f"Not served: 0 of {_litres(area.shortfall_litres)} L."
    if area.unmet_litres == 0:
        return f"Served in full ({_litres(area.allocated_litres)} L)."
    # A partial delivery never reads as 0% or 100%.
    pct = min(99, max(1, _percent(area.allocated_litres, area.shortfall_litres)))
    return f"Partially served: {_litres(area.allocated_litres)} of {_litres(area.shortfall_litres)} L ({pct}%)."


def _priority_sentence(area: AreaFacts, plan: PlanFacts, drivers: Sequence[str]) -> str:
    p = _priority(area.priority_weight)
    if area.rank == 1:
        if drivers:
            return f"Highest priority ({p}): {_join([_top_phrase(c, area, plan) for c in drivers])}."
        return f"Highest priority ({p}) in this window."
    if drivers:
        return f"Priority {p}, driven by {_join([_driver_phrase(c, area, plan) for c in drivers])}."
    return f"Priority {p} (rank {area.rank} of {plan.n_areas})."


def _unmet_sentence(
    area: AreaFacts, plan: PlanFacts, cause: str, drivers: Sequence[str], higher_priority_served: bool
) -> str:
    if drivers:
        profile = [_driver_phrase(c, area, plan) for c in drivers]
    else:
        profile = [_debt_profile(area), _vulnerability_profile(area)]
    return (
        f"{_litres(area.unmet_litres)} L remain unserved because "
        f"{_cause_clause(cause, area, plan, higher_priority_served)}; "
        f"this area has {_join(profile)} (priority {_priority(area.priority_weight)})."
    )


def _cause_clause(cause: str, area: AreaFacts, plan: PlanFacts, higher_priority_served: bool) -> str:
    if cause == "SUPPLY_EXHAUSTED":
        if plan.supply_units == 0:
            return "no tanker water is available this window"
        if higher_priority_served:
            return f"all {_litres(plan.supply_litres)} L of tanker water went to higher-priority areas first"
        return f"all {_litres(plan.supply_litres)} L of tanker water is already allocated"
    if cause == "TANKER_CAPACITY_LIMIT":
        if plan.capacity_units == 0:
            return "no tanker is available this window"
        capacity = _litres(plan.tanker_capacity_litres)
        if higher_priority_served:
            return f"all {capacity} L of available tanker capacity went to higher-priority areas first"
        return f"all {capacity} L of available tanker capacity is already loaded"
    if cause == "ROUTE_LIMIT":
        return (
            f"no available tanker can add this area ({_km(area.depot_distance_km)} km from the depot) "
            "within its route limit"
        )
    if cause == "TRAVEL_COST_TRADEOFF":
        return (
            f"an extra stop {_km(area.depot_distance_km)} km from the depot costs more travel "
            f"(travel weight {plan.travel_weight:g}) than the remaining water would gain"
        )
    if is_unit_remainder(area):
        return f"tanker deliveries are planned in whole {DELIVERY_UNIT_LITRES} L units"
    return "higher-priority areas were served first"


def _time_limit_sentence(plan: PlanFacts) -> str:
    limit = f"{plan.time_limit_s:.1f}" if plan.time_limit_s is not None else "configured"
    if plan.solver_status == "heuristic":
        return (
            f"Fallback plan: the optimizer returned no solution within its {limit} s time limit, "
            "so areas were filled greedily in priority order."
        )
    return f"Best plan found within the {limit} s solver time limit; it may not be optimal."


# --------------------------------------------------------------------------- phrases
def _top_phrase(code: str, area: AreaFacts, plan: PlanFacts) -> str:
    """Driver phrases for the rank-1 sentence: "<what> (<value>)"."""
    if code == "HIGH_PREDICTED_SHORTFALL":
        return f"high predicted shortfall ({_litres(area.shortfall_litres)} L, {_percent(area.urgency, 1)}% of need)"
    if code == "HIGH_DEBT":
        largest = "largest " if area.debt_litres >= plan.max_debt_litres else ""
        return f"{largest}accumulated deprivation debt ({_litres(area.debt_litres)} L)"
    if code == "HIGH_VULNERABILITY":
        return f"high vulnerability ({area.vulnerability:.2f})"
    return _heat_phrase(area, plan)


def _driver_phrase(code: str, area: AreaFacts, plan: PlanFacts) -> str:
    """Driver phrases for "driven by ..." and "this area has ..."."""
    if code == "HIGH_PREDICTED_SHORTFALL":
        return (
            f"a high predicted shortfall of {_litres(area.shortfall_litres)} L "
            f"({_percent(area.urgency, 1)}% of need)"
        )
    if code == "HIGH_DEBT":
        return f"accumulated deprivation debt of {_litres(area.debt_litres)} L from earlier windows"
    if code == "HIGH_VULNERABILITY":
        return f"high vulnerability ({area.vulnerability:.2f})"
    return _heat_phrase(area, plan)


def _heat_phrase(area: AreaFacts, plan: PlanFacts) -> str:
    return f"high heat exposure ({area.heat_exposure:.2f}) under heat alert level {plan.alert_level}"


def _debt_profile(area: AreaFacts) -> str:
    if area.debt_litres == 0:
        return "no prior debt"
    return f"modest prior debt ({_litres(area.debt_litres)} L)"


def _vulnerability_profile(area: AreaFacts) -> str:
    if not area.vulnerability_available:
        return "no vulnerability data"
    if area.vulnerability < LOW_VULNERABILITY_MAX:
        return "low vulnerability"
    return f"moderate vulnerability ({area.vulnerability:.2f})"
