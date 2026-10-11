import json
from pathlib import Path

def test_fixtures_against_models():
    from jp_b.api import schemas
    
    fixtures_dir = Path("backend/jp_b/clients/fixtures")
    
    # map fixtures to their schemas
    fixture_map = {
        "contract_scenario_example.json": schemas.Scenario,
        "contract_scenario_summary_example.json": schemas.ScenarioSummary,
        "contract_prediction_example.json": schemas.PredictionSet,
        "contract_model_info_example.json": schemas.ModelInfo,
        "contract_plan_result_example.json": schemas.PlanResult,
        "contract_metrics_example.json": schemas.Metrics,
        "contract_plan_record_example.json": schemas.PlanRecord,
        "contract_event_example.json": schemas.Event,
    }
    
    # test validation
    for filename, schema in fixture_map.items():
        filepath = fixtures_dir / filename
        assert filepath.exists(), f"Fixture {filename} not found"
        with open(filepath, "r", encoding="utf-8") as f:
            data = f.read()
            
        # strict validation
        obj = schema.model_validate_json(data, strict=True)
        assert obj is not None
        
    with open(fixtures_dir / "contract_solve_request_example.json", "r", encoding="utf-8") as f:
        data = f.read()
    solve_req = schemas.SolveRequest.model_validate_json(data, strict=True)
    assert solve_req is not None

