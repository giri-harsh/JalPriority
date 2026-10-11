import pytest
import os
from datetime import datetime, timezone
from jp_b.repositories.sqlite_repo import (
    SQLitePlansRepository,
    SQLiteLedgerRepository,
    SQLiteEventsRepository,
    SQLiteAuditRepository,
    SQLiteWeightsRepository
)

@pytest.fixture
def clean_db():
    repo = SQLitePlansRepository() # Initializes the DB
    c = repo.conn.cursor()
    c.execute("DELETE FROM jp_b_plans")
    c.execute("DELETE FROM jp_b_ledger")
    c.execute("DELETE FROM jp_b_events")
    c.execute("DELETE FROM jp_b_audit")
    c.execute("DELETE FROM jp_b_weights")
    repo.conn.commit()
    return repo

def test_plans_repository(clean_db):
    repo = SQLitePlansRepository()
    
    plan = {
        "plan_id": "pln_123",
        "scenario_id": "scn_1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "recommended",
        "data": "some_data"
    }
    repo.save(plan)
    
    fetched = repo.get("pln_123")
    assert fetched["plan_id"] == "pln_123"
    
    plans = repo.list_by_scenario("scn_1")
    assert len(plans) == 1
    assert plans[0]["plan_id"] == "pln_123"
    
    plans_latest = repo.list_by_scenario("scn_1", latest=True)
    assert len(plans_latest) == 1

def test_ledger_repository(clean_db):
    repo = SQLiteLedgerRepository()
    
    data1 = {"area_id": "ar_01", "val": 100}
    repo.save("sa_01", "ar_01", 1, data1)
    
    data2 = {"area_id": "ar_02", "val": 200}
    repo.save("sa_01", "ar_02", 1, data2)
    
    res = repo.get_all_for_service_area("sa_01")
    assert len(res) == 2
    
    # Replace window
    new_rows = [{"area_id": "ar_01", "val": 150}]
    repo.replace_window("sa_01", 1, new_rows)
    
    res2 = repo.get_all_for_service_area("sa_01")
    assert len(res2) == 1
    assert res2[0]["val"] == 150

def test_events_repository(clean_db):
    repo = SQLiteEventsRepository()
    
    event = {
        "event_id": "evt_1",
        "scenario_id": "scn_1",
        "ts": datetime.now(timezone.utc).isoformat(),
        "name": "test_event",
        "data": "x"
    }
    repo.save(event)
    
    events = repo.list_events(scenario_id="scn_1")
    assert len(events) == 1
    assert events[0]["event_id"] == "evt_1"

def test_audit_repository(clean_db):
    repo = SQLiteAuditRepository()
    
    entry = {
        "audit_id": "aud_1",
        "ts": datetime.now(timezone.utc).isoformat(),
        "plan_id": "pln_1",
        "action": "test_action"
    }
    repo.save(entry)
    
    entries = repo.list_entries(plan_id="pln_1")
    assert len(entries) == 1
    assert entries[0]["audit_id"] == "aud_1"

def test_weights_repository(clean_db):
    repo = SQLiteWeightsRepository()
    
    weights = {"urgency": 1.0, "debt": 1.0, "vulnerability": 0.5, "travel": 20.0}
    repo.save("sa_01", weights)
    
    fetched = repo.get("sa_01")
    assert fetched["urgency"] == 1.0
    
    assert repo.get("sa_02") is None
