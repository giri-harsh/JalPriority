"""Route smoke tests for 6.2 / 6.3 over the real repository (Settings(a_data_dir=...) + create_app)."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from jp_a.config import Settings
from jp_a.domain.entities import ErrorEnvelope, Scenario, ScenarioList
from jp_a.main import create_app

from .data_helpers import DATA_DIR, MAIN_ID, SEED_IDS, copy_data_dir

DEV_SECRET = "dev-only-jalpriority-secret-change-me-0123456789"


@pytest.fixture()
def data_dir(tmp_path):
    return copy_data_dir(tmp_path / "data")


@pytest.fixture()
def client(data_dir):
    settings = Settings(
        a_data_dir=data_dir,
        a_auth_disabled=False,
        shared_jwt_secret=DEV_SECRET,
        shared_jwt_issuer="jalpriority-b",
        shared_jwt_audience="jalpriority",
        shared_env="local",
        shared_log_level="WARNING",
    )
    with TestClient(create_app(settings)) as c:
        yield c


def assert_error(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    body = ErrorEnvelope.model_validate(response.json())
    assert body.error.code == code
    return response.json()


def test_list_scenarios(client, auth_headers):
    r = client.get("/a/v1/scenarios", headers=auth_headers)
    assert r.status_code == 200
    body = ScenarioList.model_validate(r.json())
    assert [s.scenario_id for s in body.scenarios] == sorted(SEED_IDS)
    main = next(s for s in body.scenarios if s.scenario_id == MAIN_ID)
    assert main.label == "Main demo · heat alert 2 · supply ≈55% of shortfall"
    assert (main.n_areas, main.tankers_available, main.alert_level) == (8, 4, 2)


def test_get_scenario_returns_the_file_verbatim(client, auth_headers):
    for sid in SEED_IDS:
        r = client.get(f"/a/v1/scenarios/{sid}", headers=auth_headers)
        assert r.status_code == 200
        Scenario.model_validate(r.json())
        expected = json.loads((DATA_DIR / "scenarios" / f"{sid}.json").read_text(encoding="utf-8"))
        assert r.json() == expected


@pytest.mark.parametrize(
    "bad_id", ["scn_sa01_w99", "SCN_BAD", "scn_sa01_w06.json", "scn_sa01_w06__d1_abcdef", "scn_sa01_w06%2E%2E", "%2E%2E"]
)
def test_unknown_or_malformed_scenario_is_404(client, auth_headers, bad_id):
    body = assert_error(client.get(f"/a/v1/scenarios/{bad_id}", headers=auth_headers), 404, "SCENARIO_NOT_FOUND")
    assert body["error"]["details"]["scenario_id"]


@pytest.mark.parametrize("path", ["..%2F..%2Flabels", "..%2Ftruth%2Fscn_sa01_w06", "%2E%2E%2Fscenarios%2Fscn_sa01_w06"])
def test_path_traversal_attempts_are_404(client, auth_headers, path):
    r = client.get(f"/a/v1/scenarios/{path}", headers=auth_headers)
    assert r.status_code == 404
    assert "true_need_litres" not in r.text and "areas" not in r.json()


def test_requires_a_token(client):
    assert_error(client.get("/a/v1/scenarios"), 401, "UNAUTHORIZED")
    assert_error(client.get(f"/a/v1/scenarios/{MAIN_ID}"), 401, "UNAUTHORIZED")


def test_derived_scenario_round_trip(client, auth_headers, data_dir):
    r = client.post(
        f"/a/v1/scenarios/{MAIN_ID}/disruptions",
        headers=auth_headers,
        json={"type": "tanker_down", "params": {"tanker_id": "tk_02"}},
    )
    assert r.status_code == 201, r.text
    derived_id = r.json()["scenario_id"]
    assert (data_dir / "derived" / f"{derived_id}.json").is_file()

    got = client.get(f"/a/v1/scenarios/{derived_id}", headers=auth_headers)
    assert got.status_code == 200 and got.json() == r.json()

    default = client.get("/a/v1/scenarios", headers=auth_headers).json()["scenarios"]
    assert derived_id not in [s["scenario_id"] for s in default]
    listed = client.get("/a/v1/scenarios", params={"include_derived": "true"}, headers=auth_headers).json()
    summary = next(s for s in listed["scenarios"] if s["scenario_id"] == derived_id)
    assert summary["parent_scenario_id"] == MAIN_ID
    assert summary["label"].endswith(" · tanker tk_02 down")

    again = client.post(
        f"/a/v1/scenarios/{MAIN_ID}/disruptions",
        headers=auth_headers,
        json={"type": "tanker_down", "params": {"tanker_id": "tk_02"}},
    )
    assert again.status_code == 200 and again.json()["scenario_id"] == derived_id
