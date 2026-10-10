"""Fairness-aware water allocation (MEMBER_A F3): a CP-SAT model plus a greedy-by-priority fallback.

Formulation (units of 100 L; only AVAILABLE tankers take part)
-------------------------------------------------------------
Data, for area i (in rank order) and available tanker k:
  s_i  = floor(sf_i / 100)                       whole deliverable units of the predicted shortfall
  c_i  = min(s_i, ceil((1 - phi) * sf_i / 100))  "normal" deficit allowance; phi = A_COVERAGE_FLOOR (0.25)
  P_i  = round(p_i * 1000 * 100)                 priority cost per unmet unit (p_i = F-5 priority)
  K_i  = round(kappa * P_i)                      cost per unmet unit below the coverage floor; kappa = A_FLOOR_PENALTY
  T_i  = round(delta * d(depot, i) * 1000)       travel proxy per visit; delta = weights.travel
  C_k  = floor(capacity_k / 100),  R_k = max_route_km_k (in metres, rounded down),  S = floor(supply / 100)

Variables:
  y[k,i] in [0, min(C_k, s_i)]   units tanker k delivers to area i
  z[k,i] in {0, 1}               tanker k visits area i:  z <= y <= min(C_k, s_i) * z
  Pairs with 2 * d(depot, i) > R_k cannot be visited at all and get no variables.

Constraints:
  x_i = sum_k y[k,i] <= s_i                      (never more than the shortfall)
  sum_i y[k,i] <= C_k                            (tanker capacity)
  sum_{k,i} y[k,i] <= S                          (total tanker water)
  sum_i 2 * d(depot, i) * z[k,i] <= R_k          (star route bound, d in whole metres rounded up; with a metric
                                                  distance matrix any tour over the visited set is no longer,
                                                  so routes stay feasible)

Unmet and objective:
  u_i  = s_i - x_i,   u2_i = max(0, u_i - c_i)   (deficit below the coverage floor),   u1_i = u_i - u2_i <= c_i
  minimise  sum_i P_i * (u1_i + kappa * u2_i) + sum_{k,i} T_i * z[k,i]
         =  sum_i (P_i * u_i + (K_i - P_i) * u2_i) + sum_{k,i} T_i * z[k,i]

``u2_i`` is pinned with an exact max-equality, so the objective is correct for any kappa (with kappa >= 1 it is
the convex split of the specification). Consecutive identical available tankers (same C_k and R_k) are
symmetry-broken by load (load_k >= load_k+1); this removes only mirror-image solutions.

Solve and determinism
---------------------
Stage 1 is the specified solve: ``max_time_in_seconds = max(0.1, time_limit_s - 0.5)``, ``num_workers = 8``,
``random_seed = 42``, warm-started with the greedy plan as a complete hint (trips of interchangeable tankers
reordered by load so the hint respects the symmetry breaking). OPTIMAL maps to ``optimal``; FEASIBLE to
``time_limited`` when the time limit was reached, otherwise ``feasible``.

Multi-worker CP-SAT may return any of several equally good solutions. When stage 1 proves optimality, stage 2
canonicalises the plan: it adds ``objective <= optimum`` and minimises a secondary tie-break objective
(unmet units weighted by rank, so ties go to higher-priority areas, then the number of stops) with ONE worker
and a deterministic-time budget. A single worker is deterministic, and the optimum value is unique, so
identical inputs always yield the identical plan. Stage 2 shares the stage 1 wall-clock budget (at least 0.2 s);
if that clock stops it first, the plan is still optimal but the choice among equally good plans may vary (this
is logged as ``canonicalisation_time_limited``). If stage 2 finds nothing, the stage 1 solution is kept.

Fallback
--------
``solve_greedy`` fills areas in rank order, first up to the coverage floor and then to the full shortfall,
using tankers that already visit the area before opening new stops, and never breaking capacity, supply or
the star route bound. It is always feasible.
"""
from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from ortools.sat.python import cp_model

from ..domain.constraints import (
    DELIVERY_UNIT_LITRES,
    distance,
    km_to_metres_down,
    km_to_metres_up,
    litres_to_units,
)
from ..domain.entities import Scenario, Weights
from ..domain.priority import AreaPriority
from ..logging_setup import log_event

ENGINE_CP_SAT = "ortools-cp-sat"
ENGINE_GREEDY = "greedy-fallback"

NUM_WORKERS = 8
RANDOM_SEED = 42
MIN_SOLVER_TIME_S = 0.1
CANONICAL_DETERMINISTIC_TIME = 2.0
CANONICAL_MIN_WALL_TIME_S = 0.2

AllocationStatus = Literal["optimal", "feasible", "time_limited", "heuristic"]
PairKey = tuple[str, str]  # (tanker_id, area_id)


@dataclass(frozen=True)
class AreaInput:
    area_id: str
    shortfall_units: int
    normal_deficit_cap: int
    priority_coef: int
    floor_coef: int
    travel_coef: int
    depot_metres: int

    @property
    def floor_units(self) -> int:
        """Units needed to reach the coverage floor."""
        return self.shortfall_units - self.normal_deficit_cap


@dataclass(frozen=True)
class TankerInput:
    tanker_id: str
    capacity_units: int
    route_limit_metres: int

    def can_visit(self, area: AreaInput) -> bool:
        """The tanker can carry water and the area fits its route bound on its own."""
        return self.capacity_units > 0 and 2 * area.depot_metres <= self.route_limit_metres


@dataclass(frozen=True)
class AllocationProblem:
    areas: tuple[AreaInput, ...]  # rank order: index 0 is rank 1
    tankers: tuple[TankerInput, ...]  # available tankers, tanker_id order
    supply_units: int

    def pairs(self) -> list[tuple[int, int]]:
        """(tanker index, area index) pairs that may carry water."""
        return [
            (k, i)
            for k, tanker in enumerate(self.tankers)
            for i, area in enumerate(self.areas)
            if area.shortfall_units > 0 and tanker.can_visit(area)
        ]


@dataclass(frozen=True)
class AllocationOutcome:
    engine: str
    status: AllocationStatus
    units: dict[PairKey, int]  # (tanker_id, area_id) -> delivered units (> 0 only)
    objective: int


def build_problem(
    ranked: Sequence[AreaPriority],
    scenario: Scenario,
    weights: Weights,
    *,
    coverage_floor: float,
    floor_penalty: float,
) -> AllocationProblem:
    """Integer problem data from the ranked priority records (see the module docstring)."""
    depot_id = scenario.depot.depot_id
    areas = []
    for rec in ranked:
        shortfall = rec.shortfall_litres
        units = litres_to_units(shortfall)
        normal_cap = min(units, math.ceil(round((1.0 - coverage_floor) * shortfall / DELIVERY_UNIT_LITRES, 9)))
        priority_coef = round(rec.priority_weight * 1000 * DELIVERY_UNIT_LITRES)
        depot_km = distance(scenario.distance_km, depot_id, rec.area_id)
        areas.append(
            AreaInput(
                area_id=rec.area_id,
                shortfall_units=units,
                normal_deficit_cap=max(0, normal_cap),
                priority_coef=priority_coef,
                floor_coef=round(floor_penalty * priority_coef),
                travel_coef=round(weights.travel * depot_km * 1000),
                depot_metres=km_to_metres_up(depot_km),
            )
        )
    tankers = tuple(
        TankerInput(
            tanker_id=t.tanker_id,
            capacity_units=litres_to_units(t.capacity_litres),
            route_limit_metres=km_to_metres_down(t.max_route_km),
        )
        for t in sorted(scenario.tankers, key=lambda t: t.tanker_id)
        if t.status == "available"
    )
    return AllocationProblem(
        areas=tuple(areas),
        tankers=tankers,
        supply_units=litres_to_units(scenario.supply.tanker_water_available_litres),
    )


def objective_value(problem: AllocationProblem, units: Mapping[PairKey, int]) -> int:
    """The main objective of a plan, evaluated exactly as the CP-SAT model scores it."""
    delivered: dict[str, int] = defaultdict(int)
    visits: dict[str, int] = defaultdict(int)
    for (_, area_id), q in units.items():
        if q > 0:
            delivered[area_id] += q
            visits[area_id] += 1
    total = 0
    for area in problem.areas:
        unmet = area.shortfall_units - delivered[area.area_id]
        below_floor = max(0, unmet - area.normal_deficit_cap)
        total += area.priority_coef * unmet + (area.floor_coef - area.priority_coef) * below_floor
        total += area.travel_coef * visits[area.area_id]
    return total


# --------------------------------------------------------------------------- greedy fallback
def solve_greedy(problem: AllocationProblem) -> AllocationOutcome:
    """Greedy-by-priority plan (always feasible; see the module docstring)."""
    n_tankers = len(problem.tankers)
    spare = [t.capacity_units for t in problem.tankers]
    slack = [t.route_limit_metres for t in problem.tankers]
    visited: list[set[int]] = [set() for _ in range(n_tankers)]
    supply_left = problem.supply_units
    given = [0] * len(problem.areas)
    units: dict[tuple[int, int], int] = defaultdict(int)

    targets = ([a.floor_units for a in problem.areas], [a.shortfall_units for a in problem.areas])
    for target in targets:
        for i, area in enumerate(problem.areas):
            order = sorted(range(n_tankers), key=lambda k, i=i: (i not in visited[k], k))
            for k in order:
                want = target[i] - given[i]
                if want <= 0 or supply_left <= 0:
                    break
                tanker = problem.tankers[k]
                if spare[k] <= 0 or not tanker.can_visit(area):
                    continue
                if i not in visited[k]:
                    if 2 * area.depot_metres > slack[k]:
                        continue
                    slack[k] -= 2 * area.depot_metres
                    visited[k].add(i)
                q = min(want, spare[k], supply_left)
                units[(k, i)] += q
                spare[k] -= q
                supply_left -= q
                given[i] += q

    result = _keyed(problem, units)
    return AllocationOutcome(
        engine=ENGINE_GREEDY, status="heuristic", units=result, objective=objective_value(problem, result)
    )


def _keyed(problem: AllocationProblem, units: Mapping[tuple[int, int], int]) -> dict[PairKey, int]:
    return {
        (problem.tankers[k].tanker_id, problem.areas[i].area_id): q
        for (k, i), q in sorted(units.items())
        if q > 0
    }


# --------------------------------------------------------------------------- CP-SAT
@dataclass
class _CpSatModel:
    model: cp_model.CpModel
    y: dict[tuple[int, int], cp_model.IntVar]
    z: dict[tuple[int, int], cp_model.IntVar]
    main: cp_model.LinearExprT
    tie_break: cp_model.LinearExprT


def identical_tanker_groups(problem: AllocationProblem) -> list[list[int]]:
    """Runs of consecutive tankers (by index) with equal capacity and route limit: interchangeable in the model."""
    groups: list[list[int]] = []
    for k, tanker in enumerate(problem.tankers):
        if groups and _interchangeable(problem.tankers[groups[-1][-1]], tanker):
            groups[-1].append(k)
        else:
            groups.append([k])
    return groups


def _interchangeable(a: TankerInput, b: TankerInput) -> bool:
    return (a.capacity_units, a.route_limit_metres) == (b.capacity_units, b.route_limit_metres)


def symmetric_hint(problem: AllocationProblem, units: Mapping[PairKey, int]) -> dict[tuple[int, int], int]:
    """``units`` keyed by (tanker index, area index), with the trips of interchangeable tankers reordered so their
    loads are non-increasing; otherwise the model's symmetry breaking could reject the hint."""
    tanker_index = {t.tanker_id: k for k, t in enumerate(problem.tankers)}
    area_index = {a.area_id: i for i, a in enumerate(problem.areas)}
    trips: dict[int, dict[int, int]] = defaultdict(dict)
    for (tanker_id, area_id), q in units.items():
        if q > 0:
            trips[tanker_index[tanker_id]][area_index[area_id]] = q
    ordered: dict[tuple[int, int], int] = {}
    for group in identical_tanker_groups(problem):
        bundles = sorted((trips.get(k, {}) for k in group), key=lambda trip: -sum(trip.values()))
        for k, bundle in zip(group, bundles, strict=True):
            ordered.update({(k, i): q for i, q in bundle.items()})
    return ordered


def _build_model(problem: AllocationProblem, hint: Mapping[PairKey, int] | None) -> _CpSatModel:
    m = cp_model.CpModel()
    pairs = problem.pairs()
    hinted = symmetric_hint(problem, hint) if hint is not None else None
    y: dict[tuple[int, int], cp_model.IntVar] = {}
    z: dict[tuple[int, int], cp_model.IntVar] = {}
    for k, i in pairs:
        ub = min(problem.tankers[k].capacity_units, problem.areas[i].shortfall_units)
        y[k, i] = m.new_int_var(0, ub, f"y_{k}_{i}")
        z[k, i] = m.new_bool_var(f"z_{k}_{i}")
        m.add(y[k, i] <= ub * z[k, i])
        m.add(y[k, i] >= z[k, i])

    by_area: dict[int, list[tuple[int, int]]] = defaultdict(list)
    by_tanker: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for k, i in pairs:
        by_area[i].append((k, i))
        by_tanker[k].append((k, i))

    for i, area in enumerate(problem.areas):
        if by_area[i]:
            m.add(sum(y[p] for p in by_area[i]) <= area.shortfall_units)
    loads = {}
    for k, tanker in enumerate(problem.tankers):
        loads[k] = sum(y[p] for p in by_tanker[k])
        if by_tanker[k]:
            m.add(loads[k] <= tanker.capacity_units)
            m.add(sum(2 * problem.areas[p[1]].depot_metres * z[p] for p in by_tanker[k]) <= tanker.route_limit_metres)
    if y:
        m.add(sum(y.values()) <= problem.supply_units)

    # Mirror-image symmetry: interchangeable consecutive tankers carry non-increasing loads.
    for group in identical_tanker_groups(problem):
        for k, nxt in zip(group, group[1:], strict=False):
            if by_tanker[k]:
                m.add(loads[k] >= loads[nxt])

    main_terms = []
    tie_terms = []
    n = len(problem.areas)
    for i, area in enumerate(problem.areas):
        unmet = area.shortfall_units - sum(y[p] for p in by_area[i])
        main_terms.append(area.priority_coef * unmet)
        tie_terms.append((n - i) * unmet)
        if area.floor_units > 0:
            below_floor = m.new_int_var(0, area.floor_units, f"u2_{i}")
            m.add_max_equality(below_floor, [0, unmet - area.normal_deficit_cap])
            main_terms.append((area.floor_coef - area.priority_coef) * below_floor)
            if hinted is not None:
                hinted_unmet = area.shortfall_units - sum(hinted.get(p, 0) for p in by_area[i])
                m.add_hint(below_floor, max(0, hinted_unmet - area.normal_deficit_cap))
    for (_, i), visit in z.items():
        main_terms.append(problem.areas[i].travel_coef * visit)
        tie_terms.append(visit)

    main = sum(main_terms)
    m.minimize(main)

    if hinted is not None:
        for pair, var in y.items():
            q = hinted.get(pair, 0)
            m.add_hint(var, q)
            m.add_hint(z[pair], 1 if q > 0 else 0)
    return _CpSatModel(model=m, y=y, z=z, main=main, tie_break=sum(tie_terms))


def _extract(problem: AllocationProblem, built: _CpSatModel, solver: cp_model.CpSolver) -> dict[PairKey, int]:
    raw = {pair: int(solver.value(var)) for pair, var in built.y.items()}
    return _keyed(problem, raw)


def solve_cpsat(
    problem: AllocationProblem,
    max_time_s: float,
    *,
    hint: Mapping[PairKey, int] | None = None,
    canonical_deadline: float | None = None,
) -> AllocationOutcome | None:
    """Solve with CP-SAT; ``None`` when no solution was found within ``max_time_s``.

    ``canonical_deadline`` (a ``time.perf_counter()`` value) bounds the wall time of the stage 2
    canonicalisation; it always gets at least ``CANONICAL_MIN_WALL_TIME_S``.
    When no tanker can carry water to any area with a shortfall, the empty plan is the only (hence optimal)
    plan and is returned without building a model.
    """
    started = time.perf_counter()
    if not problem.pairs():
        return AllocationOutcome(engine=ENGINE_CP_SAT, status="optimal", units={}, objective=objective_value(problem, {}))
    built = _build_model(problem, hint)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(MIN_SOLVER_TIME_S, max_time_s)
    solver.parameters.num_workers = NUM_WORKERS
    solver.parameters.random_seed = RANDOM_SEED
    status = solver.solve(built.model)

    if status == cp_model.OPTIMAL:
        result_status: AllocationStatus = "optimal"
    elif status == cp_model.FEASIBLE:
        limit_hit = solver.wall_time >= 0.95 * solver.parameters.max_time_in_seconds
        result_status = "time_limited" if limit_hit else "feasible"
    else:
        return None

    units = _extract(problem, built, solver)
    if result_status == "optimal":
        optimum = round(solver.objective_value)
        deadline = canonical_deadline if canonical_deadline is not None else started + max_time_s
        canonical = _canonicalise(problem, built, optimum, deadline)
        if canonical is not None:
            units = canonical
    return AllocationOutcome(
        engine=ENGINE_CP_SAT, status=result_status, units=units, objective=objective_value(problem, units)
    )


def _canonicalise(
    problem: AllocationProblem, built: _CpSatModel, optimum: int, deadline: float
) -> dict[PairKey, int] | None:
    """Stage 2: the deterministic optimal plan preferred by the tie-break objective (module docstring)."""
    built.model.add(built.main <= optimum)
    built.model.minimize(built.tie_break)
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = RANDOM_SEED
    solver.parameters.max_deterministic_time = CANONICAL_DETERMINISTIC_TIME
    solver.parameters.max_time_in_seconds = max(CANONICAL_MIN_WALL_TIME_S, deadline - time.perf_counter())
    status = solver.solve(built.model)
    if status != cp_model.OPTIMAL and solver.wall_time >= 0.95 * solver.parameters.max_time_in_seconds:
        # Still an optimal plan, but which of several equally good plans is returned may now vary between runs.
        log_event(
            "canonicalisation_time_limited",
            "tie-break stage stopped at its wall-clock limit before settling the canonical optimal plan",
            level=logging.WARNING,
        )
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    return _extract(problem, built, solver)
