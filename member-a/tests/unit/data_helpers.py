"""Shared helpers for the ``test_data_*`` modules (synthetic data, scenario repository)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from jp_a.domain.entities import Scenario

MEMBER_A_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = MEMBER_A_DIR / "data"

MAIN_ID = "scn_sa01_w06"
SEVERE_ID = "scn_sa01_w06_severe"
MILD_ID = "scn_sa01_w06_mild"
SEED_IDS = (MAIN_ID, SEVERE_ID, MILD_ID)
DEPOT = "dp_01"


def load_seed(scenario_id: str, data_dir: Path = DATA_DIR) -> Scenario:
    """Strict CONTRACT 5.3 parse of a committed seed file."""
    text = (data_dir / "scenarios" / f"{scenario_id}.json").read_text(encoding="utf-8")
    return Scenario.model_validate_json(text)


def load_truth(scenario_id: str, data_dir: Path = DATA_DIR) -> dict:
    return json.loads((data_dir / "truth" / f"{scenario_id}.json").read_text(encoding="utf-8"))


def copy_data_dir(dst: Path) -> Path:
    """Copy the committed seeds, truth files and label table to ``dst`` (tests never write into data/)."""
    for sub in ("scenarios", "truth"):
        shutil.copytree(DATA_DIR / sub, dst / sub)
    shutil.copy2(DATA_DIR / "labels.json", dst / "labels.json")
    (dst / "derived").mkdir()
    return dst


def true_shortfalls(scenario: Scenario, truth: dict, need_scale: float = 1.0) -> dict[str, int]:
    """F-1 shortfall computed from the hidden true need (optionally scaled to mimic a biased model)."""
    need = truth["true_need_litres"]
    return {
        a.area_id: max(0, round(need[a.area_id] * need_scale) - a.expected_piped_litres) for a in scenario.areas
    }


def depot_km(scenario: Scenario) -> dict[str, float]:
    return {a.area_id: scenario.distance_km[DEPOT][a.area_id] for a in scenario.areas}


def farthest(scenario: Scenario, k: int = 3) -> set[str]:
    d = depot_km(scenario)
    return set(sorted(d, key=lambda a: (-d[a], a))[:k])


def nearest(scenario: Scenario, k: int = 3) -> set[str]:
    d = depot_km(scenario)
    return set(sorted(d, key=lambda a: (d[a], a))[:k])


def latest_requests(scenario: Scenario, k: int = 3) -> set[str]:
    ordered = sorted(scenario.areas, key=lambda a: (a.request_time, a.area_id))
    return {a.area_id for a in ordered[-k:]}


def earliest_requests(scenario: Scenario, k: int = 3) -> set[str]:
    ordered = sorted(scenario.areas, key=lambda a: (a.request_time, a.area_id))
    return {a.area_id for a in ordered[:k]}
