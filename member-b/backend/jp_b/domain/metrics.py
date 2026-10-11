from typing import Dict, List, Optional, Any, Union
from jp_b.api.schemas import Metrics, PlanResult, Scenario
from jp_b.domain.constraints import check_constraints

def compute_unmet(shortfall_litres: int, allocated_litres: int) -> int:
    """F-6 Unmet: u_i = max(0, sf_i − allocated_i)"""
    return max(0, int(shortfall_litres) - int(allocated_litres))


def compute_coverage(shortfall_litres: int, allocated_litres: int) -> float:
    """F-7 Coverage: c_i = 1.0 if sf_i = 0, else round(min(1, allocated_i / sf_i), 4)"""
    if shortfall_litres <= 0:
        return 1.0
    ratio = min(1.0, float(allocated_litres) / float(shortfall_litres))
    return round(ratio, 4)


def update_debt(
    debt_litres: int,
    unmet_litres: int,
    decay: float = 0.3,
    cap_litres: Optional[int] = None,
) -> int:
    """
    F-10 Debt update (B):
    D_i' = round((1 − λ)·D_i + u_i), then min(D_i', cap) if cap is set.
    """
    decayed = (1.0 - float(decay)) * float(debt_litres)
    updated = round(decayed + float(unmet_litres))
    if cap_litres is not None and cap_litres >= 0:
        updated = min(updated, int(cap_litres))
    return updated


def compute_metrics(
    result: Union[PlanResult, dict],
    debt_by_area: Dict[str, int],
    decay: float = 0.3,
    cap_litres: Optional[int] = None,
    replanning_time_ms: Optional[int] = None,
    scenario: Optional[Union[Scenario, dict]] = None,
) -> Metrics:
    """
    Pure function implementing F-9 to F-15 per CONTRACT §5.11 & MEMBER_B.md B6.
    Returns the 7 PRD metrics in a strict Metrics schema.
    """
    res_dict = result if isinstance(result, dict) else result.model_dump()

    allocations = res_dict.get("allocations", [])
    routes = res_dict.get("routes", [])

    # F-9 Priority-weighted unmet: round(Σ_i p_i · u_i, 1) using rounded p_i
    pwu_sum = 0.0
    total_unmet = 0
    coverage_ratios: List[float] = []

    # F-10 & F-11 Debt update & remaining
    updated_debt_sum = 0

    for a in allocations:
        aid = a["area_id"]
        sf = int(a.get("predicted_shortfall_litres", 0))
        allocated = int(a.get("allocated_litres", 0))
        unmet = compute_unmet(sf, allocated)
        cov = compute_coverage(sf, allocated)
        p_weight = float(a.get("priority_weight", 1.0))

        pwu_sum += p_weight * float(unmet)
        total_unmet += unmet

        if sf > 0:
            coverage_ratios.append(cov)

        prior_debt = int(debt_by_area.get(aid, 0))
        d_prime = update_debt(prior_debt, unmet, decay=decay, cap_litres=cap_litres)
        updated_debt_sum += d_prime

    # Check for any areas in debt_by_area that might not have been in allocations
    # (If an area is not in allocations, its unmet is 0 for this window, but its prior debt decays)
    allocated_area_ids = {a["area_id"] for a in allocations}
    for aid, prior_debt in debt_by_area.items():
        if aid not in allocated_area_ids:
            d_prime = update_debt(prior_debt, 0, decay=decay, cap_litres=cap_litres)
            updated_debt_sum += d_prime

    # F-13 Min coverage: min_i c_i over areas with sf_i > 0; 1.0 if there are none.
    min_cov = min(coverage_ratios) if coverage_ratios else 1.0

    # F-14 Travel: Σ_k route_km_k, rounded to 2 dp.
    total_travel = round(sum(float(r.get("route_km", 0.0)) for r in routes), 2)

    # F-15 Constraint violations (recomputed independently)
    constraint_res = check_constraints(result=res_dict, scenario=scenario)

    return Metrics(
        schema_version="1.0",
        priority_weighted_unmet=round(pwu_sum, 1),
        deprivation_debt_remaining_litres=updated_debt_sum,
        total_unmet_litres=total_unmet,
        min_coverage_ratio=min_cov,
        total_travel_km=total_travel,
        constraint_violations=constraint_res.violation_count,
        replanning_time_ms=replanning_time_ms,
    )
