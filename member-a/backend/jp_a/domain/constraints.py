"""Route length (F-8) and constraint violations (F-15), CONTRACT section 5.11. Pure functions, no I/O.

``build_constraint_check`` is an independent re-computation: it trusts nothing a plan says about itself.
Tanker capacities and statuses come from the scenario, loads are re-summed from the allocations' deliveries,
and route lengths are re-measured on the scenario's distance matrix.

F-15 (count):
  +1 if sum(allocated) > supply_available_litres                     WATER_CAP_EXCEEDED
  +1 per tanker whose load exceeds its capacity                       TANKER_CAPACITY_EXCEEDED
  +1 per route with route_km > max_route_km + 0.01                    ROUTE_LIMIT_EXCEEDED
  +1 per delivery made by an unavailable (or unknown) tanker          UNAVAILABLE_TANKER_USED
"""
from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence

from .entities import Allocation, ConstraintCheck, Route, Scenario, Violation

DELIVERY_UNIT_LITRES = 100
ROUTE_TOLERANCE_KM = 0.01
KM_DP = 2


def distance(distance_km: Mapping[str, Mapping[str, float]], a: str, b: str) -> float:
    """``distance_km[a][b]``, falling back to the symmetric entry when only ``[b][a]`` is present."""
    if a == b:
        return 0.0
    row = distance_km.get(a)
    if row is not None and b in row:
        return float(row[b])
    row = distance_km.get(b)
    if row is not None and a in row:
        return float(row[a])
    raise KeyError(f"distance_km has no entry for {a} -> {b}")


def path_length_km(path: Sequence[str], distance_km: Mapping[str, Mapping[str, float]]) -> float:
    """Unrounded sum of consecutive matrix entries along ``path`` (0.0 for an empty or single-node path)."""
    return sum(distance(distance_km, a, b) for a, b in zip(path, path[1:], strict=False))


def route_km(path: Sequence[str], distance_km: Mapping[str, Mapping[str, float]]) -> float:
    """F-8: route length rounded to 2 dp; 0.0 when the path is empty."""
    return round(path_length_km(path, distance_km), KM_DP)


def litres_to_units(litres: int) -> int:
    """Whole 100 L delivery units that fit in ``litres`` (rounded DOWN: used for supply and tanker capacity)."""
    return max(0, int(litres)) // DELIVERY_UNIT_LITRES


def litres_to_units_up(litres: int) -> int:
    """Whole 100 L delivery units needed to cover ``litres`` in full (rounded UP: 6,821 L -> 69 units)."""
    return -(-max(0, int(litres)) // DELIVERY_UNIT_LITRES)


def km_to_metres_up(km: float) -> int:
    """Distance in whole metres, rounded UP (conservative when summing distances against a limit)."""
    return math.ceil(round(km * 1000.0, 6))


def km_to_metres_down(km: float) -> int:
    """Limit in whole metres, rounded DOWN (conservative for an upper bound)."""
    return math.floor(round(km * 1000.0, 6))


def find_violations(scenario: Scenario, allocations: Sequence[Allocation], routes: Sequence[Route]) -> list[Violation]:
    """All F-15 violations of a plan, recomputed from the scenario (see the module docstring)."""
    tankers = {t.tanker_id: t for t in scenario.tankers}
    supply = scenario.supply.tanker_water_available_litres
    violations: list[Violation] = []

    allocated = sum(a.allocated_litres for a in allocations)
    if allocated > supply:
        violations.append(
            Violation(code="WATER_CAP_EXCEEDED", detail=f"allocated {allocated} L exceeds supply {supply} L")
        )

    loads: dict[str, int] = defaultdict(int)
    for alloc in allocations:
        for d in alloc.deliveries:
            loads[d.tanker_id] += d.litres
    for tanker_id in sorted(loads):
        tanker = tankers.get(tanker_id)
        if tanker is not None and loads[tanker_id] > tanker.capacity_litres:
            violations.append(
                Violation(
                    code="TANKER_CAPACITY_EXCEEDED",
                    detail=f"{tanker_id} load {loads[tanker_id]} L exceeds capacity {tanker.capacity_litres} L",
                )
            )

    for route in sorted(routes, key=lambda r: r.tanker_id):
        tanker = tankers.get(route.tanker_id)
        limit = tanker.max_route_km if tanker is not None else route.max_route_km
        km = route_km(route.path, scenario.distance_km)
        if km > limit + ROUTE_TOLERANCE_KM:
            violations.append(
                Violation(
                    code="ROUTE_LIMIT_EXCEEDED",
                    detail=f"{route.tanker_id} route {km:.2f} km exceeds max {limit:.2f} km",
                )
            )

    for alloc in allocations:
        for d in alloc.deliveries:
            tanker = tankers.get(d.tanker_id)
            if tanker is None or tanker.status != "available":
                state = "unknown" if tanker is None else "unavailable"
                violations.append(
                    Violation(
                        code="UNAVAILABLE_TANKER_USED",
                        detail=f"{d.tanker_id} is {state} but delivers {d.litres} L to {alloc.area_id}",
                    )
                )
    return violations


def build_constraint_check(
    scenario: Scenario, allocations: Sequence[Allocation], routes: Sequence[Route]
) -> ConstraintCheck:
    """The PlanResult ``constraint_check`` block for a plan."""
    violations = find_violations(scenario, allocations, routes)
    codes = {v.code for v in violations}
    return ConstraintCheck(
        water_cap_ok="WATER_CAP_EXCEEDED" not in codes,
        capacity_ok="TANKER_CAPACITY_EXCEEDED" not in codes,
        route_limit_ok="ROUTE_LIMIT_EXCEEDED" not in codes,
        unavailable_tanker_ok="UNAVAILABLE_TANKER_USED" not in codes,
        violations=violations,
    )


def count_violations(scenario: Scenario, allocations: Sequence[Allocation], routes: Sequence[Route]) -> int:
    """F-15 constraint violation count."""
    return len(find_violations(scenario, allocations, routes))
