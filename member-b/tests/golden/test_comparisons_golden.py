import pytest
from starlette.testclient import TestClient

from jp_b.main import app
from jp_b.services.auth_service import auth_service
from jp_b.api.schemas import ComparisonResponse


@pytest.fixture
def test_client():
    return TestClient(app)


@pytest.fixture
def auth_headers():
    login_res = auth_service.login("officer_01", "jal-demo-2026")
    return {"Authorization": f"Bearer {login_res['token']}"}


def test_comparison_golden_contract_example(test_client, auth_headers):
    """
    CONTRACT §5.3, §5.6, §5.7 & MEMBER_B.md F7 (B5) & B7 Golden Comparison:
    Compare all 3 strategies on scn_contract_example with default weights.
    Verify:
    - JalPriority PWU = 12683.7, unmet = 9000, debt_remaining = 23000, min_coverage = 0.3571
    - FCFS PWU = 36043.8, unmet = 15000, min_coverage = 0.0
    - Nearest-first PWU = 36043.8, unmet = 15000, min_coverage = 0.0
    - best_by_metric winners
    """
    from jp_b.repositories.sqlite_repo import SQLiteLedgerRepository, SQLitePlansRepository
    repo = SQLiteLedgerRepository()
    c = repo.conn.cursor()
    c.execute("DELETE FROM jp_b_ledger WHERE service_area_id = 'sa_01'")
    c.execute("DELETE FROM jp_b_plans WHERE scenario_id = 'scn_contract_example'")
    repo.conn.commit()

    scenario_id = "scn_contract_example"
    weights = {"urgency": 1.0, "debt": 1.0, "vulnerability": 0.5, "travel": 20.0}

    resp = test_client.post(
        "/b/v1/comparisons",
        headers=auth_headers,
        json={"scenario_id": scenario_id, "weights": weights},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    comp = ComparisonResponse.model_validate(data)

    jp_m = comp.metrics["jalpriority"]
    fcfs_m = comp.metrics["fcfs"]
    nf_m = comp.metrics["nearest_first"]

    # Verify golden metrics
    assert jp_m.priority_weighted_unmet == 12683.7
    assert jp_m.total_unmet_litres == 9000
    assert jp_m.deprivation_debt_remaining_litres == 23000
    assert jp_m.min_coverage_ratio == 0.3571
    assert jp_m.constraint_violations == 0

    assert fcfs_m.priority_weighted_unmet == 36043.8
    assert fcfs_m.total_unmet_litres == 15000
    assert fcfs_m.min_coverage_ratio == 0.0
    assert fcfs_m.constraint_violations == 0

    assert nf_m.priority_weighted_unmet == 36043.8
    assert nf_m.total_unmet_litres == 15000
    assert nf_m.min_coverage_ratio == 0.0
    assert nf_m.constraint_violations == 0

    # Verify best_by_metric
    best = comp.best_by_metric
    assert best["priority_weighted_unmet"] == "jalpriority"
    assert best["deprivation_debt_remaining_litres"] == "jalpriority"
    assert best["total_unmet_litres"] == "jalpriority"
    assert best["min_coverage_ratio"] == "jalpriority"
    assert best["constraint_violations"] == "tie"
