import pytest
import time
from starlette.testclient import TestClient

from jp_b.main import app
from jp_b.config import settings
from jp_b.services.auth_service import auth_service
from jp_b.api.schemas import ReplanResponse, PlanRecord
from jp_b.repositories.sqlite_repo import (
    SQLiteEventsRepository,
    SQLiteAuditRepository,
    SQLitePlansRepository,
    SQLiteLedgerRepository,
)


@pytest.fixture
def test_client():
    return TestClient(app)


@pytest.fixture
def auth_headers():
    login_res = auth_service.login("officer_01", "jal-demo-2026")
    return {"Authorization": f"Bearer {login_res['token']}"}


def test_replan_tanker_down_e2e(test_client, auth_headers):
    """
    Test tanker_down disruption replan end-to-end:
    - Creates initial plan.
    - Applies disruption 'tanker_down' for tk_01.
    - Validates response schema, status transition (recommended -> superseded),
      new plan fields, diff summary, audit entry, and events.
    - Checks performance is well under 5 seconds.
    """
    scenario_id = "scn_sa01_w06"
    weights = {"urgency": 1.2, "debt": 1.0, "vulnerability": 0.5, "travel": 20.0}

    # 1. Generate base plan
    create_resp = test_client.post(
        "/b/v1/plans",
        headers=auth_headers,
        json={"scenario_id": scenario_id, "weights": weights},
    )
    assert create_resp.status_code == 201
    prev_plan_data = create_resp.json()
    prev_plan_id = prev_plan_data["plan_id"]
    assert prev_plan_data["status"] == "recommended"

    # 2. Trigger replan
    t0 = time.perf_counter()
    replan_resp = test_client.post(
        "/b/v1/replans",
        headers=auth_headers,
        json={
            "plan_id": prev_plan_id,
            "disruption": {
                "type": "tanker_down",
                "params": {"tanker_id": "tk_01"},
            },
        },
    )
    wall_elapsed_s = time.perf_counter() - t0
    assert wall_elapsed_s < 5.0, f"Replanning exceeded 5 seconds: {wall_elapsed_s:.2f}s"

    assert replan_resp.status_code == 201, replan_resp.text
    data = replan_resp.json()

    # Validate against strict ReplanResponse schema
    replan_obj = ReplanResponse.model_validate(data)
    assert replan_obj.previous_plan_id == prev_plan_id
    assert "__d1_" in replan_obj.derived_scenario_id
    assert replan_obj.disruption.type == "tanker_down"
    assert replan_obj.replanning_time_ms > 0
    assert replan_obj.replanning_time_ms < 5000

    # Validate new plan properties
    new_plan = replan_obj.new_plan
    assert new_plan.replan_of_plan_id == prev_plan_id
    assert new_plan.disruption.type == "tanker_down"
    assert new_plan.status == "recommended"
    assert new_plan.metrics.replanning_time_ms == replan_obj.replanning_time_ms
    assert new_plan.result.weights.urgency == weights["urgency"]
    assert new_plan.result.weights.debt == weights["debt"]

    # Validate previous plan status transition
    plans_repo = SQLitePlansRepository()
    prev_plan_updated = PlanRecord.model_validate(plans_repo.get(prev_plan_id))
    assert prev_plan_updated.status == "superseded"

    # Validate diff
    diff = replan_obj.diff
    assert len(diff.areas) > 0
    assert diff.summary.areas_changed >= 0
    assert diff.summary.litres_moved >= 0
    assert diff.summary.metrics_before.total_unmet_litres is not None
    assert diff.summary.metrics_after.total_unmet_litres is not None
    assert diff.summary.metrics_after.replanning_time_ms == replan_obj.replanning_time_ms

    # Validate retrievability of new plan via GET /b/v1/plans/{plan_id}
    get_plan_resp = test_client.get(
        f"/b/v1/plans/{new_plan.plan_id}",
        headers=auth_headers,
    )
    assert get_plan_resp.status_code == 200
    assert get_plan_resp.json()["plan_id"] == new_plan.plan_id

    # Validate events persisted
    events_repo = SQLiteEventsRepository()
    disruption_events = events_repo.list_events(
        scenario_id=replan_obj.derived_scenario_id,
        name="disruption_applied",
    )
    assert len(disruption_events) >= 1
    assert disruption_events[0]["props"]["magnitude"] == 1
    assert disruption_events[0]["props"]["type"] == "tanker_down"

    replan_events = events_repo.list_events(
        scenario_id=replan_obj.derived_scenario_id,
        name="replan_completed",
    )
    assert len(replan_events) >= 1
    assert replan_events[0]["props"]["previous_plan_id"] == prev_plan_id
    assert replan_events[0]["props"]["new_plan_id"] == new_plan.plan_id
    assert "diff_summary" in replan_events[0]["props"]

    # Validate audit entry
    audit_repo = SQLiteAuditRepository()
    audit_entries = audit_repo.list_entries(plan_id=new_plan.plan_id)
    replan_audit = next((a for a in audit_entries if a["action"] == "replan_applied"), None)
    assert replan_audit is not None
    assert replan_audit["details"]["previous_plan_id"] == prev_plan_id


def test_replan_supply_cut(test_client, auth_headers):
    """
    Test supply_cut disruption:
    - Applies disruption with fraction 0.25.
    - Validates magnitude is 0.25 in disruption_applied event.
    """
    scenario_id = "scn_sa01_w06"
    create_resp = test_client.post(
        "/b/v1/plans",
        headers=auth_headers,
        json={"scenario_id": scenario_id},
    )
    prev_plan_id = create_resp.json()["plan_id"]

    resp = test_client.post(
        "/b/v1/replans",
        headers=auth_headers,
        json={
            "plan_id": prev_plan_id,
            "disruption": {
                "type": "supply_cut",
                "params": {"fraction": 0.25},
            },
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["disruption"]["type"] == "supply_cut"

    # Check event magnitude
    events_repo = SQLiteEventsRepository()
    disruption_events = events_repo.list_events(
        scenario_id=data["derived_scenario_id"],
        name="disruption_applied",
    )
    assert len(disruption_events) >= 1
    assert disruption_events[0]["props"]["magnitude"] == 0.25


def test_replan_heat_alert(test_client, auth_headers):
    """
    Test heat_alert disruption:
    - Applies disruption with alert_level 3 (scenario default is 2, so delta != 0).
    - Validates magnitude is 3 in disruption_applied event.
    """
    scenario_id = "scn_sa01_w06"
    create_resp = test_client.post(
        "/b/v1/plans",
        headers=auth_headers,
        json={"scenario_id": scenario_id},
    )
    prev_plan_id = create_resp.json()["plan_id"]

    resp = test_client.post(
        "/b/v1/replans",
        headers=auth_headers,
        json={
            "plan_id": prev_plan_id,
            "disruption": {
                "type": "heat_alert",
                "params": {"alert_level": 3},
            },
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["disruption"]["type"] == "heat_alert"

    events_repo = SQLiteEventsRepository()
    disruption_events = events_repo.list_events(
        scenario_id=data["derived_scenario_id"],
        name="disruption_applied",
    )
    assert len(disruption_events) >= 1
    assert disruption_events[0]["props"]["magnitude"] == 3


def test_replan_approved_plan_remains_approved(test_client, auth_headers):
    """
    CONTRACT & MEMBER_B.md rule:
    If previous plan was 'approved', it stays 'approved'.
    Also verify replan generation does not alter the debt ledger.
    """
    scenario_id = "scn_sa01_w06"
    create_resp = test_client.post(
        "/b/v1/plans",
        headers=auth_headers,
        json={"scenario_id": scenario_id},
    )
    prev_plan_id = create_resp.json()["plan_id"]

    # Approve the plan
    dec_resp = test_client.post(
        f"/b/v1/plans/{prev_plan_id}/decision",
        headers=auth_headers,
        json={"decision": "approved", "note": "Approved for dispatch"},
    )
    assert dec_resp.status_code == 200

    plans_repo = SQLitePlansRepository()
    approved_plan = PlanRecord.model_validate(plans_repo.get(prev_plan_id))
    assert approved_plan.status == "approved"

    ledger_repo = SQLiteLedgerRepository()
    ledger_entries_before = [e for e in ledger_repo.get_all_for_service_area("sa_01") if e["window_index"] == 6]
    assert len(ledger_entries_before) > 0

    # Replan from approved plan
    replan_resp = test_client.post(
        "/b/v1/replans",
        headers=auth_headers,
        json={
            "plan_id": prev_plan_id,
            "disruption": {
                "type": "tanker_down",
                "params": {"tanker_id": "tk_02"},
            },
        },
    )
    assert replan_resp.status_code == 201

    # Verify previous plan is still 'approved'
    approved_plan_after = PlanRecord.model_validate(plans_repo.get(prev_plan_id))
    assert approved_plan_after.status == "approved"

    # Verify ledger was NOT modified by the replan (still matches previous approval)
    ledger_entries_after = [e for e in ledger_repo.get_all_for_service_area("sa_01") if e["window_index"] == 6]
    assert len(ledger_entries_after) == len(ledger_entries_before)


def test_replan_feature_flag_disabled(test_client, auth_headers, monkeypatch):
    """
    Test B_FF_REPLAN=false returns 403 FEATURE_DISABLED.
    """
    monkeypatch.setattr(settings, "b_ff_replan", False)

    resp = test_client.post(
        "/b/v1/replans",
        headers=auth_headers,
        json={
            "plan_id": "pln_dummy",
            "disruption": {
                "type": "tanker_down",
                "params": {"tanker_id": "tk_01"},
            },
        },
    )
    assert resp.status_code == 403
    data = resp.json()
    assert data["error"]["code"] == "FEATURE_DISABLED"


def test_replan_unknown_plan(test_client, auth_headers):
    """
    Test unknown plan_id returns 404 PLAN_NOT_FOUND.
    """
    resp = test_client.post(
        "/b/v1/replans",
        headers=auth_headers,
        json={
            "plan_id": "pln_nonexistent_999",
            "disruption": {
                "type": "tanker_down",
                "params": {"tanker_id": "tk_01"},
            },
        },
    )
    assert resp.status_code == 404
    data = resp.json()
    assert data["error"]["code"] == "PLAN_NOT_FOUND"


def test_replan_invalid_disruption_params(test_client, auth_headers):
    """
    Test invalid disruption parameters pass through Member A client error mapping:
    - fraction out of range -> 422 DISRUPTION_INVALID
    """
    scenario_id = "scn_sa01_w06"
    create_resp = test_client.post(
        "/b/v1/plans",
        headers=auth_headers,
        json={"scenario_id": scenario_id},
    )
    plan_id = create_resp.json()["plan_id"]

    resp = test_client.post(
        "/b/v1/replans",
        headers=auth_headers,
        json={
            "plan_id": plan_id,
            "disruption": {
                "type": "supply_cut",
                "params": {"fraction": 0.99},  # exceeds max 0.9
            },
        },
    )
    assert resp.status_code == 422
    data = resp.json()
    assert data["error"]["code"] == "DISRUPTION_INVALID"
