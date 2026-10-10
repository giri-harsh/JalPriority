"""M7: stop sequencing (exact permutation TSP, OR-Tools for long trips, higher-priority-first tie-break)."""
from __future__ import annotations

import itertools
import random

import pytest

from jp_a.optimization import routing
from jp_a.optimization.routing import build_path, sequence_stops, tour_length_km

from .opt_helpers import golden_inputs, road_matrix


def _random_matrix(seed: int, n: int) -> tuple[dict, list[str]]:
    rng = random.Random(seed)
    nodes = {"dp_01": (28.6692, 77.4538)}
    for i in range(1, n + 1):
        nodes[f"ar_{i:02d}"] = (28.6692 + rng.uniform(-0.05, 0.05), 77.4538 + rng.uniform(-0.05, 0.05))
    return road_matrix(nodes), [f"ar_{i:02d}" for i in range(1, n + 1)]


def _held_karp(depot: str, stops: list[str], d: dict) -> float:
    """Exact TSP length by dynamic programming (independent of the code under test)."""
    n = len(stops)
    best = {(1 << i, i): d[depot][stops[i]] for i in range(n)}
    for size in range(2, n + 1):
        for subset in itertools.combinations(range(n), size):
            mask = sum(1 << i for i in subset)
            for j in subset:
                prev = mask & ~(1 << j)
                best[(mask, j)] = min(best[(prev, k)] + d[stops[k]][stops[j]] for k in subset if k != j)
    full = (1 << n) - 1
    return min(best[(full, j)] + d[stops[j]][depot] for j in range(n))


def test_golden_tie_break_visits_higher_priority_first():
    scenario, *_ = golden_inputs()
    d = scenario.distance_km
    rank = {"ar_03": 1, "ar_02": 2, "ar_01": 3}
    order = sequence_stops("dp_01", ["ar_01", "ar_03"], d, rank)
    assert order == ["ar_03", "ar_01"]
    assert tour_length_km("dp_01", order, d) == pytest.approx(tour_length_km("dp_01", order[::-1], d))
    # With the priorities swapped the other (equally long) direction wins.
    assert sequence_stops("dp_01", ["ar_01", "ar_03"], d, {"ar_01": 1, "ar_03": 2}) == ["ar_01", "ar_03"]


def test_single_and_empty_trips():
    scenario, *_ = golden_inputs()
    assert sequence_stops("dp_01", ["ar_02"], scenario.distance_km, {"ar_02": 1}) == ["ar_02"]
    assert sequence_stops("dp_01", [], scenario.distance_km, {}) == []
    assert build_path("dp_01", []) == []
    assert build_path("dp_01", ["ar_02"]) == ["dp_01", "ar_02", "dp_01"]


@pytest.mark.parametrize("seed,n", [(1, 3), (2, 5), (3, 6), (4, 7)])
def test_exact_tour_is_optimal(seed, n):
    d, stops = _random_matrix(seed, n)
    rank = {a: i + 1 for i, a in enumerate(stops)}
    order = sequence_stops("dp_01", stops, d, rank)
    assert sorted(order) == stops
    assert tour_length_km("dp_01", order, d) == pytest.approx(_held_karp("dp_01", stops, d), abs=1e-9)
    # Of the optimal tour and its (equally long) reverse, the higher-priority end comes first.
    assert rank[order[0]] < rank[order[-1]]


def test_long_trip_uses_or_tools_and_finds_the_optimum(monkeypatch):
    d, stops = _random_matrix(7, 9)
    rank = {a: i + 1 for i, a in enumerate(stops)}
    calls = []
    original = routing._ortools_tour

    def spy(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(routing, "_ortools_tour", spy)
    order = sequence_stops("dp_01", stops, d, rank, time_limit_s=0.3)
    assert calls, "trips longer than 7 stops must go through the OR-Tools RoutingModel"
    assert sorted(order) == stops
    assert tour_length_km("dp_01", order, d) == pytest.approx(_held_karp("dp_01", stops, d), abs=1e-6)
    assert rank[order[0]] < rank[order[-1]]


def test_long_trip_falls_back_to_nearest_neighbour(monkeypatch):
    d, stops = _random_matrix(8, 9)
    monkeypatch.setattr(routing, "_ortools_tour", lambda *a, **k: None)
    rank = {a: i + 1 for i, a in enumerate(stops)}
    order = sequence_stops("dp_01", stops, d, rank)
    assert sorted(order) == stops


def test_exact_tour_is_deterministic():
    d, stops = _random_matrix(5, 7)
    rank = {a: i + 1 for i, a in enumerate(reversed(stops))}
    assert len({tuple(sequence_stops("dp_01", stops, d, rank)) for _ in range(5)}) == 1
