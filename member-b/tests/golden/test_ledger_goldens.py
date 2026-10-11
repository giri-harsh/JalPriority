import json
from pathlib import Path
from jp_b.repositories.sqlite_repo import SQLiteLedgerRepository
from jp_b.services.ledger_service import LedgerService

def test_ledger_seeding_golden():
    """
    CONTRACT §5.11 line 605 & MEMBER_B.md M5 / §6.3:
    Ledger seeding check (B): with λ = 0.3, the history in §5.3 gives debt
    after window 5 of:
    ar_01 = 0
    ar_02 = 8000
    ar_03 = 12000
    For ar_03: 6000 after w4, then 0.7·6000 + 7800 = 12000 after w5.
    """
    fixture_path = Path("backend/jp_b/clients/fixtures/scn_contract_example.json")
    with open(fixture_path, "r", encoding="utf-8") as f:
        scenario_data = json.load(f)

    repo = SQLiteLedgerRepository()
    # Clean ledger table for clean test
    c = repo.conn.cursor()
    c.execute("DELETE FROM jp_b_ledger WHERE service_area_id = ?", (scenario_data["service_area_id"],))
    repo.conn.commit()

    service = LedgerService(repo)
    rows = service.seed_history(scenario_data, decay=0.3, cap_litres=None)

    # Check window 5 debt for each area
    w5_debts = {r["area_id"]: r["debt_litres"] for r in rows if r["window_index"] == 5}
    assert w5_debts["ar_01"] == 0
    assert w5_debts["ar_02"] == 8000
    assert w5_debts["ar_03"] == 12000

    # For ar_03 specifically: 6000 after w4, 12000 after w5
    ar_03_w4 = next(r for r in rows if r["area_id"] == "ar_03" and r["window_index"] == 4)
    ar_03_w5 = next(r for r in rows if r["area_id"] == "ar_03" and r["window_index"] == 5)
    assert ar_03_w4["debt_litres"] == 6000
    assert ar_03_w4["unmet_litres"] == 6000
    assert ar_03_w5["debt_litres"] == 12000
    assert ar_03_w5["unmet_litres"] == 7800
