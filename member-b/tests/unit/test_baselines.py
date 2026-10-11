import json
import pytest
from pathlib import Path
from jp_b.api.schemas import Weights, Scenario, PredictionSet, PlanResult
from jp_b.services.baseline_service import solve_baseline


@pytest.fixture
def base_scenario():
    scen_path = Path("backend/jp_b/clients/fixtures/scn_contract_example.json")
    with open(scen_path, "r", encoding="utf-8") as f:
        return Scenario.model_validate(json.load(f))


@pytest.fixture
def base_prediction():
    pred_path = Path("backend/jp_b/clients/fixtures/contract_prediction_example.json")
    with open(pred_path, "r", encoding="utf-8") as f:
        return PredictionSet.model_validate(json.load(f))


@pytest.fixture
def default_weights():
    return Weights(urgency=1.0, debt=1.0, vulnerability=0.5, travel=20.0)


def test_fcfs_tie_breaker(base_scenario, base_prediction, default_weights):
    """Areas with identical request_time should be sorted by area_id ascending."""
    scen_dict = base_scenario.model_dump()
    # Give ar_02 and ar_01 identical request_time
    scen_dict["areas"][0]["request_time"] = "06:00"
    scen_dict["areas"][1]["request_time"] = "06:00"
    scen_dict["areas"][2]["request_time"] = "07:00"

    result = solve_baseline(
        strategy="fcfs",
        scenario=scen_dict,
        prediction=base_prediction,
        weights=default_weights,
        debt_by_area={"ar_01": 0, "ar_02": 0, "ar_03": 0},
    )

    # ar_01 should come before ar_02 in rank because "ar_01" < "ar_02"
    ranks = {a.area_id: a.rank for a in result.allocations}
    assert ranks["ar_01"] < ranks["ar_02"]


def test_nearest_first_tie_breaker(base_scenario, base_prediction, default_weights):
    """Areas with identical distance from depot should be sorted by area_id ascending."""
    scen_dict = base_scenario.model_dump()
    depot_id = scen_dict["depot"]["depot_id"]
    # Make ar_01 and ar_02 have same distance from depot
    scen_dict["distance_km"][depot_id]["ar_01"] = 5.0
    scen_dict["distance_km"][depot_id]["ar_02"] = 5.0
    scen_dict["distance_km"]["ar_01"][depot_id] = 5.0
    scen_dict["distance_km"]["ar_02"][depot_id] = 5.0

    result = solve_baseline(
        strategy="nearest_first",
        scenario=scen_dict,
        prediction=base_prediction,
        weights=default_weights,
        debt_by_area={"ar_01": 0, "ar_02": 0, "ar_03": 0},
    )

    ranks = {a.area_id: a.rank for a in result.allocations}
    assert ranks["ar_01"] < ranks["ar_02"]


def test_route_limit_rejection(base_scenario, base_prediction, default_weights):
    """If a tanker's route limit is exceeded, it cannot serve the area."""
    scen_dict = base_scenario.model_dump()
    # Ensure abundant supply so supply is not exhausted
    scen_dict["supply"]["tanker_water_available_litres"] = 50000
    # Set tanker max_route_km low so ar_02 (10.2 km round trip) and ar_03 (23.6 km round trip) exceed limit
    for t in scen_dict["tankers"]:
        t["max_route_km"] = 8.0  # ar_01 is 6.2 km (fits), ar_02 is 10.2 km (exceeds)

    result = solve_baseline(
        strategy="nearest_first",
        scenario=scen_dict,
        prediction=base_prediction,
        weights=default_weights,
        debt_by_area={"ar_01": 0, "ar_02": 0, "ar_03": 0},
    )

    # ar_01 should be served, but ar_02 and ar_03 should be unserved with ROUTE_LIMIT
    unserved_map = {u.area_id: u.reason_code for u in result.unserved}
    assert unserved_map.get("ar_02") == "ROUTE_LIMIT"
    assert unserved_map.get("ar_03") == "ROUTE_LIMIT"


def test_partial_service_reasons(base_scenario, base_prediction, default_weights):
    """When supply runs out midway through an area, PARTIALLY_SERVED is used."""
    scen_dict = base_scenario.model_dump()
    # Set supply to 10000 L, less than ar_01 requested 20000 L
    scen_dict["supply"]["tanker_water_available_litres"] = 10000

    result = solve_baseline(
        strategy="fcfs",
        scenario=scen_dict,
        prediction=base_prediction,
        weights=default_weights,
        debt_by_area={"ar_01": 0, "ar_02": 0, "ar_03": 0},
    )

    ar01 = next(a for a in result.allocations if a.area_id == "ar_01")
    assert ar01.allocated_litres == 10000
    reason_codes = [r.code for r in ar01.reasons]
    assert "PARTIALLY_SERVED" in reason_codes
    assert "FCFS_ORDER" in reason_codes

    ar02 = next(a for a in result.allocations if a.area_id == "ar_02")
    assert ar02.allocated_litres == 0
    ar02_reasons = [r.code for r in ar02.reasons]
    assert "NOT_SERVED" in ar02_reasons
    assert "SUPPLY_EXHAUSTED" in ar02_reasons


def test_schema_validity(base_scenario, base_prediction, default_weights):
    """Ensure baseline returns valid PlanResult schema."""
    result = solve_baseline(
        strategy="fcfs",
        scenario=base_scenario,
        prediction=base_prediction,
        weights=default_weights,
        debt_by_area={"ar_01": 0, "ar_02": 8000, "ar_03": 12000},
    )
    # Validates against strict model
    assert isinstance(result, PlanResult)
    assert result.strategy == "fcfs"
    assert result.solver.engine == "baseline-fcfs"
    assert result.solver.status == "heuristic"
    assert len(result.allocations) == 3
    assert len(result.routes) == 3
