import pytest
from starlette.testclient import TestClient
from jp_b.main import app

@pytest.fixture
def test_client():
    return TestClient(app)

def test_full_dashboard_shell_backend_flow(test_client):
    """
    Test the exact end-to-end API sequence executed by the Dashboard Shell:
    1. Login via POST /b/v1/auth/login
    2. Me via GET /b/v1/auth/me
    3. List scenarios via GET /b/v1/scenarios
    4. Track scenario_loaded event via POST /b/v1/events
    5. Query latest plan via GET /b/v1/plans?scenario_id=...&latest=true
    6. Generate plan via POST /b/v1/plans
    7. Track explanation_viewed event via POST /b/v1/events
    8. ApprovalBar: Approve plan via POST /b/v1/plans/{id}/decision
    9. Edit plan with revised weights
    10. Rejection requires note
    11. 401 unauthorized handling
    """
    # 1. Login
    login_resp = test_client.post(
        "/b/v1/auth/login",
        json={"username": "officer_01", "password": "jal-demo-2026"},
    )
    assert login_resp.status_code == 200
    login_data = login_resp.json()
    token = login_data["token"]
    officer_id = login_data["officer_id"]
    role = login_data["role"]
    assert token is not None
    assert officer_id == "officer_01"
    assert role in ["dispatcher", "admin"]

    headers = {"Authorization": f"Bearer {token}"}

    # 2. Get /me
    me_resp = test_client.get("/b/v1/auth/me", headers=headers)
    assert me_resp.status_code == 200
    assert me_resp.json()["officer_id"] == officer_id

    # 3. List scenarios (passthrough used by SnapshotScreen mock)
    scenarios_resp = test_client.get("/b/v1/scenarios", headers=headers)
    assert scenarios_resp.status_code == 200
    scenarios = scenarios_resp.json()["scenarios"]
    assert len(scenarios) > 0
    scenario_id = scenarios[0]["scenario_id"]

    # 4. Track scenario_loaded event (onTrack from SnapshotScreen mock)
    event_resp = test_client.post(
        "/b/v1/events",
        headers=headers,
        json={
            "name": "scenario_loaded",
            "scenario_id": scenario_id,
            "props": {
                "n_areas": scenarios[0]["n_areas"],
                "supply_litres": scenarios[0]["supply_litres"],
                "tankers_available": scenarios[0]["tankers_available"],
            },
        },
    )
    assert event_resp.status_code == 202
    assert "event_id" in event_resp.json()

    # 5. Query latest plan before generation
    latest_resp = test_client.get(
        f"/b/v1/plans?scenario_id={scenario_id}&latest=true",
        headers=headers,
    )
    assert latest_resp.status_code == 200

    # 6. Generate plan (onGeneratePlan)
    weights = {"urgency": 1.0, "debt": 1.0, "vulnerability": 0.5, "travel": 20.0}
    gen_resp = test_client.post(
        "/b/v1/plans",
        headers=headers,
        json={"scenario_id": scenario_id, "weights": weights},
    )
    assert gen_resp.status_code == 201
    plan = gen_resp.json()
    plan_id = plan["plan_id"]
    assert plan["status"] == "recommended"

    # 7. Track explanation_viewed event
    exp_event_resp = test_client.post(
        "/b/v1/events",
        headers=headers,
        json={
            "name": "explanation_viewed",
            "scenario_id": scenario_id,
            "props": {
                "plan_id": plan_id,
                "area_id": plan["result"]["allocations"][0]["area_id"],
            },
        },
    )
    assert exp_event_resp.status_code == 202

    # 8. ApprovalBar: Approve plan
    approve_resp = test_client.post(
        f"/b/v1/plans/{plan_id}/decision",
        headers=headers,
        json={"decision": "approved", "note": "Approved by dispatcher"},
    )
    assert approve_resp.status_code == 200
    approve_data = approve_resp.json()
    assert approve_data["plan"]["status"] == "approved"
    assert approve_data["ledger_updated"] is True

    # 9. Edit workflow with new plan
    gen2_resp = test_client.post(
        "/b/v1/plans",
        headers=headers,
        json={"scenario_id": scenario_id, "weights": weights},
    )
    plan2_id = gen2_resp.json()["plan_id"]
    revised_weights = {"urgency": 1.5, "debt": 1.2, "vulnerability": 0.8, "travel": 25.0}

    edit_resp = test_client.post(
        f"/b/v1/plans/{plan2_id}/decision",
        headers=headers,
        json={"decision": "edited", "revised_weights": revised_weights},
    )
    assert edit_resp.status_code == 200
    edit_data = edit_resp.json()
    assert edit_data["plan"]["status"] == "edited"
    assert edit_data["next_plan"] is not None
    assert edit_data["next_plan"]["status"] == "recommended"

    # 10. Rejection requires note
    gen3_resp = test_client.post(
        "/b/v1/plans",
        headers=headers,
        json={"scenario_id": scenario_id},
    )
    plan3_id = gen3_resp.json()["plan_id"]

    reject_fail = test_client.post(
        f"/b/v1/plans/{plan3_id}/decision",
        headers=headers,
        json={"decision": "rejected", "note": None},
    )
    assert reject_fail.status_code == 422

    reject_ok = test_client.post(
        f"/b/v1/plans/{plan3_id}/decision",
        headers=headers,
        json={"decision": "rejected", "note": "Severe traffic gridlock in district"},
    )
    assert reject_ok.status_code == 200
    assert reject_ok.json()["plan"]["status"] == "rejected"

    # 11. 401 unauthorized test
    unauth_resp = test_client.get(
        "/b/v1/plans",
        headers={"Authorization": "Bearer bad-token"},
    )
    assert unauth_resp.status_code == 401
