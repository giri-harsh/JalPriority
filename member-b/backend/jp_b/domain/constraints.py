from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass
from jp_b.api.schemas import PlanResult, Scenario, ConstraintCheck, Violation

@dataclass
class IndependentConstraintCheckResult:
    water_cap_ok: bool
    capacity_ok: bool
    route_limit_ok: bool
    unavailable_tanker_ok: bool
    violations: List[Dict[str, str]]
    violation_count: int

    def to_schema(self) -> ConstraintCheck:
        return ConstraintCheck(
            water_cap_ok=self.water_cap_ok,
            capacity_ok=self.capacity_ok,
            route_limit_ok=self.route_limit_ok,
            unavailable_tanker_ok=self.unavailable_tanker_ok,
            violations=[Violation(code=v["code"], detail=v["detail"]) for v in self.violations],  # type: ignore
        )


def check_constraints(
    result: Union[PlanResult, dict],
    scenario: Optional[Union[Scenario, dict]] = None,
) -> IndependentConstraintCheckResult:
    """
    Independent constraint-violation checks per CONTRACT §5.11 (F-15):
    - +1 if Σ allocated > supply_available_litres (WATER_CAP_EXCEEDED)
    - +1 per tanker whose load exceeds its capacity (TANKER_CAPACITY_EXCEEDED)
    - +1 per route with route_km > max_route_km + 0.01 (ROUTE_LIMIT_EXCEEDED)
    - +1 per delivery made by an unavailable tanker (UNAVAILABLE_TANKER_USED)
    """
    res_dict = result if isinstance(result, dict) else result.model_dump()
    scen_dict = (scenario if isinstance(scenario, dict) else scenario.model_dump()) if scenario else None

    violations: List[Dict[str, str]] = []
    violation_count = 0

    # 1. Water cap check
    allocations = res_dict.get("allocations", [])
    total_allocated = sum(int(a.get("allocated_litres", 0)) for a in allocations)

    if scen_dict and "supply" in scen_dict:
        supply_available = int(scen_dict["supply"].get("tanker_water_available_litres", 0))
    elif "totals" in res_dict:
        supply_available = int(res_dict["totals"].get("supply_available_litres", 0))
    else:
        supply_available = total_allocated

    water_cap_ok = True
    if total_allocated > supply_available:
        water_cap_ok = False
        violation_count += 1
        violations.append({
            "code": "WATER_CAP_EXCEEDED",
            "detail": f"Total allocated litres ({total_allocated}) exceeds supply available ({supply_available})"
        })

    # 2. Tanker capacity & 3. Route limit check
    routes = res_dict.get("routes", [])
    capacity_ok = True
    route_limit_ok = True

    for r in routes:
        tanker_id = r.get("tanker_id", "unknown")
        load = int(r.get("load_litres", 0))
        capacity = int(r.get("capacity_litres", 0))
        route_km = float(r.get("route_km", 0.0))
        max_km = float(r.get("max_route_km", 0.0))

        if load > capacity:
            capacity_ok = False
            violation_count += 1
            violations.append({
                "code": "TANKER_CAPACITY_EXCEEDED",
                "detail": f"Tanker {tanker_id} load ({load} L) exceeds capacity ({capacity} L)"
            })

        if route_km > max_km + 0.01:
            route_limit_ok = False
            violation_count += 1
            violations.append({
                "code": "ROUTE_LIMIT_EXCEEDED",
                "detail": f"Tanker {tanker_id} route km ({route_km:.2f}) exceeds max route km ({max_km:.2f})"
            })

    # 4. Unavailable tanker deliveries
    unavailable_tanker_ok = True
    unavailable_tanker_ids = set()

    if scen_dict and "tankers" in scen_dict:
        for t in scen_dict["tankers"]:
            if t.get("status") == "unavailable":
                unavailable_tanker_ids.add(t.get("tanker_id"))
    else:
        for r in routes:
            if r.get("status") == "unavailable":
                unavailable_tanker_ids.add(r.get("tanker_id"))

    # Count each delivery made by an unavailable tanker
    for r in routes:
        tid = r.get("tanker_id")
        if tid in unavailable_tanker_ids:
            stops = r.get("stops", [])
            deliveries_count = sum(1 for s in stops if int(s.get("litres", 0)) > 0)
            if deliveries_count > 0:
                unavailable_tanker_ok = False
                violation_count += deliveries_count
                violations.append({
                    "code": "UNAVAILABLE_TANKER_USED",
                    "detail": f"Unavailable tanker {tid} performed {deliveries_count} delivery/deliveries"
                })

    return IndependentConstraintCheckResult(
        water_cap_ok=water_cap_ok,
        capacity_ok=capacity_ok,
        route_limit_ok=route_limit_ok,
        unavailable_tanker_ok=unavailable_tanker_ok,
        violations=violations,
        violation_count=violation_count,
    )
