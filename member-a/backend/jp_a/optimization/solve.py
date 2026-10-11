"""Dispatch orchestration: allocation (CP-SAT, greedy fallback) -> per-tanker routing -> route repair.

Time budget, for a request limit ``time_limit_s`` of which ``elapsed_s`` was already spent upstream:
  * CP-SAT stage 1 gets ``max(0.1, time_limit_s - 0.5 - elapsed_s)`` seconds (MEMBER_A F3),
  * stage 2 canonicalisation shares that budget (minimum 0.2 s, deterministic-time capped),
  * routing of trips with more than 7 stops gets at most 0.3 s per tanker and stops before
    ``time_limit_s + 0.5`` after the request started,
so the response stays inside the contract's ``time_limit_s + 1.0`` seconds.

A time limit is never an error: if CP-SAT finds nothing (or raises), or stops at its time limit with a plan worse
than the greedy one, the greedy plan is used (engine ``greedy-fallback``, status ``heuristic``).

Route repair: the star bound guarantees route feasibility only for metric distance matrices. If a sequenced
trip still exceeds ``max_route_km + 0.01`` (F-15), its lowest-priority stop is dropped (that water becomes
unmet) until it fits; an ``optimal`` plan that needed repair is reported as ``feasible``.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

from ..domain.constraints import DELIVERY_UNIT_LITRES, ROUTE_TOLERANCE_KM, route_km
from ..domain.entities import Scenario
from ..logging_setup import log_event
from .allocation import (
    MIN_SOLVER_TIME_S,
    AllocationOutcome,
    AllocationProblem,
    AllocationStatus,
    solve_cpsat,
    solve_greedy,
)
from .routing import EXACT_MAX_STOPS, ORTOOLS_TIME_LIMIT_S, build_path, sequence_stops

SOLVER_MARGIN_S = 0.5
ROUTING_GRACE_S = 0.5
MIN_ROUTING_TIME_S = 0.05


class OptimizationError(RuntimeError):
    """Neither CP-SAT nor the greedy fallback produced a plan."""


@dataclass(frozen=True)
class TankerTrip:
    tanker_id: str
    stops: tuple[tuple[str, int], ...]  # (area_id, litres) in visiting order

    @property
    def area_ids(self) -> list[str]:
        return [area_id for area_id, _ in self.stops]

    @property
    def load_litres(self) -> int:
        return sum(litres for _, litres in self.stops)


@dataclass(frozen=True)
class DispatchSolution:
    engine: str
    status: AllocationStatus
    trips: dict[str, TankerTrip]  # tankers with at least one stop, keyed by tanker_id
    dropped: frozenset[str]  # areas that lost a stop in route repair

    def delivered_litres(self) -> dict[str, int]:
        totals: dict[str, int] = defaultdict(int)
        for trip in self.trips.values():
            for area_id, litres in trip.stops:
                totals[area_id] += litres
        return dict(totals)


def solver_budget_s(time_limit_s: float, elapsed_s: float = 0.0) -> float:
    """Stage 1 CP-SAT wall-time limit."""
    return max(MIN_SOLVER_TIME_S, time_limit_s - SOLVER_MARGIN_S - elapsed_s)


def allocate(problem: AllocationProblem, time_limit_s: float, *, elapsed_s: float = 0.0) -> AllocationOutcome:
    """CP-SAT allocation with the greedy plan as warm start and as fallback.

    The greedy plan is returned when CP-SAT finds nothing, raises, or stops at its time limit with a plan that
    scores worse than the greedy one (the contract's "best feasible plan" on a time limit).
    """
    started = time.perf_counter()
    budget = solver_budget_s(time_limit_s, elapsed_s)
    greedy: AllocationOutcome | None
    try:
        greedy = solve_greedy(problem)
    except Exception as exc:  # noqa: BLE001 - CP-SAT can still solve without the warm start
        log_event("greedy_failed", f"greedy plan failed: {exc!r}", level=logging.WARNING)
        greedy = None
    try:
        outcome = solve_cpsat(
            problem, budget, hint=greedy.units if greedy else None, canonical_deadline=started + budget
        )
    except Exception as exc:  # noqa: BLE001 - any solver crash degrades to the greedy plan
        log_event("cpsat_failed", f"CP-SAT raised, using greedy fallback: {exc!r}", level=logging.WARNING)
        outcome = None

    if greedy is None:
        if outcome is None:
            raise OptimizationError("neither CP-SAT nor the greedy fallback produced a plan")
        return outcome
    if outcome is None:
        log_event("greedy_fallback", "CP-SAT found no solution in time; using greedy fallback", level=logging.WARNING)
        return greedy
    if outcome.status != "optimal" and greedy.objective < outcome.objective:
        log_event(
            "greedy_better",
            f"CP-SAT stopped at {outcome.objective}, above the greedy plan's {greedy.objective}; using greedy",
            level=logging.WARNING,
        )
        return greedy
    return outcome


def build_trips(
    outcome: AllocationOutcome,
    scenario: Scenario,
    rank: Mapping[str, int],
    *,
    routing_deadline: float,
) -> tuple[dict[str, TankerTrip], frozenset[str]]:
    """Sequence every tanker's stops and repair any trip over its route limit.

    Returns ``(trips, dropped)``: the trips of tankers that still have stops, and the areas that lost a stop.
    """
    depot_id = scenario.depot.depot_id
    limits = {t.tanker_id: t.max_route_km for t in scenario.tankers}
    litres_by_tanker: dict[str, dict[str, int]] = defaultdict(dict)
    for (tanker_id, area_id), units in sorted(outcome.units.items()):
        litres_by_tanker[tanker_id][area_id] = units * DELIVERY_UNIT_LITRES

    long_trips = sum(1 for stops in litres_by_tanker.values() if len(stops) > EXACT_MAX_STOPS)
    trips: dict[str, TankerTrip] = {}
    dropped: set[str] = set()
    for tanker_id in sorted(litres_by_tanker):
        stops = dict(litres_by_tanker[tanker_id])
        while stops:
            time_limit = ORTOOLS_TIME_LIMIT_S
            if len(stops) > EXACT_MAX_STOPS:
                share = (routing_deadline - time.perf_counter()) / max(1, long_trips)
                time_limit = min(ORTOOLS_TIME_LIMIT_S, max(MIN_ROUTING_TIME_S, share))
            order = sequence_stops(depot_id, list(stops), scenario.distance_km, rank, time_limit_s=time_limit)
            if route_km(build_path(depot_id, order), scenario.distance_km) <= limits[tanker_id] + ROUTE_TOLERANCE_KM:
                trips[tanker_id] = TankerTrip(tanker_id, tuple((a, stops[a]) for a in order))
                break
            victim = max(stops, key=lambda a: (rank[a], a))
            log_event(
                "route_repaired",
                f"{tanker_id} exceeds its route limit; dropping stop {victim}",
                level=logging.WARNING,
            )
            del stops[victim]
            dropped.add(victim)
    return trips, frozenset(dropped)


def solve_dispatch(
    problem: AllocationProblem,
    scenario: Scenario,
    rank: Mapping[str, int],
    time_limit_s: float,
    *,
    elapsed_s: float = 0.0,
) -> DispatchSolution:
    """Allocation + routing for one window (see the module docstring)."""
    request_started = time.perf_counter() - elapsed_s
    outcome = allocate(problem, time_limit_s, elapsed_s=elapsed_s)
    trips, dropped = build_trips(
        outcome, scenario, rank, routing_deadline=request_started + time_limit_s + ROUTING_GRACE_S
    )
    status: AllocationStatus = "feasible" if dropped and outcome.status == "optimal" else outcome.status
    return DispatchSolution(engine=outcome.engine, status=status, trips=trips, dropped=dropped)
