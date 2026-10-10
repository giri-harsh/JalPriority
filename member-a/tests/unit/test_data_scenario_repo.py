"""ScenarioRepository (frozen interface in repositories/scenario_repo.py)."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from jp_a.api.errors import ScenarioNotFoundError
from jp_a.config import Settings
from jp_a.domain.disruptions import apply_disruption
from jp_a.domain.entities import ScenarioSummary
from jp_a.repositories.scenario_repo import ScenarioRepository
from jp_a.repositories.storage_local import LocalStorage

from .data_helpers import DATA_DIR, MAIN_ID, MILD_ID, SEED_IDS, SEVERE_ID, copy_data_dir, load_seed, load_truth

APPLIED_AT = "2026-10-10T09:15:02.481Z"
MALFORMED_IDS = [
    "", "SCN_SA01_W06", "scn_sa01_w06.json", "../scenarios/scn_sa01_w06", "scn_sa01_w06/../x",
    "scn___d1_abcdef", "scn_sa01_w06__d1_ABCDEF", "scn sa01", "..", "scn_sa01_w06\\x",
]  # fmt: skip


class SpyStorage(LocalStorage):
    """LocalStorage that records every path it is asked for."""

    def __init__(self, root):
        super().__init__(root)
        self.calls: list[str] = []

    def read_json(self, relpath):
        self.calls.append(relpath)
        return super().read_json(relpath)

    def exists(self, relpath):
        self.calls.append(relpath)
        return super().exists(relpath)

    def list_json(self, prefix):
        self.calls.append(prefix)
        return super().list_json(prefix)


@pytest.fixture()
def data_dir(tmp_path):
    return copy_data_dir(tmp_path / "data")


@pytest.fixture()
def storage(data_dir) -> SpyStorage:
    return SpyStorage(data_dir)


@pytest.fixture()
def repo(data_dir, storage) -> ScenarioRepository:
    return ScenarioRepository(Settings(a_data_dir=data_dir), storage)


def test_list_summaries_seeds(repo):
    summaries = repo.list_summaries()
    assert [s.scenario_id for s in summaries] == sorted(SEED_IDS)
    by_id = {s.scenario_id: s for s in summaries}
    for sid in SEED_IDS:
        scenario = load_seed(sid)
        ScenarioSummary.model_validate(by_id[sid].model_dump())
        assert by_id[sid].model_dump() == {
            "schema_version": "1.0",
            "scenario_id": sid,
            "parent_scenario_id": None,
            "service_area_id": "sa_01",
            "window_index": 6,
            "label": by_id[sid].label,
            "n_areas": 8,
            "supply_litres": scenario.supply.tanker_water_available_litres,
            "tankers_available": 4,
            "alert_level": scenario.heat.alert_level,
            "is_synthetic": True,
        }
    assert by_id[MAIN_ID].label == "Main demo · heat alert 2 · supply ≈55% of shortfall"
    assert by_id[SEVERE_ID].label == "Severe heat · heat alert 3 · supply ≈35% of shortfall"
    assert by_id[MILD_ID].label == "Mild heat · heat alert 1 · supply ≈85% of shortfall"


def test_list_ignores_foreign_files(repo, data_dir):
    (data_dir / "scenarios" / "README.json").write_text("{}", encoding="utf-8")
    (data_dir / "scenarios" / "scn_x__d1_abcdef.json").write_text("{}", encoding="utf-8")  # derived id in seeds dir
    assert [s.scenario_id for s in repo.list_summaries()] == sorted(SEED_IDS)


def test_get_returns_the_strict_scenario(repo):
    scenario = repo.get(MAIN_ID)
    assert scenario == load_seed(MAIN_ID)
    assert scenario.model_dump(mode="json") == json.loads((DATA_DIR / "scenarios" / f"{MAIN_ID}.json").read_text(encoding="utf-8"))


def test_get_returns_independent_copies(repo):
    first = repo.get(MAIN_ID)
    first.tankers[0].status = "unavailable"
    first.areas.pop()
    again = repo.get(MAIN_ID)
    assert again.tankers[0].status == "available" and len(again.areas) == 8


def test_get_caches_parsed_scenarios(repo, storage):
    repo.get(MAIN_ID)
    reads = len(storage.calls)
    repo.get(MAIN_ID)
    repo.list_summaries()
    assert storage.calls.count(f"scenarios/{MAIN_ID}.json") == 1
    assert len(storage.calls) > reads  # listing still consults the directory


def test_unknown_scenario(repo):
    with pytest.raises(ScenarioNotFoundError) as exc:
        repo.get("scn_sa01_w99")
    assert exc.value.code == "SCENARIO_NOT_FOUND" and exc.value.status_code == 404
    assert exc.value.details == {"scenario_id": "scn_sa01_w99"}
    assert repo.exists("scn_sa01_w99") is False
    with pytest.raises(ScenarioNotFoundError):
        repo.get("scn_sa01_w06__d1_abcdef")


@pytest.mark.parametrize("bad_id", MALFORMED_IDS)
def test_malformed_ids_never_touch_storage(repo, storage, bad_id):
    with pytest.raises(ScenarioNotFoundError):
        repo.get(bad_id)
    assert repo.exists(bad_id) is False
    assert repo.get_truth(bad_id) is None
    assert storage.calls == []


def test_exists(repo):
    assert all(repo.exists(sid) for sid in SEED_IDS)


def test_strict_parse_rejects_corrupt_files(repo, data_dir):
    path = data_dir / "scenarios" / f"{MAIN_ID}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["unexpected"] = 1
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValidationError):
        repo.get(MAIN_ID)


def test_file_holding_another_id_is_rejected(repo, data_dir):
    src = (data_dir / "scenarios" / f"{MAIN_ID}.json").read_text(encoding="utf-8")
    (data_dir / "scenarios" / "scn_copy.json").write_text(src, encoding="utf-8")
    with pytest.raises(ValueError):
        repo.get("scn_copy")


def test_truth(repo):
    for sid in SEED_IDS:
        assert repo.get_truth(sid) == load_truth(sid)
    assert repo.get_truth("scn_sa01_w99") is None
    assert repo.get_truth("scn_sa01_w06__d1_abcdef") is None


def _derive(parent, dtype, params, applied_at=APPLIED_AT):
    return apply_disruption(parent, dtype, params, applied_at)


def test_save_derived_is_persisted_listed_and_labelled(repo, data_dir):
    derived = _derive(repo.get(MAIN_ID), "tanker_down", {"tanker_id": "tk_02"})
    assert not repo.exists(derived.scenario_id)
    repo.save_derived(derived)

    path = data_dir / "derived" / f"{derived.scenario_id}.json"
    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8")) == derived.model_dump(mode="json")
    assert repo.exists(derived.scenario_id)
    assert repo.get(derived.scenario_id) == derived
    assert repo.get_truth(derived.scenario_id) is None

    assert [s.scenario_id for s in repo.list_summaries()] == sorted(SEED_IDS)
    listed = repo.list_summaries(include_derived=True)
    assert [s.scenario_id for s in listed] == sorted([*SEED_IDS, derived.scenario_id])
    summary = next(s for s in listed if s.scenario_id == derived.scenario_id)
    assert summary.parent_scenario_id == MAIN_ID
    assert summary.tankers_available == 3
    assert summary.label == "Main demo · heat alert 2 · supply ≈55% of shortfall · tanker tk_02 down"


def test_derived_visible_to_a_fresh_repository(repo, data_dir):
    derived = _derive(repo.get(MAIN_ID), "supply_cut", {"fraction": 0.3})
    repo.save_derived(derived)
    fresh = ScenarioRepository(Settings(a_data_dir=data_dir), LocalStorage(data_dir))
    assert fresh.get(derived.scenario_id) == derived
    assert derived.scenario_id in [s.scenario_id for s in fresh.list_summaries(include_derived=True)]


def test_save_derived_is_idempotent(repo, data_dir):
    first = _derive(repo.get(MAIN_ID), "heat_alert", {"alert_level": 3})
    repo.save_derived(first)
    path = data_dir / "derived" / f"{first.scenario_id}.json"
    before = path.read_bytes()
    second = _derive(repo.get(MAIN_ID), "heat_alert", {"alert_level": 3}, applied_at="2026-10-11T00:00:00.000Z")
    assert second.scenario_id == first.scenario_id
    repo.save_derived(second)
    assert path.read_bytes() == before
    assert repo.get(first.scenario_id).disruptions_applied[0].applied_at == APPLIED_AT


def test_save_derived_rejects_seed_scenarios(repo):
    with pytest.raises(ValueError):
        repo.save_derived(repo.get(MAIN_ID))


@pytest.mark.parametrize(
    ("dtype", "params", "suffix"),
    [
        ("supply_cut", {"fraction": 0.3}, "supply cut 30%"),
        ("supply_cut", {"fraction": 0.05}, "supply cut 5%"),
        ("supply_cut", {"fraction": 0.125}, "supply cut 12.5%"),
        ("heat_alert", {"alert_level": 3}, "heat alert → 3"),
        ("tanker_down", {"tanker_id": "tk_04"}, "tanker tk_04 down"),
    ],
)
def test_derived_labels(repo, dtype, params, suffix):
    derived = _derive(repo.get(MAIN_ID), dtype, params)
    repo.save_derived(derived)
    summary = next(s for s in repo.list_summaries(include_derived=True) if s.scenario_id == derived.scenario_id)
    assert summary.label == f"Main demo · heat alert 2 · supply ≈55% of shortfall · {suffix}"


def test_chained_disruptions_label(repo):
    first = _derive(repo.get(SEVERE_ID), "tanker_down", {"tanker_id": "tk_01"})
    second = _derive(first, "supply_cut", {"fraction": 0.3})
    repo.save_derived(first)
    repo.save_derived(second)
    summary = next(s for s in repo.list_summaries(include_derived=True) if s.scenario_id == second.scenario_id)
    assert summary.parent_scenario_id == first.scenario_id
    assert summary.label == "Severe heat · heat alert 3 · supply ≈35% of shortfall · tanker tk_01 down · supply cut 30%"


def test_missing_label_table_falls_back(data_dir):
    (data_dir / "labels.json").unlink()
    repo = ScenarioRepository(Settings(a_data_dir=data_dir), LocalStorage(data_dir))
    labels = {s.scenario_id: s.label for s in repo.list_summaries()}
    assert labels[MAIN_ID] == "scn_sa01_w06 · heat alert 2"


def test_concurrent_access(repo):
    derived = [_derive(repo.get(MAIN_ID), "supply_cut", {"fraction": f / 100}) for f in range(10, 40, 2)]

    def work(k: int):
        repo.save_derived(derived[k % len(derived)])
        return repo.get(MAIN_ID).scenario_id, len(repo.list_summaries(include_derived=True))

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(work, range(60)))
    assert all(sid == MAIN_ID for sid, _ in results)
    assert len(repo.list_summaries(include_derived=True)) == len(SEED_IDS) + len(derived)


def test_real_data_directory():
    repo = ScenarioRepository(Settings(), LocalStorage(DATA_DIR))
    assert [s.scenario_id for s in repo.list_summaries()] == sorted(SEED_IDS)
    assert repo.get(MAIN_ID).heat.alert_level == 2
