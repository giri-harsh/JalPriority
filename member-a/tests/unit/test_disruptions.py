"""M8: CONTRACT 5.12 disruption semantics, errors, derived-id hashing, idempotency, 6.4 endpoint.

OWNER: LEAD. Uses an in-memory repository so it is independent of the generated seed data."""
from __future__ import annotations

import hashlib
import json
import math
import re
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from jp_a.api.deps import Container
from jp_a.api.errors import DisruptionInvalidError, DisruptionNotApplicableError, ScenarioNotFoundError
from jp_a.domain.disruptions import apply_disruption, derived_scenario_id, root_scenario_id, validate_disruption
from jp_a.domain.entities import DERIVED_SCENARIO_RE, Scenario, ScenarioSummary
from jp_a.main import create_app
from jp_a.services.scenario_service import ScenarioService

SEED_ID = "scn_sa01_w06"
APPLIED_AT = "2026-10-10T09:15:02.481Z"


class FakeRepo:
    """Minimal ScenarioRepository stand-in (same surface as the frozen interface)."""

    def __init__(self, seed: Scenario):
        self.items: dict[str, Scenario] = {seed.scenario_id: seed}

    def list_summaries(self, include_derived: bool = False):
        out = []
        for s in self.items.values():
            if s.parent_scenario_id is not None and not include_derived:
                continue
            out.append(
                ScenarioSummary(
                    scenario_id=s.scenario_id,
                    parent_scenario_id=s.parent_scenario_id,
                    service_area_id=s.service_area_id,
                    window_index=s.window_index,
                    label=s.scenario_id,
                    n_areas=len(s.areas),
                    supply_litres=s.supply.tanker_water_available_litres,
                    tankers_available=sum(t.status == "available" for t in s.tankers),
                    alert_level=s.heat.alert_level,
                )
            )
        return out

    def get(self, scenario_id: str) -> Scenario:
        try:
            return self.items[scenario_id]
        except KeyError:
            raise ScenarioNotFoundError(scenario_id) from None

    def exists(self, scenario_id: str) -> bool:
        return scenario_id in self.items

    def save_derived(self, scenario: Scenario) -> None:
        self.items[scenario.scenario_id] = scenario

    def get_truth(self, scenario_id: str):
        return None


@pytest.fixture()
def seed(load_fixture) -> Scenario:
    return Scenario.model_validate(load_fixture("contract_scenario_example"))


@pytest.fixture()
def repo(seed):
    return FakeRepo(seed)


@pytest.fixture()
def api(settings, repo):
    container = Container(
        settings=settings,
        scenario_repo=repo,
        model_repo=None,
        prediction_service=None,
        scenario_service=ScenarioService(settings, repo),
        plan_service=None,
    )
    with TestClient(create_app(settings, container)) as c:
        yield c


def _expected_hash(applied: list[dict]) -> str:
    payload = json.dumps([{"type": a["type"], "params": a["params"]} for a in applied], sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(payload.encode()).hexdigest()[:6]


# --------------------------------------------------------------------------- pure functions
def test_tanker_down_effect(seed):
    params = validate_disruption(seed, "tanker_down", {"tanker_id": "tk_01"})
    child = apply_disruption(seed, "tanker_down", params, APPLIED_AT)
    status = {t.tanker_id: t.status for t in child.tankers}
    assert status == {"tk_01": "unavailable", "tk_02": "available", "tk_03": "unavailable"}
    assert child.supply == seed.supply and child.heat == seed.heat  # nothing else changes
    assert child.parent_scenario_id == SEED_ID
    assert [d.model_dump() for d in child.disruptions_applied] == [{"type": "tanker_down", "params": {"tanker_id": "tk_01"}, "applied_at": APPLIED_AT}]
    assert seed.tankers[0].status == "available"  # parent untouched


def test_tanker_down_errors(seed):
    with pytest.raises(DisruptionInvalidError) as e:
        validate_disruption(seed, "tanker_down", {"tanker_id": "tk_99"})
    assert (e.value.code, e.value.status_code) == ("DISRUPTION_INVALID", 422)
    with pytest.raises(DisruptionNotApplicableError) as e2:
        validate_disruption(seed, "tanker_down", {"tanker_id": "tk_03"})  # already unavailable in the example
    assert (e2.value.code, e2.value.status_code) == ("DISRUPTION_NOT_APPLICABLE", 409)
    for bad in ({}, {"tanker_id": 3}, {"tanker_id": None}):
        with pytest.raises(DisruptionInvalidError):
            validate_disruption(seed, "tanker_down", bad)


@pytest.mark.parametrize(
    "supply,fraction",
    [(20000, 0.3), (20000, 0.05), (20000, 0.9), (20000, 0.25), (18700, 0.33), (12000, 0.5), (999999, 0.123)],
)
def test_supply_cut_formula(seed, supply, fraction):
    s = seed.model_copy(deep=True)
    s.supply.tanker_water_available_litres = supply
    child = apply_disruption(s, "supply_cut", validate_disruption(s, "supply_cut", {"fraction": fraction}), APPLIED_AT)
    expected = math.floor(Decimal(supply) * (1 - Decimal(str(fraction))) / 100) * 100  # exact arithmetic
    assert child.supply.tanker_water_available_litres == expected
    assert expected % 100 == 0 and expected <= supply * (1 - fraction) + 1e-6


def test_supply_cut_known_values(seed):
    got = {}
    for f in (0.05, 0.3, 0.5, 0.9):
        got[f] = apply_disruption(seed, "supply_cut", {"fraction": f}, APPLIED_AT).supply.tanker_water_available_litres
    assert got == {0.05: 19000, 0.3: 14000, 0.5: 10000, 0.9: 2000}


@pytest.mark.parametrize("bad", [0.04, 0.0, 0.91, 1.0, -0.2, "0.3", None, True, float("nan")])
def test_supply_cut_range_errors(seed, bad):
    with pytest.raises(DisruptionInvalidError):
        validate_disruption(seed, "supply_cut", {"fraction": bad})


def test_supply_cut_bounds_are_inclusive(seed):
    assert validate_disruption(seed, "supply_cut", {"fraction": 0.05}) == {"fraction": 0.05}
    assert validate_disruption(seed, "supply_cut", {"fraction": 0.9}) == {"fraction": 0.9}


@pytest.mark.parametrize("new_level", [0, 1, 3])
def test_heat_alert_effect(seed, new_level):
    delta = new_level - seed.heat.alert_level  # example scenario is at level 2
    child = apply_disruption(seed, "heat_alert", validate_disruption(seed, "heat_alert", {"alert_level": new_level}), APPLIED_AT)
    assert child.heat.alert_level == new_level
    assert child.heat.max_temp_c == round(seed.heat.max_temp_c + 1.5 * delta, 1)
    assert child.heat.heat_index_c == round(seed.heat.heat_index_c + 2.5 * delta, 1)


def test_heat_alert_known_values(seed):
    child = apply_disruption(seed, "heat_alert", {"alert_level": 3}, APPLIED_AT)
    assert (child.heat.max_temp_c, child.heat.heat_index_c) == (46.0, 51.5)


def test_heat_alert_errors(seed):
    with pytest.raises(DisruptionNotApplicableError):
        validate_disruption(seed, "heat_alert", {"alert_level": 2})  # delta == 0
    for bad in (4, -1, 2.5, "3", None, True):
        with pytest.raises(DisruptionInvalidError):
            validate_disruption(seed, "heat_alert", {"alert_level": bad})
    assert validate_disruption(seed, "heat_alert", {"alert_level": 3.0}) == {"alert_level": 3}  # integral float normalised


def test_unknown_type_is_disruption_invalid(seed):
    with pytest.raises(DisruptionInvalidError):
        validate_disruption(seed, "meteor_strike", {})


def test_extra_params_are_dropped(seed):
    assert validate_disruption(seed, "tanker_down", {"tanker_id": "tk_01", "junk": 1}) == {"tanker_id": "tk_01"}


# --------------------------------------------------------------------------- derived ids
def test_derived_id_format_and_hash(seed):
    child = apply_disruption(seed, "tanker_down", {"tanker_id": "tk_02"}, APPLIED_AT)
    assert DERIVED_SCENARIO_RE.match(child.scenario_id)
    applied = [d.model_dump() for d in child.disruptions_applied]
    assert child.scenario_id == f"{SEED_ID}__d1_{_expected_hash(applied)}"


def test_hash_ignores_timestamps_and_depends_on_params_only(seed):
    a = apply_disruption(seed, "supply_cut", {"fraction": 0.3}, "2026-01-01T00:00:00.000Z")
    b = apply_disruption(seed, "supply_cut", {"fraction": 0.3}, "2027-02-02T00:00:00.000Z")
    c = apply_disruption(seed, "supply_cut", {"fraction": 0.31}, APPLIED_AT)
    assert a.scenario_id == b.scenario_id != c.scenario_id


def test_chained_disruptions_accumulate_and_root_is_seed(seed):
    d1 = apply_disruption(seed, "tanker_down", {"tanker_id": "tk_02"}, APPLIED_AT)
    d2 = apply_disruption(d1, "supply_cut", {"fraction": 0.3}, APPLIED_AT)
    assert d2.parent_scenario_id == d1.scenario_id
    assert [d.type for d in d2.disruptions_applied] == ["tanker_down", "supply_cut"]
    assert d2.scenario_id.startswith(f"{SEED_ID}__d2_")
    assert root_scenario_id(d2.scenario_id) == SEED_ID
    assert d2.scenario_id == derived_scenario_id(SEED_ID, [d.model_dump() for d in d2.disruptions_applied])
    Scenario.model_validate(d2.model_dump(mode="json"))  # strict-valid


def test_order_of_disruptions_changes_the_id(seed):
    a = apply_disruption(apply_disruption(seed, "tanker_down", {"tanker_id": "tk_02"}, APPLIED_AT), "supply_cut", {"fraction": 0.3}, APPLIED_AT)
    b = apply_disruption(apply_disruption(seed, "supply_cut", {"fraction": 0.3}, APPLIED_AT), "tanker_down", {"tanker_id": "tk_02"}, APPLIED_AT)
    assert a.scenario_id != b.scenario_id


# --------------------------------------------------------------------------- endpoint 6.4
def test_post_creates_201_then_idempotent_200(api, auth_headers):
    body = {"type": "tanker_down", "params": {"tanker_id": "tk_02"}}
    r1 = api.post(f"/a/v1/scenarios/{SEED_ID}/disruptions", json=body, headers=auth_headers)
    assert r1.status_code == 201, r1.text
    derived = Scenario.model_validate(r1.json())  # strict
    assert derived.parent_scenario_id == SEED_ID and DERIVED_SCENARIO_RE.match(derived.scenario_id)
    r2 = api.post(f"/a/v1/scenarios/{SEED_ID}/disruptions", json=body, headers=auth_headers)
    assert r2.status_code == 200
    assert r2.json() == r1.json()  # same stored scenario (same applied_at)
    assert r2.json()["scenario_id"] == r1.json()["scenario_id"]


def test_derived_scenario_is_retrievable_and_listed_only_on_request(api, auth_headers):
    sid = api.post(f"/a/v1/scenarios/{SEED_ID}/disruptions", json={"type": "heat_alert", "params": {"alert_level": 3}}, headers=auth_headers).json()["scenario_id"]
    got = api.get(f"/a/v1/scenarios/{sid}", headers=auth_headers)
    assert got.status_code == 200 and got.json()["scenario_id"] == sid
    assert [s["scenario_id"] for s in api.get("/a/v1/scenarios", headers=auth_headers).json()["scenarios"]] == [SEED_ID]
    listed = api.get("/a/v1/scenarios?include_derived=true", headers=auth_headers).json()["scenarios"]
    assert [s["scenario_id"] for s in listed] == sorted([SEED_ID, sid])


def test_chain_via_api(api, auth_headers):
    first = api.post(f"/a/v1/scenarios/{SEED_ID}/disruptions", json={"type": "supply_cut", "params": {"fraction": 0.3}}, headers=auth_headers).json()
    second = api.post(f"/a/v1/scenarios/{first['scenario_id']}/disruptions", json={"type": "tanker_down", "params": {"tanker_id": "tk_01"}}, headers=auth_headers)
    assert second.status_code == 201
    body = second.json()
    assert body["parent_scenario_id"] == first["scenario_id"]
    assert re.match(rf"^{SEED_ID}__d2_[0-9a-f]{{6}}$", body["scenario_id"])
    assert len(body["disruptions_applied"]) == 2
    assert body["supply"]["tanker_water_available_litres"] == 14000


@pytest.mark.parametrize(
    "body,status,code",
    [
        ({"type": "tanker_down", "params": {"tanker_id": "tk_99"}}, 422, "DISRUPTION_INVALID"),
        ({"type": "tanker_down", "params": {"tanker_id": "tk_03"}}, 409, "DISRUPTION_NOT_APPLICABLE"),
        ({"type": "supply_cut", "params": {"fraction": 0.95}}, 422, "DISRUPTION_INVALID"),
        ({"type": "supply_cut", "params": {"fraction": 0.01}}, 422, "DISRUPTION_INVALID"),
        ({"type": "heat_alert", "params": {"alert_level": 4}}, 422, "DISRUPTION_INVALID"),
        ({"type": "heat_alert", "params": {"alert_level": 2}}, 409, "DISRUPTION_NOT_APPLICABLE"),
        ({"type": "earthquake", "params": {}}, 422, "DISRUPTION_INVALID"),
        ({"params": {}}, 422, "VALIDATION_ERROR"),
    ],
)
def test_error_codes(api, auth_headers, body, status, code):
    r = api.post(f"/a/v1/scenarios/{SEED_ID}/disruptions", json=body, headers=auth_headers)
    assert r.status_code == status, r.text
    err = r.json()["error"]
    assert err["code"] == code and set(err) == {"code", "message", "details", "request_id"}


def test_unknown_parent_is_404(api, auth_headers):
    r = api.post("/a/v1/scenarios/scn_sa01_w99/disruptions", json={"type": "supply_cut", "params": {"fraction": 0.3}}, headers=auth_headers)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "SCENARIO_NOT_FOUND"
    assert r.json()["error"]["details"] == {"scenario_id": "scn_sa01_w99"}


def test_disruption_requires_auth(api):
    r = api.post(f"/a/v1/scenarios/{SEED_ID}/disruptions", json={"type": "supply_cut", "params": {"fraction": 0.3}})
    assert r.status_code == 401


def test_second_application_of_tanker_down_on_derived_is_409(api, auth_headers):
    body = {"type": "tanker_down", "params": {"tanker_id": "tk_02"}}
    sid = api.post(f"/a/v1/scenarios/{SEED_ID}/disruptions", json=body, headers=auth_headers).json()["scenario_id"]
    r = api.post(f"/a/v1/scenarios/{sid}/disruptions", json=body, headers=auth_headers)
    assert r.status_code == 409
