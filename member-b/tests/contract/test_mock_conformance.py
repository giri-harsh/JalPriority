import pytest
import re
from pathlib import Path
from jp_b.clients.a_mock import AMockClient
from jp_b.api.schemas import (
    Scenario,
    ScenarioSummary,
    PredictionSet,
    ModelInfo,
    PlanResult,
    SolveRequest,
    Weights,
    Disruption
)
from jp_b.api.errors import APIError

@pytest.fixture
def mock_client():
    return AMockClient()

@pytest.mark.anyio
async def test_fixtures_loaded_and_strict_parsing(mock_client):
    # Both prescribed fixtures must be retrievable and parse strictly
    scn_contract = await mock_client.get_scenario("scn_contract_example")
    assert scn_contract.scenario_id == "scn_contract_example"
    assert scn_contract.schema_version == "1.0"
    assert len(scn_contract.areas) == 3
    assert len(scn_contract.tankers) == 3
    Scenario.model_validate(scn_contract.model_dump(), strict=True)

    scn_sa01 = await mock_client.get_scenario("scn_sa01_w06")
    assert scn_sa01.scenario_id == "scn_sa01_w06"
    assert scn_sa01.schema_version == "1.0"
    assert len(scn_sa01.areas) == 8
    assert len(scn_sa01.tankers) == 4
    assert scn_sa01.heat.alert_level == 2
    Scenario.model_validate(scn_sa01.model_dump(), strict=True)

@pytest.mark.anyio
async def test_list_scenarios_conformance(mock_client):
    scenarios = await mock_client.list_scenarios(include_derived=False)
    assert len(scenarios) >= 2
    for s in scenarios:
        assert isinstance(s, ScenarioSummary)
        ScenarioSummary.model_validate(s.model_dump(), strict=True)
        assert re.match(r"^scn_[a-z0-9_]+$", s.scenario_id)

@pytest.mark.anyio
async def test_model_info_conformance(mock_client):
    model_info = await mock_client.get_model_info()
    assert isinstance(model_info, ModelInfo)
    assert model_info.schema_version == "1.0"
    assert model_info.is_simulation is True
    ModelInfo.model_validate(model_info.model_dump(), strict=True)

@pytest.mark.anyio
async def test_predictions_conformance_and_formulas(mock_client):
    # 1. scn_contract_example returns §5.4 example verbatim
    pred_contract = await mock_client.get_predictions("scn_contract_example")
    assert pred_contract.scenario_id == "scn_contract_example"
    assert len(pred_contract.areas) == 3
    assert pred_contract.areas[0].predicted_shortfall_litres == 14000
    assert pred_contract.areas[1].predicted_shortfall_litres == 9000
    assert pred_contract.areas[2].predicted_shortfall_litres == 6000
    PredictionSet.model_validate(pred_contract.model_dump(), strict=True)

    # 2. scn_sa01_w06 returns dynamic predictions satisfying F-1
    pred_sa01 = await mock_client.get_predictions("scn_sa01_w06")
    assert pred_sa01.scenario_id == "scn_sa01_w06"
    assert len(pred_sa01.areas) == 8
    PredictionSet.model_validate(pred_sa01.model_dump(), strict=True)

    scn_sa01 = await mock_client.get_scenario("scn_sa01_w06")
    area_dict = {a.area_id: a for a in scn_sa01.areas}
    for p_area in pred_sa01.areas:
        raw_area = area_dict[p_area.area_id]
        # F-1: sf_i = max(0, predicted_need - expected_piped)
        expected_sf = max(0, p_area.predicted_need_litres - raw_area.expected_piped_litres)
        assert p_area.predicted_shortfall_litres == expected_sf
        assert len(p_area.top_features) == 3

@pytest.mark.anyio
async def test_solve_verbatim_contract_example(mock_client):
    solve_req = SolveRequest(
        scenario_id="scn_contract_example",
        weights=Weights(urgency=1.0, debt=1.0, vulnerability=0.5, travel=20.0),
        debt_by_area={"ar_01": 0, "ar_02": 8000, "ar_03": 12000},
        time_limit_s=8.0
    )
    result = await mock_client.solve(solve_req)
    assert isinstance(result, PlanResult)
    assert result.scenario_id == "scn_contract_example"
    assert result.strategy == "jalpriority"
    assert result.totals.allocated_litres == 20000
    assert result.totals.unmet_litres == 9000
    assert result.constraint_check.violations == []
    PlanResult.model_validate(result.model_dump(), strict=True)

@pytest.mark.anyio
async def test_solve_sa01_and_formulas(mock_client):
    solve_req = SolveRequest(
        scenario_id="scn_sa01_w06",
        weights=Weights(urgency=1.5, debt=1.2, vulnerability=0.8, travel=25.0),
        debt_by_area={"ar_01": 1000, "ar_02": 5000, "ar_07": 12000, "ar_08": 15000},
        time_limit_s=8.0
    )
    result = await mock_client.solve(solve_req)
    assert isinstance(result, PlanResult)
    PlanResult.model_validate(result.model_dump(), strict=True)

    # Contract Guarantees
    assert result.constraint_check.violations == []
    assert result.totals.allocated_litres <= result.totals.supply_available_litres
    assert result.totals.allocated_litres <= result.totals.tanker_capacity_available_litres

    # Check F-6 unmet litres and F-7 coverage
    for alloc in result.allocations:
        expected_unmet = max(0, alloc.predicted_shortfall_litres - alloc.allocated_litres)
        assert alloc.unmet_litres == expected_unmet
        if alloc.predicted_shortfall_litres == 0:
            assert alloc.coverage_ratio == 1.0
        else:
            assert alloc.coverage_ratio == round(min(1.0, alloc.allocated_litres / alloc.predicted_shortfall_litres), 4)

    # Check F-8 route distances
    scn = await mock_client.get_scenario("scn_sa01_w06")
    for r in result.routes:
        if r.status == "unavailable" or r.status == "idle":
            assert r.route_km == 0.0
            assert r.stops == []
        else:
            expected_km = 0.0
            for i in range(len(r.path) - 1):
                expected_km += scn.distance_km[r.path[i]][r.path[i+1]]
            assert r.route_km == round(expected_km, 2)
            assert r.route_km <= r.max_route_km + 0.01

@pytest.mark.anyio
async def test_disruption_tanker_down(mock_client):
    # 1. Successful application
    disruption = {"type": "tanker_down", "params": {"tanker_id": "tk_01"}}
    derived = await mock_client.apply_disruption("scn_sa01_w06", disruption)
    assert derived.parent_scenario_id == "scn_sa01_w06"
    assert re.match(r"^scn_sa01_w06__d1_[0-9a-f]{6}$", derived.scenario_id)
    target = next(t for t in derived.tankers if t.tanker_id == "tk_01")
    assert target.status == "unavailable"
    Scenario.model_validate(derived.model_dump(), strict=True)

    # 2. Idempotent check
    derived2 = await mock_client.apply_disruption("scn_sa01_w06", disruption)
    assert derived2.scenario_id == derived.scenario_id

    # 3. Retrieve derived scenario with get_scenario and list_scenarios(include_derived=True)
    retrieved = await mock_client.get_scenario(derived.scenario_id)
    assert retrieved.scenario_id == derived.scenario_id

    summaries = await mock_client.list_scenarios(include_derived=True)
    assert any(s.scenario_id == derived.scenario_id for s in summaries)

    # 4. Error: already unavailable -> 409 DISRUPTION_NOT_APPLICABLE
    with pytest.raises(APIError) as exc_info:
        await mock_client.apply_disruption(derived.scenario_id, {"type": "tanker_down", "params": {"tanker_id": "tk_01"}})
    assert exc_info.value.code == "DISRUPTION_NOT_APPLICABLE"
    assert exc_info.value.status_code == 409

    # 5. Error: unknown tanker_id -> 422 DISRUPTION_INVALID
    with pytest.raises(APIError) as exc_info:
        await mock_client.apply_disruption("scn_sa01_w06", {"type": "tanker_down", "params": {"tanker_id": "tk_99"}})
    assert exc_info.value.code == "DISRUPTION_INVALID"
    assert exc_info.value.status_code == 422

@pytest.mark.anyio
async def test_disruption_supply_cut(mock_client):
    # S = 20,000, fraction = 0.3 -> S' = floor(20000 * 0.7 / 100) * 100 = 14,000
    disruption = {"type": "supply_cut", "params": {"fraction": 0.3}}
    derived = await mock_client.apply_disruption("scn_sa01_w06", disruption)
    assert derived.supply.tanker_water_available_litres == 14000
    assert re.match(r"^scn_sa01_w06__d1_[0-9a-f]{6}$", derived.scenario_id)
    Scenario.model_validate(derived.model_dump(), strict=True)

    # Invalid fractions (< 0.05 or > 0.9)
    for bad_f in [0.01, 0.95, -0.1, "bad"]:
        with pytest.raises(APIError) as exc_info:
            await mock_client.apply_disruption("scn_sa01_w06", {"type": "supply_cut", "params": {"fraction": bad_f}})
        assert exc_info.value.code == "DISRUPTION_INVALID"
        assert exc_info.value.status_code == 422

@pytest.mark.anyio
async def test_disruption_heat_alert(mock_client):
    # parent alert_level = 2, max_temp_c = 44.5, heat_index_c = 49.0
    # new level = 3 -> delta = 1 -> max_temp = 44.5 + 1.5 = 46.0, heat_index = 49.0 + 2.5 = 51.5
    disruption = {"type": "heat_alert", "params": {"alert_level": 3}}
    derived = await mock_client.apply_disruption("scn_sa01_w06", disruption)
    assert derived.heat.alert_level == 3
    assert derived.heat.max_temp_c == 46.0
    assert derived.heat.heat_index_c == 51.5
    Scenario.model_validate(derived.model_dump(), strict=True)

    # Level unchanged -> 409 DISRUPTION_NOT_APPLICABLE
    with pytest.raises(APIError) as exc_info:
        await mock_client.apply_disruption("scn_sa01_w06", {"type": "heat_alert", "params": {"alert_level": 2}})
    assert exc_info.value.code == "DISRUPTION_NOT_APPLICABLE"
    assert exc_info.value.status_code == 409

    # Level out of range -> 422 DISRUPTION_INVALID
    for bad_lvl in [-1, 4, 2.5, "high"]:
        with pytest.raises(APIError) as exc_info:
            await mock_client.apply_disruption("scn_sa01_w06", {"type": "heat_alert", "params": {"alert_level": bad_lvl}})
        assert exc_info.value.code == "DISRUPTION_INVALID"
        assert exc_info.value.status_code == 422

@pytest.mark.anyio
async def test_disruption_unknown_and_missing_scenario(mock_client):
    # Unknown disruption type -> 422
    with pytest.raises(APIError) as exc_info:
        await mock_client.apply_disruption("scn_sa01_w06", {"type": "earthquake", "params": {}})
    assert exc_info.value.code == "DISRUPTION_INVALID"
    assert exc_info.value.status_code == 422

    # Unknown scenario -> 404 SCENARIO_NOT_FOUND
    with pytest.raises(APIError) as exc_info:
        await mock_client.apply_disruption("scn_unknown", {"type": "tanker_down", "params": {"tanker_id": "tk_01"}})
    assert exc_info.value.code == "SCENARIO_NOT_FOUND"
    assert exc_info.value.status_code == 404
