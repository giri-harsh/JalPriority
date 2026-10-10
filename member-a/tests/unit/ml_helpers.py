"""Helpers for the ML tests: a tiny synthetic training set (exact CSV contract header) and a trained model.

The generator follows the documented data-generating process in spirit (need grows with heat, exposure and
alert level, AR(0.3) carry-over, piped share per area) but is small and independent of ``data/``.
"""
from __future__ import annotations

import copy
import csv
import json
from pathlib import Path
from typing import Any

import numpy as np

from jp_a.ml.features import CSV_COLUMNS
from jp_a.ml.train import train_model

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"

TRAIN_SERVICE_AREAS = [f"sa_{i:02d}" for i in range(2, 14)]
TEST_SERVICE_AREAS = [f"sa_{i:02d}" for i in range(34, 38)]

DERIVED_ID = "scn_sa01_w06__d1_3f9a2c"
TRUE_NEED = {"ar_01": 54500, "ar_02": 32800, "ar_03": 21400}


def _service_area_rows(rng: np.random.Generator, service_area_id: str, n_areas: int, n_windows: int) -> list[dict]:
    rows: list[dict] = []
    base_temp = rng.uniform(36.0, 41.0)
    temps = base_temp + 4.0 * np.sin(np.linspace(0, 3 * np.pi, n_windows)) + rng.normal(0, 1.2, n_windows)
    for a in range(1, n_areas + 1):
        population = int(rng.integers(5_000, 20_000))
        exposure = float(round(rng.uniform(0.3, 0.95), 2))
        piped_share = float(rng.uniform(0.55, 0.85))
        needs: list[int] = []
        received: list[int] = []
        prev_need = None
        for w in range(1, n_windows + 1):
            temp = float(round(temps[w - 1], 1))
            alert = int(np.clip((temp - 37.0) // 2.5, 0, 3))
            heat_index = round(temp + 2.0 + 1.5 * alert, 1)
            raw = population * 3.0 * (1 + 0.04 * max(0.0, temp - 35)) * (1 + 0.15 * exposure * alert / 3)
            raw *= float(rng.lognormal(0.0, 0.08))
            need = int(round(raw if prev_need is None else 0.7 * raw + 0.3 * prev_need))
            expected_piped = int(round(population * 3.0 * piped_share * (1 - 0.03 * alert)))
            got = int(min(need, expected_piped + rng.integers(0, 3000)))
            if w >= 2:
                last3 = needs[-3:]
                rows.append(
                    {
                        "service_area_id": service_area_id,
                        "area_id": f"ar_{a:02d}",
                        "window_index": w,
                        "max_temp_c": temp,
                        "heat_index_c": heat_index,
                        "alert_level": alert,
                        "heat_exposure": exposure,
                        "population": population,
                        "last_need_litres": needs[-1],
                        "mean_need_last3_litres": round(sum(last3) / len(last3), 1),
                        "last_received_litres": received[-1],
                        "expected_piped_litres": expected_piped,
                        "need_litres": need,
                    }
                )
            needs.append(need)
            received.append(got)
            prev_need = need
    return rows


def write_training_csv(path: Path, service_areas: list[str], *, seed: int, n_areas: int = 6, n_windows: int = 16) -> Path:
    rng = np.random.default_rng(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for sa in service_areas:
            writer.writerows(_service_area_rows(rng, sa, n_areas, n_windows))
    return path


def write_synthetic_split(base: Path) -> tuple[Path, Path]:
    train = write_training_csv(base / "train.csv", TRAIN_SERVICE_AREAS, seed=7)
    test = write_training_csv(base / "test.csv", TEST_SERVICE_AREAS, seed=11)
    return train, test


def train_synthetic_model(base: Path) -> tuple[Path, dict[str, Any]]:
    """Write a synthetic split under ``base/training`` and train into ``base/models``; returns (models_dir, meta)."""
    train, test = write_synthetic_split(base / "training")
    models_dir = base / "models"
    meta = train_model(train, test, models_dir)
    return models_dir, meta


MEMBER_A_DIR = Path(__file__).resolve().parents[2]
REAL_DATA_DIR = MEMBER_A_DIR / "data"
REAL_MODELS_DIR = MEMBER_A_DIR / "models"
SEED_SCENARIO_IDS = ("scn_sa01_w06", "scn_sa01_w06_severe", "scn_sa01_w06_mild")


def copy_real_data(dst: Path) -> Path:
    """Copy the committed seed scenarios, truth files and labels into ``dst`` (an isolated A_DATA_DIR).

    Endpoint tests that create derived scenarios write under ``dst/derived`` and never touch ``data/``.
    """
    for sub in ("scenarios", "truth"):
        (dst / sub).mkdir(parents=True, exist_ok=True)
        for src in sorted((REAL_DATA_DIR / sub).glob("*.json")):
            (dst / sub / src.name).write_bytes(src.read_bytes())
    labels = REAL_DATA_DIR / "labels.json"
    if labels.is_file():
        (dst / "labels.json").write_bytes(labels.read_bytes())
    (dst / "derived").mkdir(exist_ok=True)
    return dst


def widen_scenario(data: dict, scenario_id: str, n_areas: int) -> dict:
    """A copy of a scenario dict grown to ``n_areas`` by cloning existing areas (new ids, scaled population).

    Each clone sits 0.5 km from its template, so the distance matrix stays complete and symmetric.
    """
    widened = copy.deepcopy(data)
    widened["scenario_id"] = scenario_id
    templates = list(widened["areas"])
    dist = widened["distance_km"]
    for i in range(len(templates), n_areas):
        template = templates[i % len(templates)]
        clone = copy.deepcopy(template)
        clone["area_id"] = f"ar_{i + 1:02d}"
        clone["population"] = int(template["population"] * 1.1)
        dist[clone["area_id"]] = dict(dist[template["area_id"]])
        for node, row in dist.items():
            if node != clone["area_id"]:
                row[clone["area_id"]] = dist[template["area_id"]][node] if node != template["area_id"] else 0.5
        dist[clone["area_id"]][template["area_id"]] = 0.5
        dist[clone["area_id"]][clone["area_id"]] = 0.0
        widened["areas"].append(clone)
    return widened


def load_scenario_fixture() -> dict:
    return json.loads((FIXTURES_DIR / "contract_scenario_example.json").read_text(encoding="utf-8"))


def derived_scenario_dict() -> dict:
    data = copy.deepcopy(load_scenario_fixture())
    data["scenario_id"] = DERIVED_ID
    data["parent_scenario_id"] = data["scenario_id"].split("__", 1)[0]
    data["tankers"][0]["status"] = "unavailable"
    data["disruptions_applied"] = [
        {"type": "tanker_down", "params": {"tanker_id": "tk_01"}, "applied_at": "2026-10-10T09:20:00.000Z"}
    ]
    return data


def ten_area_scenario_dict() -> dict:
    """The contract example widened to 10 areas (latency check)."""
    data = copy.deepcopy(load_scenario_fixture())
    template = data["areas"]
    areas = []
    for i in range(10):
        area = copy.deepcopy(template[i % len(template)])
        area["area_id"] = f"ar_{i + 1:02d}"
        area["population"] = 6_000 + 1_500 * i
        areas.append(area)
    data["areas"] = areas
    ids = ["dp_01", *(a["area_id"] for a in areas)]
    data["distance_km"] = {f: {t: (0.0 if f == t else 5.0) for t in ids} for f in ids}
    return data


class FakeScenarioRepo:
    """In-memory scenario repository: the contract example as a seed plus one derived variant."""

    def __init__(self, scenarios: list[dict] | None = None, truth: dict[str, dict] | None = None):
        from jp_a.domain.entities import Scenario

        items = scenarios if scenarios is not None else [load_scenario_fixture(), derived_scenario_dict()]
        self._scenarios = {d["scenario_id"]: Scenario.model_validate(d) for d in items}
        self._truth = (
            truth
            if truth is not None
            else {"scn_sa01_w06": {"scenario_id": "scn_sa01_w06", "window_index": 6, "true_need_litres": dict(TRUE_NEED)}}
        )
        self.get_calls = 0

    def get(self, scenario_id: str):
        from jp_a.api.errors import ScenarioNotFoundError

        self.get_calls += 1
        if scenario_id not in self._scenarios:
            raise ScenarioNotFoundError(scenario_id)
        return self._scenarios[scenario_id]

    def exists(self, scenario_id: str) -> bool:
        return scenario_id in self._scenarios

    def get_truth(self, scenario_id: str) -> dict | None:
        return self._truth.get(scenario_id)
