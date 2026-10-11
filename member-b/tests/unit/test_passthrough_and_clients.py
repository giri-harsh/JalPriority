import pytest
import respx
import httpx
from starlette.testclient import TestClient

from jp_b.main import app
from jp_b.config import settings
from jp_b.services.auth_service import auth_service
from jp_b.clients.a_http import AHttpClient
from jp_b.clients.a_mock import AMockClient
from jp_b.api.deps import get_a_client
from jp_b.api.errors import APIError

@pytest.fixture
def test_client():
    return TestClient(app)

@pytest.fixture
def auth_headers():
    login_res = auth_service.login("officer_01", "jal-demo-2026")
    token = login_res["token"]
    return {"Authorization": f"Bearer {token}"}

# ==================== 1. Passthrough Route Tests ====================

def test_passthrough_scenarios_requires_auth(test_client):
    resp = test_client.get("/b/v1/scenarios")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"

def test_passthrough_scenarios_success(test_client, auth_headers):
    resp = test_client.get("/b/v1/scenarios", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "scenarios" in data
    assert len(data["scenarios"]) >= 2
    assert "X-Request-ID" in resp.headers

def test_passthrough_get_scenario_success(test_client, auth_headers):
    resp = test_client.get("/b/v1/scenarios/scn_sa01_w06", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["scenario_id"] == "scn_sa01_w06"
    assert data["schema_version"] == "1.0"
    assert "X-Request-ID" in resp.headers

def test_passthrough_get_scenario_not_found(test_client, auth_headers):
    resp = test_client.get("/b/v1/scenarios/scn_non_existent", headers=auth_headers)
    assert resp.status_code == 404
    data = resp.json()
    assert data["error"]["code"] == "SCENARIO_NOT_FOUND"
    assert data["error"]["details"]["scenario_id"] == "scn_non_existent"

def test_passthrough_get_predictions_success(test_client, auth_headers):
    resp = test_client.get("/b/v1/scenarios/scn_sa01_w06/predictions", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["scenario_id"] == "scn_sa01_w06"
    assert "areas" in data
    assert len(data["areas"]) == 8

def test_passthrough_get_predictions_not_found(test_client, auth_headers):
    resp = test_client.get("/b/v1/scenarios/scn_missing/predictions", headers=auth_headers)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SCENARIO_NOT_FOUND"

# ==================== 2. Client Switching (B_USE_MOCK_A) ====================

def test_client_switching_flag():
    # When True -> AMockClient
    settings.b_use_mock_a = True
    client_mock = get_a_client()
    assert isinstance(client_mock, AMockClient)

    # When False -> AHttpClient
    settings.b_use_mock_a = False
    client_http = get_a_client()
    assert isinstance(client_http, AHttpClient)

    # Restore default
    settings.b_use_mock_a = True

# ==================== 3. AHttpClient Tests with respx ====================

@pytest.mark.anyio
@respx.mock
async def test_a_http_client_success():
    client = AHttpClient(base_url="http://mock-upstream-a:8001")

    # Mock Health
    respx.get("http://mock-upstream-a:8001/a/v1/health").respond(
        200, json={"status": "ok", "service": "jp-a", "version": "1.0.0"}
    )
    health = await client.health()
    assert health["status"] == "ok"

    # Mock List Scenarios
    respx.get("http://mock-upstream-a:8001/a/v1/scenarios?include_derived=false").respond(
        200, json={
            "scenarios": [
                {
                    "schema_version": "1.0",
                    "scenario_id": "scn_sa01_w06",
                    "parent_scenario_id": None,
                    "service_area_id": "sa_01",
                    "window_index": 6,
                    "label": "Demo",
                    "n_areas": 8,
                    "supply_litres": 20000,
                    "tankers_available": 2,
                    "alert_level": 2,
                    "is_synthetic": True
                }
            ]
        }
    )
    scenarios = await client.list_scenarios(include_derived=False)
    assert len(scenarios) == 1
    assert scenarios[0].scenario_id == "scn_sa01_w06"

    # Mock Get Scenario
    respx.get("http://mock-upstream-a:8001/a/v1/scenarios/scn_sa01_w06").respond(
        200, json=AMockClient()._scenarios["scn_sa01_w06"].model_dump(mode="json")
    )
    scn = await client.get_scenario("scn_sa01_w06")
    assert scn.scenario_id == "scn_sa01_w06"

    # Mock Predictions
    respx.get("http://mock-upstream-a:8001/a/v1/scenarios/scn_sa01_w06/predictions").respond(
        200, json=AMockClient()._contract_pred_data
    )
    preds = await client.get_predictions("scn_sa01_w06")
    assert preds.scenario_id == "scn_sa01_w06"

    # Mock Model Info
    respx.get("http://mock-upstream-a:8001/a/v1/model").respond(
        200, json=AMockClient()._model_info_data
    )
    model_info = await client.get_model_info()
    assert model_info.model_version == "xgb-sa-v1"

@pytest.mark.anyio
@respx.mock
async def test_a_http_client_service_token_used():
    client = AHttpClient(base_url="http://mock-upstream-a:8001")
    route = respx.get("http://mock-upstream-a:8001/a/v1/scenarios?include_derived=false").respond(
        200, json={"scenarios": []}
    )

    await client.list_scenarios()
    assert route.called
    auth_header = route.calls.last.request.headers.get("Authorization")
    assert auth_header is not None
    assert auth_header.startswith("Bearer ")

@pytest.mark.anyio
@respx.mock
async def test_a_http_client_404_passthrough():
    client = AHttpClient(base_url="http://mock-upstream-a:8001")
    respx.get("http://mock-upstream-a:8001/a/v1/scenarios/scn_missing").respond(
        404, json={
            "error": {
                "code": "SCENARIO_NOT_FOUND",
                "message": "Scenario scn_missing not found",
                "details": {"scenario_id": "scn_missing"},
                "request_id": "req_0001"
            }
        }
    )

    with pytest.raises(APIError) as exc_info:
        await client.get_scenario("scn_missing")

    assert exc_info.value.code == "SCENARIO_NOT_FOUND"
    assert exc_info.value.status_code == 404
    assert exc_info.value.details == {"scenario_id": "scn_missing"}

@pytest.mark.anyio
@respx.mock
async def test_a_http_client_upstream_error_mapped():
    client = AHttpClient(base_url="http://mock-upstream-a:8001")
    respx.post("http://mock-upstream-a:8001/a/v1/plans/solve").respond(
        500, json={
            "error": {
                "code": "SOLVER_ERROR",
                "message": "Solver crashed",
                "details": {},
                "request_id": "req_0002"
            }
        }
    )

    with pytest.raises(APIError) as exc_info:
        await client.solve({"scenario_id": "scn_sa01_w06", "weights": {"urgency": 1.0, "debt": 1.0, "vulnerability": 0.5, "travel": 20.0}, "debt_by_area": {}})

    assert exc_info.value.code == "UPSTREAM_A_ERROR"
    assert exc_info.value.status_code == 502
    assert exc_info.value.details["upstream"]["code"] == "SOLVER_ERROR"

@pytest.mark.anyio
@respx.mock
async def test_a_http_client_upstream_unavailable_or_timeout():
    client = AHttpClient(base_url="http://mock-upstream-a:8001")
    respx.get("http://mock-upstream-a:8001/a/v1/scenarios/scn_sa01_w06").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )

    with pytest.raises(APIError) as exc_info:
        await client.get_scenario("scn_sa01_w06")

    assert exc_info.value.code == "UPSTREAM_A_UNAVAILABLE"
    assert exc_info.value.status_code == 502

@pytest.mark.anyio
@respx.mock
async def test_a_http_client_disruption_and_solve_and_request_id():
    client = AHttpClient(base_url="http://mock-upstream-a:8001", timeout=10.0)
    assert client._base_url == "http://mock-upstream-a:8001"
    assert client._timeout == 10.0

    # Test apply_disruption
    respx.post("http://mock-upstream-a:8001/a/v1/scenarios/scn_sa01_w06/disruptions").respond(
        201, json=AMockClient()._scenarios["scn_sa01_w06"].model_dump(mode="json")
    )
    derived = await client.apply_disruption("scn_sa01_w06", {"type": "tanker_down", "params": {"tanker_id": "tk_01"}})
    assert derived.scenario_id == "scn_sa01_w06"

    # Test solve success
    respx.post("http://mock-upstream-a:8001/a/v1/plans/solve").respond(
        200, json=AMockClient()._contract_plan_result_data
    )
    res = await client.solve({"scenario_id": "scn_contract_example", "weights": {"urgency": 1.0, "debt": 1.0, "vulnerability": 0.5, "travel": 20.0}, "debt_by_area": {}})
    assert res.result_id == "res_7c1e9a40b2d3"

