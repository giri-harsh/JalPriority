"""Stop sequencing for one tanker trip (MEMBER_A F4).

Every trip starts and ends at the depot. Up to ``EXACT_MAX_STOPS`` stops the shortest tour is found exactly by
enumerating permutations; longer trips use the OR-Tools ``RoutingModel`` (PATH_CHEAPEST_ARC first solution,
GUIDED_LOCAL_SEARCH, 0.3 s per tanker), with a nearest-neighbour tour as a last resort.

Tie-break: tours whose lengths differ by at most ``TOUR_TOLERANCE_KM`` are equal, and the one that visits the
higher-priority area first wins (lexicographically smallest sequence of ranks). With a symmetric matrix every
tour has an equally long reverse, so this rule also fixes the direction of travel.
"""
from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from ..domain.constraints import distance, km_to_metres_up, path_length_km

EXACT_MAX_STOPS = 7
ORTOOLS_TIME_LIMIT_S = 0.3
TOUR_TOLERANCE_KM = 1e-6

DistanceMatrix = Mapping[str, Mapping[str, float]]


def build_path(depot_id: str, stops: Sequence[str]) -> list[str]:
    """Route path: depot, stops in order, depot (``[]`` when there are no stops)."""
    return [depot_id, *stops, depot_id] if stops else []


def tour_length_km(depot_id: str, stops: Sequence[str], distance_km: DistanceMatrix) -> float:
    """Unrounded length of the closed tour through ``stops``."""
    return path_length_km(build_path(depot_id, stops), distance_km)


def sequence_stops(
    depot_id: str,
    stops: Sequence[str],
    distance_km: DistanceMatrix,
    rank: Mapping[str, int],
    *,
    time_limit_s: float = ORTOOLS_TIME_LIMIT_S,
) -> list[str]:
    """Visiting order for ``stops`` (area ids). ``rank`` maps area id -> rank (1 = highest priority)."""
    ordered = sorted(stops, key=lambda a: (rank[a], a))
    if len(ordered) <= 1:
        return ordered
    if len(ordered) <= EXACT_MAX_STOPS:
        return _exact_tour(depot_id, ordered, distance_km)
    tour = _ortools_tour(depot_id, ordered, distance_km, time_limit_s) or _nearest_neighbour_tour(
        depot_id, ordered, distance_km
    )
    return _preferred(depot_id, [tour, tour[::-1]], distance_km, rank)


def _exact_tour(depot_id: str, ordered: Sequence[str], distance_km: DistanceMatrix) -> list[str]:
    """Shortest tour by enumeration. ``ordered`` is in rank order, so permutations arrive in lexicographic rank
    order and keeping the first of equally long tours applies the higher-priority-first tie-break."""
    best: tuple[str, ...] = tuple(ordered)
    best_len = tour_length_km(depot_id, best, distance_km)
    for perm in itertools.permutations(ordered):
        length = tour_length_km(depot_id, perm, distance_km)
        if length < best_len - TOUR_TOLERANCE_KM:
            best, best_len = perm, length
    return list(best)


def _preferred(
    depot_id: str, tours: Sequence[Sequence[str]], distance_km: DistanceMatrix, rank: Mapping[str, int]
) -> list[str]:
    """Shortest of ``tours`` (non-empty); near-equal lengths are decided by the higher-priority-first rule."""
    best = tours[0]
    best_len = tour_length_km(depot_id, best, distance_km)
    for tour in tours[1:]:
        length = tour_length_km(depot_id, tour, distance_km)
        if length < best_len - TOUR_TOLERANCE_KM:
            best, best_len = tour, length
        elif abs(length - best_len) <= TOUR_TOLERANCE_KM and [rank[a] for a in tour] < [rank[a] for a in best]:
            best = tour
    return list(best)


def _ortools_tour(
    depot_id: str, ordered: Sequence[str], distance_km: DistanceMatrix, time_limit_s: float
) -> list[str] | None:
    nodes = [depot_id, *ordered]
    matrix = [[km_to_metres_up(distance(distance_km, a, b)) for b in nodes] for a in nodes]
    manager = pywrapcp.RoutingIndexManager(len(nodes), 1, 0)
    routing = pywrapcp.RoutingModel(manager)

    def arc_cost(from_index: int, to_index: int) -> int:
        return matrix[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)]

    routing.SetArcCostEvaluatorOfAllVehicles(routing.RegisterTransitCallback(arc_cost))
    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromMilliseconds(max(1, int(time_limit_s * 1000)))
    solution = routing.SolveWithParameters(params)
    if solution is None:
        return None
    tour = []
    index = solution.Value(routing.NextVar(routing.Start(0)))
    while not routing.IsEnd(index):
        tour.append(nodes[manager.IndexToNode(index)])
        index = solution.Value(routing.NextVar(index))
    return tour if sorted(tour) == sorted(ordered) else None


def _nearest_neighbour_tour(depot_id: str, ordered: Sequence[str], distance_km: DistanceMatrix) -> list[str]:
    remaining = list(ordered)
    tour: list[str] = []
    current = depot_id
    while remaining:
        nxt = min(remaining, key=lambda a: distance(distance_km, current, a))  # ties: earlier (higher) rank
        tour.append(nxt)
        remaining.remove(nxt)
        current = nxt
    return tour
