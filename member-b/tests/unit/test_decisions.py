import pytest
from starlette.testclient import TestClient

from jp_b.main import app
from jp_b.services.auth_service import auth_service
from jp_b.api.schemas import PlanRecord, Weights
from jp_b.repositories.sqlite_repo import SQLiteLedgerRepository, SQLitePlansRepository, SQLiteAuditRepository, SQLiteEventsRepository


@pytest.fixture
def test_client():
    return TestClient(app)


@pytest.fixture
def auth_headers():
    login_res = auth_service.login("officer_01", "jal-demo-2026")
    return {"Authorization": f"Bearer {login_res['token']}"}


@pytest.fixture
def recommended_plan(test_client, auth_headers):
    """Helper to generate a recommended plan."""
    resp = test_client.post(
        "/b/v1/plans",
        headers=auth_headers,
        json={"scenario_id": "scn_sa01_w06", "time_limit_s": 8.0},
    )
    assert resp.status_code == 201
    return resp.json()


def test_approval_workflow_and_ledger_update(test_client, auth_headers, recommended_plan):
    """
    1. Successful approval marks status 'approved', updates ledger,
       persists audit entry 'plan_approved', and emits event 'plan_decision'.
    """
    plan_id = recommended_plan["plan_id"]
    scenario_id = recommended_plan["scenario_id"]
    service_area_id = recommended_plan["service_area_id"]
    window_index = recommended_plan["window_index"]

    # Apply approval
    resp = test_client.post(
        f"/b/v1/plans/{plan_id}/decision",
        headers=auth_headers,
        json={
            "decision": "approved",
            "note": "Dispatch confirmed for morning window.",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ledger_updated"] is True
    assert data["next_plan"] is None

    approved_plan = PlanRecord.model_validate(data["plan"])
    assert approved_plan.status == "approved"
    assert approved_plan.decision is not None
    assert approved_plan.decision.decision == "approved"
    assert approved_plan.decision.note == "Dispatch confirmed for morning window."
    assert approved_plan.decision.officer_id == "officer_01"

    # Verify persisted plan in PlansRepository
    plans_repo = SQLitePlansRepository()
    stored_plan = plans_repo.get(plan_id)
    assert stored_plan["status"] == "approved"
    assert stored_plan["decision"]["decision"] == "approved"

    # Verify persisted ledger update for this window in LedgerRepository
    ledger_repo = SQLiteLedgerRepository()
    rows = ledger_repo.get_all_for_service_area(service_area_id)
    window_rows = [r for r in rows if int(r["window_index"]) == window_index]
    assert len(window_rows) > 0
    assert all(r["source"] == "approved_plan" for r in window_rows)
    assert all(r["plan_id"] == plan_id for r in window_rows)

    # Verify audit entry persisted
    audit_repo = SQLiteAuditRepository()
    entries = audit_repo.list_entries(plan_id=plan_id)
    assert any(e["action"] == "plan_approved" for e in entries)

    # Verify event emitted
    events_repo = SQLiteEventsRepository()
    events = events_repo.list_events(scenario_id=scenario_id, name="plan_decision")
    decision_event = next((e for e in events if e.get("props", {}).get("plan_id") == plan_id), None)
    assert decision_event is not None
    assert decision_event["props"]["decision"] == "approved"


def test_edit_workflow_creates_superseding_plan(test_client, auth_headers, recommended_plan):
    """
    2. Successful edit marks original plan as 'edited', generates superseding plan,
       sets supersedes_plan_id and decision.next_plan_id, persists audit and event.
    """
    plan_id = recommended_plan["plan_id"]
    scenario_id = recommended_plan["scenario_id"]
    revised_weights = {
        "urgency": 2.0,
        "debt": 1.5,
        "vulnerability": 0.8,
        "travel": 15.0,
    }

    resp = test_client.post(
        f"/b/v1/plans/{plan_id}/decision",
        headers=auth_headers,
        json={
            "decision": "edited",
            "note": "Adjusted debt weight to prioritize backlog.",
            "revised_weights": revised_weights,
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ledger_updated"] is False
    assert data["next_plan"] is not None

    orig_plan = PlanRecord.model_validate(data["plan"])
    next_plan = PlanRecord.model_validate(data["next_plan"])

    assert orig_plan.status == "edited"
    assert orig_plan.decision.decision == "edited"
    assert orig_plan.decision.next_plan_id == next_plan.plan_id

    assert next_plan.status == "recommended"
    assert next_plan.supersedes_plan_id == orig_plan.plan_id
    assert next_plan.result.weights.urgency == 2.0
    assert next_plan.result.weights.debt == 1.5

    # Check persistence of both plans
    plans_repo = SQLitePlansRepository()
    assert plans_repo.get(orig_plan.plan_id)["status"] == "edited"
    assert plans_repo.get(next_plan.plan_id)["status"] == "recommended"

    # Check audit entry
    audit_repo = SQLiteAuditRepository()
    entries = audit_repo.list_entries(plan_id=plan_id)
    assert any(e["action"] == "plan_edited" for e in entries)

    # Check event
    events_repo = SQLiteEventsRepository()
    events = events_repo.list_events(scenario_id=scenario_id, name="plan_decision")
    decision_event = next((e for e in events if e.get("props", {}).get("plan_id") == plan_id), None)
    assert decision_event is not None
    assert decision_event["props"]["decision"] == "edited"


def test_rejection_workflow(test_client, auth_headers, recommended_plan):
    """
    3. Successful rejection marks status 'rejected', stores note, audit, and event.
    """
    plan_id = recommended_plan["plan_id"]
    scenario_id = recommended_plan["scenario_id"]

    resp = test_client.post(
        f"/b/v1/plans/{plan_id}/decision",
        headers=auth_headers,
        json={
            "decision": "rejected",
            "note": "Route infeasible due to emergency road closure.",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ledger_updated"] is False
    assert data["next_plan"] is None

    rejected_plan = PlanRecord.model_validate(data["plan"])
    assert rejected_plan.status == "rejected"
    assert rejected_plan.decision.decision == "rejected"
    assert rejected_plan.decision.note == "Route infeasible due to emergency road closure."

    # Verify audit
    audit_repo = SQLiteAuditRepository()
    entries = audit_repo.list_entries(plan_id=plan_id)
    assert any(e["action"] == "plan_rejected" for e in entries)


def test_rejection_without_note_returns_422(test_client, auth_headers, recommended_plan):
    """4. Rejection without note (or empty/whitespace) returns HTTP 422."""
    plan_id = recommended_plan["plan_id"]

    # Null note
    resp1 = test_client.post(
        f"/b/v1/plans/{plan_id}/decision",
        headers=auth_headers,
        json={"decision": "rejected", "note": None},
    )
    assert resp1.status_code == 422

    # Empty whitespace note
    resp2 = test_client.post(
        f"/b/v1/plans/{plan_id}/decision",
        headers=auth_headers,
        json={"decision": "rejected", "note": "   "},
    )
    assert resp2.status_code == 422


def test_edit_without_revised_weights_returns_422(test_client, auth_headers, recommended_plan):
    """Edit decision without revised_weights returns HTTP 422."""
    plan_id = recommended_plan["plan_id"]

    resp = test_client.post(
        f"/b/v1/plans/{plan_id}/decision",
        headers=auth_headers,
        json={"decision": "edited", "note": "Needs change", "revised_weights": None},
    )
    assert resp.status_code == 422


def test_second_decision_returns_409_plan_already_decided(test_client, auth_headers, recommended_plan):
    """5. A second decision on an already decided plan returns 409 PLAN_ALREADY_DECIDED."""
    plan_id = recommended_plan["plan_id"]

    # First decision: approve
    resp1 = test_client.post(
        f"/b/v1/plans/{plan_id}/decision",
        headers=auth_headers,
        json={"decision": "approved", "note": "First approval"},
    )
    assert resp1.status_code == 200

    # Second decision: try to reject
    resp2 = test_client.post(
        f"/b/v1/plans/{plan_id}/decision",
        headers=auth_headers,
        json={"decision": "rejected", "note": "Second attempt"},
    )
    assert resp2.status_code == 409
    assert resp2.json()["error"]["code"] == "PLAN_ALREADY_DECIDED"

    # Third decision: try to edit
    resp3 = test_client.post(
        f"/b/v1/plans/{plan_id}/decision",
        headers=auth_headers,
        json={
            "decision": "edited",
            "revised_weights": {"urgency": 1.0, "debt": 1.0, "vulnerability": 0.5, "travel": 20.0},
        },
    )
    assert resp3.status_code == 409
    assert resp3.json()["error"]["code"] == "PLAN_ALREADY_DECIDED"


def test_decision_on_nonexistent_plan_returns_404(test_client, auth_headers):
    """7. Decision on missing plan ID returns 404 PLAN_NOT_FOUND."""
    resp = test_client.post(
        "/b/v1/plans/pln_nonexistent_123456/decision",
        headers=auth_headers,
        json={"decision": "approved"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "PLAN_NOT_FOUND"
