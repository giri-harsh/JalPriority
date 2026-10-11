import pytest
import os
import sys
from pathlib import Path
import boto3
from datetime import datetime, timezone
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.create_ddb_tables import create_tables
from jp_b.config import settings
from jp_b.repositories.dynamodb_repo import (
    DynamoDBPlansRepository,
    DynamoDBLedgerRepository,
    DynamoDBEventsRepository,
    DynamoDBAuditRepository,
    DynamoDBWeightsRepository,
)
from jp_b.api.deps import (
    get_plans_repo,
    get_ledger_repo,
    get_events_repo,
    get_audit_repo,
    get_weights_repo,
)
from jp_b.repositories.sqlite_repo import (
    SQLitePlansRepository,
    SQLiteLedgerRepository,
    SQLiteEventsRepository,
    SQLiteAuditRepository,
    SQLiteWeightsRepository,
)

@pytest.fixture
def ddb_env(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-south-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("B_AWS_REGION", "ap-south-1")
    monkeypatch.setenv("B_DDB_TABLE_PREFIX", "jp_b_")

    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="ap-south-1")
        # Idempotently create tables via script
        create_tables(ddb=ddb)
        yield ddb

def test_create_tables_idempotency(ddb_env):
    # Calling create_tables a second time skips all existing tables without error
    res = create_tables(ddb=ddb_env)
    assert len(res["created"]) == 0
    assert len(res["skipped"]) == 5

def test_dynamodb_plans_repository(ddb_env):
    repo = DynamoDBPlansRepository(ddb=ddb_env)

    plan = {
        "plan_id": "pln_123",
        "scenario_id": "scn_1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "recommended",
        "data": "some_data"
    }
    repo.save(plan)

    fetched = repo.get("pln_123")
    assert fetched is not None
    assert fetched["plan_id"] == "pln_123"
    assert fetched["status"] == "recommended"

    # Non-existent
    assert repo.get("pln_nonexistent") is None

    # List by scenario
    plans = repo.list_by_scenario("scn_1")
    assert len(plans) == 1
    assert plans[0]["plan_id"] == "pln_123"

    # List latest
    plans_latest = repo.list_by_scenario("scn_1", latest=True)
    assert len(plans_latest) == 1

    # List with status filter
    plans_rec = repo.list_by_scenario("scn_1", status="recommended")
    assert len(plans_rec) == 1
    plans_app = repo.list_by_scenario("scn_1", status="approved")
    assert len(plans_app) == 0

def test_dynamodb_ledger_repository(ddb_env):
    repo = DynamoDBLedgerRepository(ddb=ddb_env)

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

def test_dynamodb_events_repository(ddb_env):
    repo = DynamoDBEventsRepository(ddb=ddb_env)

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

    # Filter by name
    assert len(repo.list_events(name="test_event")) == 1
    assert len(repo.list_events(name="other_event")) == 0

def test_dynamodb_audit_repository(ddb_env):
    repo = DynamoDBAuditRepository(ddb=ddb_env)

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

    assert len(repo.list_entries(plan_id="pln_other")) == 0

def test_dynamodb_weights_repository(ddb_env):
    repo = DynamoDBWeightsRepository(ddb=ddb_env)

    weights = {"urgency": 1.0, "debt": 1.0, "vulnerability": 0.5, "travel": 20.0}
    repo.save("sa_01", weights)

    fetched = repo.get("sa_01")
    assert fetched is not None
    assert fetched["urgency"] == 1.0

    assert repo.get("sa_02") is None

def test_deps_store_mode_switching(ddb_env, monkeypatch):
    # When B_STORE_MODE is dynamodb
    monkeypatch.setattr(settings, "b_store_mode", "dynamodb")
    assert isinstance(get_plans_repo(), DynamoDBPlansRepository)
    assert isinstance(get_ledger_repo(), DynamoDBLedgerRepository)
    assert isinstance(get_events_repo(), DynamoDBEventsRepository)
    assert isinstance(get_audit_repo(), DynamoDBAuditRepository)
    assert isinstance(get_weights_repo(), DynamoDBWeightsRepository)

    # When B_STORE_MODE is sqlite (default)
    monkeypatch.setattr(settings, "b_store_mode", "sqlite")
    assert isinstance(get_plans_repo(), SQLitePlansRepository)
    assert isinstance(get_ledger_repo(), SQLiteLedgerRepository)
    assert isinstance(get_events_repo(), SQLiteEventsRepository)
    assert isinstance(get_audit_repo(), SQLiteAuditRepository)
    assert isinstance(get_weights_repo(), SQLiteWeightsRepository)
