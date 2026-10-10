"""Scenario repository. OWNER: Agent DATA.

INTERFACE (frozen by LEAD; routes, ScenarioService, PredictionService, PlanService and tests rely on it):

    ScenarioRepository(settings, storage)
        list_summaries(include_derived: bool = False) -> list[ScenarioSummary]   # sorted by scenario_id
        get(scenario_id: str) -> Scenario            # raises jp_a.api.errors.ScenarioNotFoundError
        exists(scenario_id: str) -> bool
        save_derived(scenario: Scenario) -> None     # persists to derived/<id>.json (gitignored dir)
        get_truth(scenario_id: str) -> dict | None   # hidden ground truth for seeds, else None
            truth json: {"scenario_id": str, "window_index": int, "true_need_litres": {area_id: int}}

Seeds live in <A_DATA_DIR>/scenarios/<id>.json, derived in <A_DATA_DIR>/derived/<id>.json,
truth in <A_DATA_DIR>/truth/<id>.json. Files are strict CONTRACT 5.3 Scenario JSON (no extra keys).
ScenarioSummary.label comes from a label table owned by this module (see MEMBER_A F1 / CONTRACT 5.3).

Label table: ``<A_DATA_DIR>/labels.json`` (written by the generator, ``{seed_id: label}``). Derived scenarios
are labelled ``"<seed label> · <disruption> · <disruption> ..."``, e.g.
``"Main demo · heat alert 2 · supply ≈55% of shortfall · tanker tk_02 down"``.

Ids are validated with ``is_valid_scenario_id`` before any storage access, so malformed or path-like ids
(``../x``, ``scn_A``) are reported as SCENARIO_NOT_FOUND without touching the filesystem.
Parsed scenarios, summaries and truth files are cached in memory; a lock makes the cache and the
check-then-write in ``save_derived`` safe under FastAPI's threadpool.
"""
from __future__ import annotations

import threading
from typing import Any

from ..api.errors import ScenarioNotFoundError
from ..domain.entities import (
    DERIVED_SCENARIO_RE,
    AppliedDisruption,
    Scenario,
    ScenarioSummary,
    is_valid_scenario_id,
)

SCENARIOS_PREFIX = "scenarios"
DERIVED_PREFIX = "derived"
TRUTH_PREFIX = "truth"
LABELS_PATH = "labels.json"


def is_derived_id(scenario_id: str) -> bool:
    return "__" in scenario_id


def describe_disruption(disruption: AppliedDisruption) -> str:
    """Short human-readable description used in derived scenario labels."""
    params = disruption.params
    if disruption.type == "tanker_down":
        return f"tanker {params['tanker_id']} down"
    if disruption.type == "supply_cut":
        return f"supply cut {round(float(params['fraction']) * 100, 1):g}%"
    return f"heat alert → {params['alert_level']}"


class ScenarioRepository:
    """Seed + derived scenarios on a StorageBackend (local files by default)."""

    def __init__(self, settings, storage):
        self.settings = settings
        self.storage = storage
        self._lock = threading.RLock()
        self._scenarios: dict[str, Scenario] = {}
        self._summaries: dict[str, ScenarioSummary] = {}
        self._truth: dict[str, dict[str, Any] | None] = {}
        self._labels: dict[str, str] | None = None

    # ------------------------------------------------------------------ public interface
    def list_summaries(self, include_derived: bool = False) -> list[ScenarioSummary]:
        ids = self._stored_ids(SCENARIOS_PREFIX)
        if include_derived:
            ids += self._stored_ids(DERIVED_PREFIX)
        summaries = []
        for scenario_id in sorted(ids):
            summary = self._summary(scenario_id)
            if summary is not None:
                summaries.append(summary)
        return summaries

    def get(self, scenario_id: str) -> Scenario:
        scenario = self._load(scenario_id)
        if scenario is None:
            raise ScenarioNotFoundError(scenario_id)
        return scenario.model_copy(deep=True)  # callers may mutate their copy; the cache stays pristine

    def exists(self, scenario_id: str) -> bool:
        if not is_valid_scenario_id(scenario_id):
            return False
        with self._lock:
            if scenario_id in self._scenarios:
                return True
        return self.storage.exists(self._relpath(scenario_id))

    def save_derived(self, scenario: Scenario) -> None:
        """Persist a derived scenario to ``derived/<id>.json``. Idempotent: an existing file is kept as is."""
        scenario_id = scenario.scenario_id
        if not DERIVED_SCENARIO_RE.match(scenario_id) or scenario.parent_scenario_id is None:
            raise ValueError(f"not a derived scenario: {scenario_id!r}")
        relpath = self._relpath(scenario_id)
        with self._lock:
            if self.storage.exists(relpath):
                return
            self.storage.write_json(relpath, scenario.model_dump(mode="json"))
            self._scenarios[scenario_id] = scenario.model_copy(deep=True)

    def get_truth(self, scenario_id: str) -> dict | None:
        if not is_valid_scenario_id(scenario_id) or is_derived_id(scenario_id):
            return None
        with self._lock:
            if scenario_id in self._truth:
                return self._truth[scenario_id]
        data = self.storage.read_json(f"{TRUTH_PREFIX}/{scenario_id}.json")
        truth = data if isinstance(data, dict) and data.get("scenario_id") == scenario_id else None
        with self._lock:
            return self._truth.setdefault(scenario_id, truth)

    # ------------------------------------------------------------------ labels
    def label_for(self, scenario: Scenario) -> str:
        if not is_derived_id(scenario.scenario_id):
            return self._labels_table().get(
                scenario.scenario_id, f"{scenario.scenario_id} · heat alert {scenario.heat.alert_level}"
            )
        root = scenario.scenario_id.split("__", 1)[0]
        parts = [self._labels_table().get(root, root)]
        parts += [describe_disruption(d) for d in scenario.disruptions_applied]
        return " · ".join(parts)

    def _labels_table(self) -> dict[str, str]:
        with self._lock:
            if self._labels is None:
                data = self.storage.read_json(LABELS_PATH)
                self._labels = (
                    {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
                )
            return self._labels

    # ------------------------------------------------------------------ internals
    @staticmethod
    def _relpath(scenario_id: str) -> str:
        prefix = DERIVED_PREFIX if is_derived_id(scenario_id) else SCENARIOS_PREFIX
        return f"{prefix}/{scenario_id}.json"

    def _stored_ids(self, prefix: str) -> list[str]:
        """Scenario ids stored under ``prefix`` whose file name is a valid id of the matching kind."""
        want_derived = prefix == DERIVED_PREFIX
        ids = []
        for relpath in self.storage.list_json(prefix):
            stem = relpath.rsplit("/", 1)[-1][: -len(".json")]
            if is_valid_scenario_id(stem) and is_derived_id(stem) == want_derived:
                ids.append(stem)
        return ids

    def _load(self, scenario_id: str) -> Scenario | None:
        if not is_valid_scenario_id(scenario_id):
            return None
        with self._lock:
            cached = self._scenarios.get(scenario_id)
        if cached is not None:
            return cached
        data = self.storage.read_json(self._relpath(scenario_id))
        if data is None:
            return None
        scenario = Scenario.model_validate(data)
        if scenario.scenario_id != scenario_id:
            raise ValueError(f"scenario file {self._relpath(scenario_id)} holds {scenario.scenario_id!r}")
        with self._lock:
            return self._scenarios.setdefault(scenario_id, scenario)

    def _summary(self, scenario_id: str) -> ScenarioSummary | None:
        with self._lock:
            cached = self._summaries.get(scenario_id)
        if cached is not None:
            return cached
        scenario = self._load(scenario_id)
        if scenario is None:  # removed between listing and loading
            return None
        summary = ScenarioSummary(
            scenario_id=scenario.scenario_id,
            parent_scenario_id=scenario.parent_scenario_id,
            service_area_id=scenario.service_area_id,
            window_index=scenario.window_index,
            label=self.label_for(scenario),
            n_areas=len(scenario.areas),
            supply_litres=scenario.supply.tanker_water_available_litres,
            tankers_available=sum(t.status == "available" for t in scenario.tankers),
            alert_level=scenario.heat.alert_level,
        )
        with self._lock:
            return self._summaries.setdefault(scenario_id, summary)
