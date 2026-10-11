import json
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from jp_b.main import app
from jp_b.repositories.sqlite_repo import SQLiteLedgerRepository
from jp_b.services.ledger_service import LedgerService
from jp_b.services.auth_service import auth_service
from jp_b.api.schemas import LedgerResponse

client = TestClient(app)


@pytest.fixture
def clean_ledger():
    repo = SQLiteLedgerRepository()
    c = repo.conn.cursor()
    c.execute("DELETE FROM jp_b_ledger")
    repo.conn.commit()
    return repo


@pytest.fixture
def auth_headers():
    res = auth_service.login("officer_01", "jal-demo-2026")
    return {"Authorization": f"Bearer {res['token']}"}


def test_ledger_seeding_with_cap(clean_ledger):
    fixture_path = Path("backend/jp_b/clients/fixtures/scn_contract_example.json")
    with open(fixture_path, "r", encoding="utf-8") as f:
        scenario_data = json.load(f)

    service = LedgerService(clean_ledger)
    # Seed with cap=10000
    rows = service.seed_history(scenario_data, decay=0.3, cap_litres=10000)

    # ar_03 debt would have been 12000, capped at 10000
    w5_debts = {r["area_id"]: r["debt_litres"] for r in rows if r["window_index"] == 5}
    assert w5_debts["ar_01"] == 0
    assert w5_debts["ar_02"] == 8000
    assert w5_debts["ar_03"] == 10000


def test_ledger_update_on_approval_and_reapproval_replacement(clean_ledger):
    fixture_path = Path("backend/jp_b/clients/fixtures/scn_contract_example.json")
    with open(fixture_path, "r", encoding="utf-8") as f:
        scenario_data = json.load(f)

    service = LedgerService(clean_ledger)
    service.seed_history(scenario_data, decay=0.3)

    plan_data = {
        "plan_id": "pln_test_01",
        "service_area_id": "sa_01",
        "window_index": 6,
        "allocations": [
            {"area_id": "ar_01", "unmet_litres": 9000},
            {"area_id": "ar_02", "unmet_litres": 0},
            {"area_id": "ar_03", "unmet_litres": 0},
        ]
    }

    # First approval for window 6
    rows_w6 = service.update_on_approval(plan_data, scenario_data=scenario_data, decay=0.3)
    assert len(rows_w6) == 3

    # Check debt calculations for window 6:
    # ar_01: prior w5 debt = 0 -> round((1-0.3)*0 + 9000) = 9000
    # ar_02: prior w5 debt = 8000 -> round((1-0.3)*8000 + 0) = 5600
    # ar_03: prior w5 debt = 12000 -> round((1-0.3)*12000 + 0) = 8400
    w6_debts = {r["area_id"]: r["debt_litres"] for r in rows_w6}
    assert w6_debts["ar_01"] == 9000
    assert w6_debts["ar_02"] == 5600
    assert w6_debts["ar_03"] == 8400

    all_rows = clean_ledger.get_all_for_service_area("sa_01")
    # 2 historical windows (w4, w5) * 3 areas = 6 rows + 3 rows for w6 = 9 rows
    assert len(all_rows) == 9

    # Re-approval (or replanned reapproval) for window 6 with different allocations
    revised_plan_data = {
        "plan_id": "pln_test_02_replan",
        "service_area_id": "sa_01",
        "window_index": 6,
        "allocations": [
            {"area_id": "ar_01", "unmet_litres": 4000},
            {"area_id": "ar_02", "unmet_litres": 2000},
            {"area_id": "ar_03", "unmet_litres": 1000},
        ]
    }
    reapproved_rows = service.update_on_approval(revised_plan_data, scenario_data=scenario_data, decay=0.3)
    assert len(reapproved_rows) == 3

    # Crucial test: Total rows MUST STILL BE 9 (no duplicates created!)
    all_rows_after = clean_ledger.get_all_for_service_area("sa_01")
    assert len(all_rows_after) == 9

    # Verify rows were replaced
    w6_after_debts = {r["area_id"]: r["debt_litres"] for r in all_rows_after if r["window_index"] == 6}
    # ar_01: round(0.7*0 + 4000) = 4000
    # ar_02: round(0.7*8000 + 2000) = 5600 + 2000 = 7600
    # ar_03: round(0.7*12000 + 1000) = 8400 + 1000 = 9400
    assert w6_after_debts["ar_01"] == 4000
    assert w6_after_debts["ar_02"] == 7600
    assert w6_after_debts["ar_03"] == 9400


def test_get_ledger_api_unauthorized():
    response = client.get("/b/v1/ledger?service_area_id=sa_01")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_get_ledger_api_missing_params(auth_headers):
    response = client.get("/b/v1/ledger", headers=auth_headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_get_ledger_api_scenario_not_found(auth_headers):
    response = client.get("/b/v1/ledger?scenario_id=scn_nonexistent", headers=auth_headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SCENARIO_NOT_FOUND"


def test_get_ledger_api_with_scenario_id_auto_seed(clean_ledger, auth_headers):
    # Call with contract fixture scenario
    response = client.get("/b/v1/ledger?scenario_id=scn_contract_example", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()

    # Validate against schema
    ledger_obj = LedgerResponse.model_validate(data)
    assert ledger_obj.service_area_id == "sa_01"
    assert ledger_obj.decay == 0.3
    assert ledger_obj.windows == [4, 5]
    assert len(ledger_obj.areas) == 3

    # Check totals
    totals_map = {t.window_index: t.debt_litres for t in ledger_obj.totals}
    assert totals_map[4] == 11000  # 0 + 5000 + 6000
    assert totals_map[5] == 20000  # 0 + 8000 + 12000

    # Check individual areas
    ar3 = next(a for a in ledger_obj.areas if a.area_id == "ar_03")
    assert ar3.current_debt_litres == 12000
    assert len(ar3.series) == 2
    assert ar3.series[0].window_index == 4
    assert ar3.series[0].debt_litres == 6000
    assert ar3.series[1].window_index == 5
    assert ar3.series[1].debt_litres == 12000


def test_get_ledger_api_with_service_area_id(clean_ledger, auth_headers):
    # Pre-seed via service
    fixture_path = Path("backend/jp_b/clients/fixtures/scn_contract_example.json")
    with open(fixture_path, "r", encoding="utf-8") as f:
        scenario_data = json.load(f)
    service = LedgerService(clean_ledger)
    service.seed_history(scenario_data)

    response = client.get("/b/v1/ledger?service_area_id=sa_01", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["service_area_id"] == "sa_01"
    assert len(data["areas"]) == 3
