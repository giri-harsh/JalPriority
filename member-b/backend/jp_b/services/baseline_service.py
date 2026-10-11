import secrets
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Literal, Union, Any

from jp_b.api.schemas import (
    Weights,
    Scenario,
    PredictionSet,
    PlanResult,
    SolverInfo,
    PredictionInfo,
    Totals,
    Allocation,
    Delivery,
    Reason,
    Route,
    RouteStop,
    Unserved,
    ConstraintCheck,
)
from jp_b.domain.priority import compute_priorities_for_scenario
from jp_b.domain.metrics import compute_unmet, compute_coverage
from jp_b.domain.constraints import check_constraints


def _calculate_path_km(path: List[str], distance_matrix: Dict[str, Dict[str, float]]) -> float:
    if not path or len(path) < 2:
        return 0.0
    total = 0.0
    for i in range(len(path) - 1):
        u, v = path[i], path[i + 1]
        dist = distance_matrix.get(u, {}).get(v, 0.0)
        total += dist
    return round(total, 2)


def solve_baseline(
    strategy: Literal["fcfs", "nearest_first"],
    scenario: Union[Scenario, dict],
    prediction: Union[PredictionSet, dict],
    weights: Weights,
    debt_by_area: Dict[str, int],
) -> PlanResult:
    """
    Implement baseline allocation strategies (FCFS and Nearest-First)
    per CONTRACT §5.6 and MEMBER_B.md F7 (B5).
    """
    start_time = time.time()

    scen_dict = scenario if isinstance(scenario, dict) else scenario.model_dump()
    pred_dict = prediction if isinstance(prediction, dict) else prediction.model_dump()

    depot_id = scen_dict["depot"]["depot_id"]
    distance_matrix = scen_dict["distance_km"]
    supply_available = int(scen_dict["supply"]["tanker_water_available_litres"])
    areas = scen_dict["areas"]

    # Build predictions lookup
    pred_area_map = {p["area_id"]: p for p in pred_dict.get("areas", [])}

    # 1. Order areas by strategy with area_id tie-breaker
    if strategy == "fcfs":
        # sort by request_time, tie-break by area_id
        ordered_areas = sorted(
            areas,
            key=lambda a: (a.get("request_time") or "", a["area_id"])
        )
    elif strategy == "nearest_first":
        # sort by distance from depot, tie-break by area_id
        ordered_areas = sorted(
            areas,
            key=lambda a: (
                distance_matrix.get(depot_id, {}).get(a["area_id"], float("inf")),
                a["area_id"]
            )
        )
    else:
        raise ValueError(f"Unknown baseline strategy: {strategy}")

    # Available tankers sorted by tanker_id
    all_tankers = sorted(scen_dict["tankers"], key=lambda t: t["tanker_id"])
    available_tankers = [t for t in all_tankers if t.get("status") == "available"]

    # State tracking for tankers
    # tanker_id -> { "capacity": C, "max_route_km": M, "load": L, "stops": [], "path": [depot] }
    tanker_state: Dict[str, Dict[str, Any]] = {}
    for t in all_tankers:
        tid = t["tanker_id"]
        status = t.get("status", "available")
        cap = int(t.get("capacity_litres", 0))
        max_km = float(t.get("max_route_km", 0.0))
        tanker_state[tid] = {
            "tanker_id": tid,
            "status": status,
            "capacity": cap,
            "max_route_km": max_km,
            "load": 0,
            "stops": [],
            "path": [depot_id] if status == "available" else [],
            "route_km": 0.0,
        }

    remaining_supply = supply_available
    deliveries_by_area: Dict[str, List[Dict[str, Any]]] = {a["area_id"]: [] for a in areas}
    unserved_reasons: Dict[str, str] = {}

    # Compute priorities for all areas per F-5
    priorities = compute_priorities_for_scenario(
        areas=areas,
        debt_by_area=debt_by_area,
        weights=weights,
        predictions=pred_dict.get("areas", []),
    )

    # 2. Allocation loop walking areas in order
    for area in ordered_areas:
        aid = area["area_id"]
        req = int(area.get("requested_litres", 0))
        # Target per area = requested_litres rounded down to 100 L
        target = (req // 100) * 100

        if target <= 0:
            continue

        if remaining_supply <= 0:
            unserved_reasons[aid] = "SUPPLY_EXHAUSTED"
            continue

        delivered_for_area = 0

        # Try to fill available tankers
        for t in available_tankers:
            tid = t["tanker_id"]
            state = tanker_state[tid]
            rem_cap = state["capacity"] - state["load"]

            if rem_cap <= 0:
                continue

            # Route limit check: before adding stop, verify route km <= max_route_km
            current_path = state["path"]  # starts with depot_id
            # Proposed path: current path without trailing depot, then area, then return to depot
            base_nodes = current_path if len(current_path) == 1 else current_path[:-1]
            proposed_path = base_nodes + [aid, depot_id]
            proposed_km = _calculate_path_km(proposed_path, distance_matrix)

            if proposed_km > state["max_route_km"] + 0.01:
                # Tanker route limit exceeded; try next tanker
                continue

            # Amount to deliver on this tanker
            needed = target - delivered_for_area
            alloc = min(needed, rem_cap, remaining_supply)
            # Ensure alloc is in 100 L increments
            alloc = (alloc // 100) * 100

            if alloc <= 0:
                continue

            # Add stop
            stop_order = len(state["stops"]) + 1
            state["stops"].append({
                "stop_order": stop_order,
                "area_id": aid,
                "litres": alloc,
            })
            state["load"] += alloc
            state["path"] = proposed_path
            state["route_km"] = proposed_km

            remaining_supply -= alloc
            delivered_for_area += alloc

            deliveries_by_area[aid].append({
                "tanker_id": tid,
                "stop_order": stop_order,
                "litres": alloc,
            })

            if delivered_for_area >= target or remaining_supply <= 0:
                break

        if delivered_for_area == 0:
            if remaining_supply <= 0:
                unserved_reasons[aid] = "SUPPLY_EXHAUSTED"
            else:
                unserved_reasons[aid] = "ROUTE_LIMIT"

    # 3. Assemble routes
    routes_list: List[Route] = []
    for t in all_tankers:
        tid = t["tanker_id"]
        st = tanker_state[tid]
        status = st["status"]
        if status == "available":
            route_status = "assigned" if st["stops"] else "idle"
            path = st["path"] if st["stops"] else []
            route_km = st["route_km"] if st["stops"] else 0.0
        else:
            route_status = "unavailable"
            path = []
            route_km = 0.0

        routes_list.append(
            Route(
                tanker_id=tid,
                status=route_status,
                capacity_litres=st["capacity"],
                max_route_km=st["max_route_km"],
                load_litres=st["load"],
                route_km=route_km,
                stops=[RouteStop(**s) for s in st["stops"]],
                path=path,
            )
        )

    # 4. Assemble allocations
    allocations_list: List[Allocation] = []
    unserved_list: List[Unserved] = []

    # Partition ordered areas into served and unserved to assign rank
    served_areas = [a for a in ordered_areas if sum(d["litres"] for d in deliveries_by_area[a["area_id"]]) > 0]
    unserved_areas = [a for a in ordered_areas if sum(d["litres"] for d in deliveries_by_area[a["area_id"]]) == 0]
    ranked_areas = served_areas + unserved_areas

    for rank_idx, area in enumerate(ranked_areas, start=1):
        aid = area["area_id"]
        pred_a = pred_area_map.get(aid, {})
        sf_i = int(pred_a.get("predicted_shortfall_litres", area.get("requested_litres", 0)))
        delivs = deliveries_by_area[aid]
        allocated_litres = sum(d["litres"] for d in delivs)
        unmet_litres = compute_unmet(sf_i, allocated_litres)
        coverage_ratio = compute_coverage(sf_i, allocated_litres)

        p_info = priorities[aid]

        # Determine reasons and reason_text
        order_code = "FCFS_ORDER" if strategy == "fcfs" else "NEAREST_FIRST_ORDER"
        order_pos = next(idx for idx, a in enumerate(ordered_areas, start=1) if a["area_id"] == aid)

        reasons: List[Reason] = []
        if strategy == "fcfs":
            reasons.append(Reason(code="FCFS_ORDER", params={"request_time": area.get("request_time"), "position": order_pos}))
        else:
            dist_depot = distance_matrix.get(depot_id, {}).get(aid, 0.0)
            reasons.append(Reason(code="NEAREST_FIRST_ORDER", params={"distance_from_depot_km": dist_depot, "position": order_pos}))

        if allocated_litres >= sf_i and sf_i > 0:
            reasons.append(Reason(code="FULLY_SERVED", params={"allocated_litres": allocated_litres}))
            reason_text = f"Served in full ({allocated_litres:,} L) under {strategy.upper()} order (position {order_pos})."
        elif allocated_litres > 0:
            reasons.append(Reason(code="PARTIALLY_SERVED", params={"allocated_litres": allocated_litres, "predicted_shortfall_litres": sf_i}))
            reason_text = f"Partially served ({allocated_litres:,} L of {sf_i:,} L) under {strategy.upper()} order."
        else:
            reasons.append(Reason(code="NOT_SERVED", params={"predicted_shortfall_litres": sf_i}))
            cause = unserved_reasons.get(aid, "SUPPLY_EXHAUSTED")
            if cause == "SUPPLY_EXHAUSTED":
                reasons.append(Reason(code="SUPPLY_EXHAUSTED", params={"supply_available_litres": supply_available, "unmet_litres": unmet_litres}))
                reason_text = f"Not served under {strategy.upper()} order; tanker water supply exhausted."
            else:
                dist_depot = distance_matrix.get(depot_id, {}).get(aid, 0.0)
                reasons.append(Reason(code="ROUTE_LIMIT", params={"distance_from_depot_km": dist_depot}))
                reason_text = f"Not served under {strategy.upper()} order; route limit exceeded."

        if unmet_litres > 0:
            cause_code = unserved_reasons.get(aid, "SUPPLY_EXHAUSTED")
            unserved_list.append(
                Unserved(
                    area_id=aid,
                    unmet_litres=unmet_litres,
                    reason_code=cause_code if cause_code in ["SUPPLY_EXHAUSTED", "ROUTE_LIMIT"] else "SUPPLY_EXHAUSTED",  # type: ignore
                )
            )

        allocations_list.append(
            Allocation(
                area_id=aid,
                rank=rank_idx,
                predicted_shortfall_litres=sf_i,
                allocated_litres=allocated_litres,
                unmet_litres=unmet_litres,
                coverage_ratio=coverage_ratio,
                priority_weight=p_info.priority_weight,
                urgency=p_info.urgency,
                debt_litres_used=p_info.debt_litres_used,
                debt_normalized=p_info.debt_normalized,
                vulnerability_used=p_info.vulnerability_used,
                deliveries=[Delivery(**d) for d in delivs],
                reasons=reasons,
                reason_text=reason_text,
                is_unserved_remainder=(unmet_litres > 0),
            )
        )

    # Sort allocations by rank
    allocations_list.sort(key=lambda a: a.rank)

    # Calculate totals
    total_allocated = sum(a.allocated_litres for a in allocations_list)
    total_shortfall = sum(a.predicted_shortfall_litres for a in allocations_list)
    total_unmet = sum(a.unmet_litres for a in allocations_list)
    total_travel_km = round(sum(r.route_km for r in routes_list), 2)
    tanker_cap_avail = sum(t["capacity_litres"] for t in available_tankers)

    totals = Totals(
        supply_available_litres=supply_available,
        tanker_capacity_available_litres=tanker_cap_avail,
        shortfall_litres=total_shortfall,
        allocated_litres=total_allocated,
        unmet_litres=total_unmet,
        travel_km=total_travel_km,
    )

    elapsed_ms = int((time.time() - start_time) * 1000)

    solver_info = SolverInfo(
        engine="baseline-fcfs" if strategy == "fcfs" else "baseline-nearest",
        status="heuristic",
        solve_time_ms=elapsed_ms,
        time_limit_s=None,
    )

    pred_info = PredictionInfo(
        model_version=pred_dict.get("model_version", "mock-0"),
        latency_ms=int(pred_dict.get("latency_ms", 1)),
        run_mae_litres=pred_dict.get("run_mae_litres"),
    )

    result_id = f"res_{secrets.token_hex(6)}"

    # Check constraints
    constraint_check = check_constraints(
        result={
            "totals": totals.model_dump(),
            "routes": [r.model_dump() for r in routes_list],
            "allocations": [a.model_dump() for a in allocations_list],
        },
        scenario=scen_dict,
    ).to_schema()

    return PlanResult(
        schema_version="1.0",
        result_id=result_id,
        strategy=strategy,
        scenario_id=scen_dict["scenario_id"],
        service_area_id=scen_dict["service_area_id"],
        window_index=int(scen_dict["window_index"]),
        generated_at=datetime.now(timezone.utc),
        weights=weights,
        solver=solver_info,
        prediction=pred_info,
        totals=totals,
        allocations=allocations_list,
        routes=routes_list,
        unserved=unserved_list,
        constraint_check=constraint_check,
    )
