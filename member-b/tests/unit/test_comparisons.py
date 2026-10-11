import pytest
from starlette.testclient import TestClient

from jp_b.main import app
from jp_b.services.auth_service import auth_service
from jp_b.api.schemas import ComparisonResponse, Weights
from jp_b.repositories.sqlite_repo import SQLiteEventsRepository, SQLitePlansRepository
from jp_b.services.comparison_service import compute_best_by_metric, _weights_match
from jp_b.domain.metrics import compute_metrics


@pytest.fixture
def test_client():
    return TestClient(app)


@pytest.fixture
def auth_headers():
    login_res = auth_service.login("officer_01", "jal-demo-2026")
    return {"Authorization": f"Bearer {login_res['token']}"}


def test_comparison_endpoint_all_three_strategies(test_client, auth_headers):
    """
    1 & 2. Successful comparison returns all three strategy results,
    each containing complete allocation and metric fields.
    """
    scenario_id = "scn_sa01_w06"
    weights = {"urgency": 1.2, "debt": 1.0, "vulnerability": 0.5, "travel": 20.0}

    resp = test_client.post(
        "/b/v1/comparisons",
        headers=auth_headers,
        json={"scenario_id": scenario_id, "weights": weights},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # Validate against strict ComparisonResponse schema
    comp = ComparisonResponse.model_validate(data)
    assert comp.scenario_id == scenario_id
    assert comp.is_simulation is True
    assert set(comp.results.keys()) == {"jalpriority", "fcfs", "nearest_first"}
    assert set(comp.metrics.keys()) == {"jalpriority", "fcfs", "nearest_first"}

    # Validate results contents
    for strat in ["jalpriority", "fcfs", "nearest_first"]:
        res = comp.results[strat]
        assert res.strategy == strat
        assert len(res.allocations) > 0
        assert len(res.routes) > 0
        assert res.totals is not None

        met = comp.metrics[strat]
        assert met.total_unmet_litres is not None
        assert met.priority_weighted_unmet is not None
        assert met.min_coverage_ratio is not None


def test_comparison_reuses_existing_plan(test_client, auth_headers):
    """
    4. Existing recommended/approved plan for same scenario & weights is reused.
    """
    scenario_id = "scn_sa01_w06"
    weights = {"urgency": 1.1, "debt": 0.9, "vulnerability": 0.4, "travel": 20.0}

    # 1. Create plan first via POST /b/v1/plans
    create_resp = test_client.post(
        "/b/v1/plans",
        headers=auth_headers,
        json={"scenario_id": scenario_id, "weights": weights},
    )
    assert create_resp.status_code == 201
    created_plan_id = create_resp.json()["plan_id"]

    # 2. Run comparison with identical weights
    comp_resp = test_client.post(
        "/b/v1/comparisons",
        headers=auth_headers,
        json={"scenario_id": scenario_id, "weights": weights},
    )
    assert comp_resp.status_code == 200
    comp_data = comp_resp.json()
    assert comp_data["plan_id"] == created_plan_id


def test_comparison_events_emitted(test_client, auth_headers):
    """
    6. Baseline plan events are emitted with plan_id: None.
    """
    scenario_id = "scn_sa01_w06"
    resp = test_client.post(
        "/b/v1/comparisons",
        headers=auth_headers,
        json={"scenario_id": scenario_id},
    )
    assert resp.status_code == 200

    events_repo = SQLiteEventsRepository()
    events = events_repo.list_events(scenario_id=scenario_id, name="plan_generated")
    fcfs_event = next((e for e in events if e.get("props", {}).get("strategy") == "fcfs"), None)
    nf_event = next((e for e in events if e.get("props", {}).get("strategy") == "nearest_first"), None)

    assert fcfs_event is not None
    assert fcfs_event["props"]["plan_id"] is None
    assert fcfs_event["props"]["strategy"] == "fcfs"

    assert nf_event is not None
    assert nf_event["props"]["plan_id"] is None
    assert nf_event["props"]["strategy"] == "nearest_first"


def test_best_by_metric_computation():
    """
    5. Test best_by_metric rules, directions, and tie-breaking.
    """
    from jp_b.api.schemas import Metrics

    m_jp = Metrics(
        schema_version="1.0",
        priority_weighted_unmet=100.0,
        deprivation_debt_remaining_litres=5000,
        total_unmet_litres=2000,
        min_coverage_ratio=0.8,
        total_travel_km=50.0,
        constraint_violations=0,
        replanning_time_ms=None,
    )
    m_fcfs = Metrics(
        schema_version="1.0",
        priority_weighted_unmet=200.0,
        deprivation_debt_remaining_litres=10000,
        total_unmet_litres=4000,
        min_coverage_ratio=0.2,
        total_travel_km=30.0,
        constraint_violations=0,
        replanning_time_ms=None,
    )
    m_nf = Metrics(
        schema_version="1.0",
        priority_weighted_unmet=250.0,
        deprivation_debt_remaining_litres=12000,
        total_unmet_litres=5000,
        min_coverage_ratio=0.1,
        total_travel_km=25.0,
        constraint_violations=1,
        replanning_time_ms=None,
    )

    best = compute_best_by_metric({
        "jalpriority": m_jp,
        "fcfs": m_fcfs,
        "nearest_first": m_nf,
    })

    assert best["priority_weighted_unmet"] == "jalpriority"
    assert best["deprivation_debt_remaining_litres"] == "jalpriority"
    assert best["total_unmet_litres"] == "jalpriority"
    assert best["min_coverage_ratio"] == "jalpriority"  # Higher is better
    assert best["total_travel_km"] == "nearest_first"     # Lower is better (25.0)
    assert best["constraint_violations"] == "tie"         # JP and FCFS tied at 0


def test_comparison_scenario_not_found(test_client, auth_headers):
    """8. Missing scenario returns 404 SCENARIO_NOT_FOUND."""
    resp = test_client.post(
        "/b/v1/comparisons",
        headers=auth_headers,
        json={"scenario_id": "scn_missing_scenario"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SCENARIO_NOT_FOUND"
