"""Demo-property report on REAL model predictions (MEMBER_A F1).

The F1 demo property must hold for the shortfall the API actually serves, not only for the hidden truth:

* at least ``MIN_QUALIFYING_AREAS`` areas that are vulnerable (``vulnerability_index >= 0.6``), among the
  ``RANK_K`` farthest from the depot, among the ``RANK_K`` latest requests, request less than their
  predicted shortfall and carry deprivation debt after the history (CONTRACT F-10, lambda = 0.3);
* total predicted shortfall ``>= MIN_SHORTFALL_TO_SUPPLY`` x tanker water available;
* at least one near, early, low-vulnerability area that requests more than its predicted shortfall.

Only ``scn_sa01_w06`` must satisfy the property (the mild seed deliberately has supply close to the shortfall);
the report is printed for every seed so the ratios can be compared.

Usage (prints one JSON report per seed scenario)::

    python -m jp_a.ml.demo_report
    python -m jp_a.ml.demo_report scn_sa01_w06
"""
from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from ..data.debt import DEFAULT_DEBT_DECAY, debt_after_history
from ..domain.entities import PredictionSet, Scenario

MIN_VULNERABILITY = 0.6
LOW_VULNERABILITY = 0.4
RANK_K = 3
MIN_QUALIFYING_AREAS = 2
MIN_SHORTFALL_TO_SUPPLY = 1.6
SEED_SCENARIO_IDS: tuple[str, ...] = ("scn_sa01_w06", "scn_sa01_w06_severe", "scn_sa01_w06_mild")


@dataclass(frozen=True)
class AreaDemoRow:
    area_id: str
    vulnerability_index: float
    depot_km: float
    distance_rank: int  # 1 = farthest from the depot
    request_rank: int  # 1 = latest request
    requested_litres: int
    predicted_shortfall_litres: int
    debt_litres: int
    qualifies: bool  # far, late, vulnerable, under-requesting, indebted
    over_requests: bool  # near, early, low vulnerability, requests more than its predicted shortfall


@dataclass(frozen=True)
class DemoReport:
    scenario_id: str
    supply_litres: int
    total_predicted_shortfall_litres: int
    shortfall_to_supply: float
    qualifying_area_ids: list[str]
    over_requesting_area_ids: list[str]
    areas: list[AreaDemoRow]

    @property
    def ratio_ok(self) -> bool:
        return self.shortfall_to_supply >= MIN_SHORTFALL_TO_SUPPLY

    @property
    def property_holds(self) -> bool:
        return (
            len(self.qualifying_area_ids) >= MIN_QUALIFYING_AREAS
            and self.ratio_ok
            and bool(self.over_requesting_area_ids)
        )

    def to_dict(self) -> dict:
        return {**asdict(self), "ratio_ok": self.ratio_ok, "property_holds": self.property_holds}


def demo_property_report(
    scenario: Scenario, predictions: PredictionSet, lam: float = DEFAULT_DEBT_DECAY
) -> DemoReport:
    """Evaluate the demo property of ``scenario`` against its PredictionSet (areas matched by id)."""
    if predictions.scenario_id != scenario.scenario_id:
        raise ValueError(f"predictions are for {predictions.scenario_id}, not {scenario.scenario_id}")
    shortfall = {p.area_id: p.predicted_shortfall_litres for p in predictions.areas}
    missing = sorted({a.area_id for a in scenario.areas} - shortfall.keys())
    if missing:
        raise ValueError(f"predictions are missing areas {missing}")

    depot_row = scenario.distance_km[scenario.depot.depot_id]
    by_distance = sorted(scenario.areas, key=lambda a: (-depot_row[a.area_id], a.area_id))
    by_lateness = sorted(scenario.areas, key=lambda a: (a.request_time, a.area_id), reverse=True)
    distance_rank = {a.area_id: i + 1 for i, a in enumerate(by_distance)}
    request_rank = {a.area_id: i + 1 for i, a in enumerate(by_lateness)}
    n = len(scenario.areas)

    rows: list[AreaDemoRow] = []
    for area in scenario.areas:
        aid = area.area_id
        debt = debt_after_history(area.history, lam)
        far, late = distance_rank[aid] <= RANK_K, request_rank[aid] <= RANK_K
        near, early = distance_rank[aid] > n - RANK_K, request_rank[aid] > n - RANK_K
        rows.append(
            AreaDemoRow(
                area_id=aid,
                vulnerability_index=area.vulnerability_index,
                depot_km=depot_row[aid],
                distance_rank=distance_rank[aid],
                request_rank=request_rank[aid],
                requested_litres=area.requested_litres,
                predicted_shortfall_litres=shortfall[aid],
                debt_litres=debt,
                qualifies=(
                    area.vulnerability_index >= MIN_VULNERABILITY
                    and far
                    and late
                    and area.requested_litres < shortfall[aid]
                    and debt > 0
                ),
                over_requests=(
                    near
                    and early
                    and area.vulnerability_index < LOW_VULNERABILITY
                    and area.requested_litres > shortfall[aid]
                ),
            )
        )

    supply = scenario.supply.tanker_water_available_litres
    total = sum(shortfall[a.area_id] for a in scenario.areas)
    return DemoReport(
        scenario_id=scenario.scenario_id,
        supply_litres=supply,
        total_predicted_shortfall_litres=total,
        shortfall_to_supply=round(total / supply, 4) if supply > 0 else float("inf"),
        qualifying_area_ids=[r.area_id for r in rows if r.qualifies],
        over_requesting_area_ids=[r.area_id for r in rows if r.over_requests],
        areas=rows,
    )


def main(argv: Sequence[str] | None = None) -> int:
    from ..api.deps import build_container
    from ..config import Settings

    scenario_ids = list(argv if argv is not None else sys.argv[1:]) or list(SEED_SCENARIO_IDS)
    container = build_container(Settings(shared_log_level="WARNING"))
    reports = []
    for scenario_id in scenario_ids:
        scenario = container.scenario_repo.get(scenario_id)
        predictions = container.prediction_service.get_predictions(scenario_id)
        reports.append(demo_property_report(scenario, predictions).to_dict())
    print(json.dumps(reports, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
