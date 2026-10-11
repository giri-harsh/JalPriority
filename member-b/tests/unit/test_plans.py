import secrets
import pytest
from datetime import datetime, timezone
from jp_b.api.schemas import Weights, PlanRecord, CreatePlanRequest
from jp_b.config import settings
from jp_b.clients.a_mock import AMockClient
from jp_b.repositories.sqlite_repo import (
    SQLitePlansRepository,
    SQLiteLedgerRepository,
    SQLiteWeightsRepository,
    SQLiteEventsRepository,
    SQLiteAuditRepository,
)
from jp_b.services.ledger_service import LedgerService
from jp_b.services.event_service import EventService
from jp_b.services.audit_service import AuditService
from jp_b.services.plan_service import PlanService, DEFAULT_WEIGHTS


@pytest.fixture
def plan_service():
    a_client = AMockClient()
    plans_repo = SQLitePlansRepository()
    ledger_repo = SQLiteLedgerRepository()
    weights_repo = SQLiteWeightsRepository()
    events_repo = SQLiteEventsRepository()
    audit_repo = SQLiteAuditRepository()

    ledger_service = LedgerService(ledger_repo)
    event_service = EventService(events_repo)
    audit_service = AuditService(audit_repo)

    return PlanService(
        a_client=a_client,
        plans_repo=plans_repo,
        ledger_service=ledger_service,
        weights_repo=weights_repo,
        event_service=event_service,
        audit_service=audit_service,
    )


def test_resolve_weights_priority_order(plan_service):
    """Test resolution: request weights > stored weights > defaults, with feature flags."""
    service_area_id = f"sa_{secrets.token_hex(4)}"

    # 1. Defaults when no stored weights and no request weights
    w1 = plan_service.resolve_weights(service_area_id, request_weights=None)
    assert w1 == DEFAULT_WEIGHTS

    # 2. Stored weights when request weights is None
    custom_stored = Weights(urgency=2.0, debt=1.5, vulnerability=1.0, travel=10.0)
    plan_service.weights_repo.save(service_area_id, {
        "service_area_id": service_area_id,
        "weights": custom_stored.model_dump(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": "officer_01",
    })
    w2 = plan_service.resolve_weights(service_area_id, request_weights=None)
    assert w2 == custom_stored

    # 3. Request weights override stored weights
    req_weights = Weights(urgency=0.5, debt=0.8, vulnerability=0.2, travel=25.0)
    w3 = plan_service.resolve_weights(service_area_id, request_weights=req_weights)
    assert w3 == req_weights

    # 4. Feature flag B_FF_FAIRNESS_WEIGHTS=False reverts to defaults
    orig_ff = settings.b_ff_fairness_weights
    try:
        settings.b_ff_fairness_weights = False
        w4 = plan_service.resolve_weights(service_area_id, request_weights=req_weights)
        assert w4 == DEFAULT_WEIGHTS
    finally:
        settings.b_ff_fairness_weights = orig_ff

    # 5. Feature flag B_FF_VULNERABILITY=False zeroes vulnerability
    orig_vuln = settings.b_ff_vulnerability
    try:
        settings.b_ff_vulnerability = False
        w5 = plan_service.resolve_weights(service_area_id, request_weights=req_weights)
        assert w5.vulnerability == 0.0
        assert w5.urgency == req_weights.urgency
    finally:
        settings.b_ff_vulnerability = orig_vuln


@pytest.mark.anyio
async def test_generate_plan_persists_records_events_and_audit(plan_service):
    """Test end-to-end plan generation in PlanService and verify persistence."""
    scenario_id = "scn_sa01_w06"
    weights = Weights(urgency=1.0, debt=1.0, vulnerability=0.5, travel=20.0)
    officer_id = "officer_01"

    plan = await plan_service.generate_plan(
        scenario_id=scenario_id,
        request_weights=weights,
        time_limit_s=8.0,
        officer_id=officer_id,
    )

    # 1. Check returned PlanRecord
    assert isinstance(plan, PlanRecord)
    assert plan.status == "recommended"
    assert plan.scenario_id == scenario_id
    assert plan.created_by == officer_id
    assert plan.plan_id.startswith("pln_")
    assert plan.metrics is not None
    assert plan.result is not None

    # 2. Check persistence in PlansRepository
    stored_plan = plan_service.get_plan(plan.plan_id)
    assert stored_plan is not None
    assert stored_plan.plan_id == plan.plan_id
    assert stored_plan.status == "recommended"

    # 3. Check persistence of audit entry
    audit_entries = plan_service.audit_service.list_entries(plan_id=plan.plan_id)
    assert len(audit_entries) >= 1
    gen_entry = next((e for e in audit_entries if e.action == "plan_generated"), None)
    assert gen_entry is not None
    assert gen_entry.plan_id == plan.plan_id
    assert gen_entry.scenario_id == scenario_id
    assert gen_entry.officer_id == officer_id

    # 4. Check persistence of both events
    events = plan_service.event_service.list_events(scenario_id=scenario_id)
    event_names = [e.name for e in events]
    assert "prediction_generated" in event_names
    assert "plan_generated" in event_names

    plan_gen_evt = next(e for e in events if e.name == "plan_generated")
    assert plan_gen_evt.props["plan_id"] == plan.plan_id
    assert plan_gen_evt.props["strategy"] == "jalpriority"
    assert "solve_time_ms" in plan_gen_evt.props

    pred_gen_evt = next(e for e in events if e.name == "prediction_generated")
    assert "model_version" in pred_gen_evt.props
    assert "latency_ms" in pred_gen_evt.props
