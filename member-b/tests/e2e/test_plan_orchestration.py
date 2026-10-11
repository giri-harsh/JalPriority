import pytest
from starlette.testclient import TestClient

from jp_b.main import app
from jp_b.services.auth_service import auth_service
from jp_b.api.schemas import PlanRecord, Event, AuditEntry


@pytest.fixture
def test_client():
    return TestClient(app)


@pytest.fixture
def auth_headers():
    login_res = auth_service.login("officer_01", "jal-demo-2026")
    return {"Authorization": f"Bearer {login_res['token']}"}


def test_plan_generation_and_retrieval_e2e(test_client, auth_headers):
    """
    M7 Acceptance Test:
    Generate a plan via POST /b/v1/plans, retrieve it, list it,
    and verify persisted events and audit entries.
    """
    scenario_id = "scn_sa01_w06"
    weights_payload = {
        "urgency": 1.2,
        "debt": 1.0,
        "vulnerability": 0.5,
        "travel": 20.0,
    }

    # 1. Generate plan
    create_resp = test_client.post(
        "/b/v1/plans",
        headers=auth_headers,
        json={
            "scenario_id": scenario_id,
            "weights": weights_payload,
            "time_limit_s": 8.0,
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    created_data = create_resp.json()

    # Validate against strict schema
    plan_record = PlanRecord.model_validate(created_data)
    plan_id = plan_record.plan_id
    assert plan_record.status == "recommended"
    assert plan_record.scenario_id == scenario_id
    assert plan_record.created_by == "officer_01"
    assert plan_record.result.strategy == "jalpriority"
    assert plan_record.metrics.total_unmet_litres is not None

    # 2. Retrieve plan by ID
    get_resp = test_client.get(f"/b/v1/plans/{plan_id}", headers=auth_headers)
    assert get_resp.status_code == 200
    retrieved_plan = PlanRecord.model_validate(get_resp.json())
    assert retrieved_plan.plan_id == plan_id
    assert retrieved_plan.metrics == plan_record.metrics

    # 3. Retrieve non-existent plan returns 404 PLAN_NOT_FOUND
    missing_resp = test_client.get("/b/v1/plans/pln_missing_123456", headers=auth_headers)
    assert missing_resp.status_code == 404
    assert missing_resp.json()["error"]["code"] == "PLAN_NOT_FOUND"

    # 4. List plans with filtering
    list_resp = test_client.get(f"/b/v1/plans?scenario_id={scenario_id}", headers=auth_headers)
    assert list_resp.status_code == 200
    list_data = list_resp.json()
    assert "plans" in list_data
    assert any(p["plan_id"] == plan_id for p in list_data["plans"])

    # 5. List plans with latest=true
    latest_resp = test_client.get(f"/b/v1/plans?scenario_id={scenario_id}&latest=true", headers=auth_headers)
    assert latest_resp.status_code == 200
    latest_data = latest_resp.json()
    assert len(latest_data["plans"]) <= 1
    if latest_data["plans"]:
        assert latest_data["plans"][0]["plan_id"] == plan_id

    # 6. Verify persisted audit entries
    audit_resp = test_client.get(f"/b/v1/audit?plan_id={plan_id}", headers=auth_headers)
    assert audit_resp.status_code == 200
    audit_data = audit_resp.json()
    assert "entries" in audit_data
    audit_actions = [e["action"] for e in audit_data["entries"]]
    assert "plan_generated" in audit_actions

    # 7. Verify persisted events
    events_resp = test_client.get(f"/b/v1/events?scenario_id={scenario_id}", headers=auth_headers)
    assert events_resp.status_code == 200
    events_data = events_resp.json()
    assert "events" in events_data
    event_names = [e["name"] for e in events_data["events"]]
    assert "prediction_generated" in event_names
    assert "plan_generated" in event_names


def test_weights_and_events_api(test_client, auth_headers):
    """Test weights GET/PUT and events POST/GET APIs."""
    service_area_id = "sa_01"

    # 1. Get weights
    get_w_resp = test_client.get(f"/b/v1/weights?service_area_id={service_area_id}", headers=auth_headers)
    assert get_w_resp.status_code == 200
    w_data = get_w_resp.json()
    assert "weights" in w_data
    assert "flags" in w_data
    assert "defaults" in w_data
    assert "debt_policy" in w_data

    # 2. Update weights
    put_w_resp = test_client.put(
        "/b/v1/weights",
        headers=auth_headers,
        json={
            "service_area_id": service_area_id,
            "weights": {"urgency": 1.5, "debt": 1.2, "vulnerability": 0.8, "travel": 25.0},
        },
    )
    assert put_w_resp.status_code == 200
    updated_w = put_w_resp.json()
    assert updated_w["weights"]["urgency"] == 1.5

    # 3. Track event (valid name)
    track_resp = test_client.post(
        "/b/v1/events",
        headers=auth_headers,
        json={
            "name": "comparison_viewed",
            "scenario_id": "scn_sa01_w06",
            "props": {"metrics_by_strategy": {}},
        },
    )
    assert track_resp.status_code == 202
    assert "event_id" in track_resp.json()
    assert track_resp.json()["event_id"].startswith("evt_")

    # 4. Track event (invalid name returns 422)
    bad_track_resp = test_client.post(
        "/b/v1/events",
        headers=auth_headers,
        json={
            "name": "invalid_event_name",
            "scenario_id": "scn_sa01_w06",
            "props": {},
        },
    )
    assert bad_track_resp.status_code == 422
    assert bad_track_resp.json()["error"]["code"] == "EVENT_INVALID"


def test_scenario_not_found_on_plan_generation(test_client, auth_headers):
    """Attempting to generate a plan for a nonexistent scenario returns 404."""
    resp = test_client.post(
        "/b/v1/plans",
        headers=auth_headers,
        json={
            "scenario_id": "scn_does_not_exist",
            "weights": {"urgency": 1.0, "debt": 1.0, "vulnerability": 0.5, "travel": 20.0},
        },
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SCENARIO_NOT_FOUND"
