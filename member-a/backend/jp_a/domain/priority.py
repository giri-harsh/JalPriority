"""Priority formulas F-2 to F-5 (CONTRACT section 5.11). Pure functions, no I/O.

    U_i  = sf_i / predicted_need_i            (0 when the need is 0)                    F-2
    D^_i = D_i / max(1, max_j D_j)            (max over ALL areas of the scenario)      F-3
    V_i  = vulnerability_index_i if vulnerability_available_i else 0                    F-4
    p_i  = round(1 + a*U_i + b*D^_i + g*V_i, 4)   from the UNROUNDED U, D^, V           F-5

The stored ``urgency`` / ``debt_normalized`` / ``vulnerability_used`` fields are the 4 dp roundings of
U, D^ and V; ``priority_weight`` is rounded exactly once, from the unrounded components.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from .entities import Area, PredictionSet, Scenario, Weights

RATIO_DP = 4


def urgency(shortfall_litres: int, predicted_need_litres: int) -> float:
    """F-2 (unrounded)."""
    if predicted_need_litres <= 0:
        return 0.0
    return shortfall_litres / predicted_need_litres


def normalized_debt(debt_litres: int, max_debt_litres: int) -> float:
    """F-3 (unrounded). ``max_debt_litres`` is the maximum input debt over all areas of the scenario."""
    return debt_litres / max(1, max_debt_litres)


def vulnerability_used(area: Area) -> float:
    """F-4: the vulnerability index, or 0 when it is not available for the area."""
    return float(area.vulnerability_index) if area.vulnerability_available else 0.0


def priority_weight(weights: Weights, urgency_value: float, debt_normalized_value: float, vulnerability: float) -> float:
    """F-5: rounded once to 4 dp from the unrounded components."""
    raw = (
        1.0
        + weights.urgency * urgency_value
        + weights.debt * debt_normalized_value
        + weights.vulnerability * vulnerability
    )
    return round(raw, RATIO_DP)


@dataclass(frozen=True)
class AreaPriority:
    """Per-area inputs and F-2..F-5 outputs. Ratio fields hold the 4 dp wire values."""

    area_id: str
    predicted_need_litres: int
    expected_piped_litres: int
    shortfall_litres: int
    debt_litres: int
    urgency: float
    debt_normalized: float
    vulnerability: float
    priority_weight: float


def compute_area_priorities(
    scenario: Scenario,
    prediction: PredictionSet,
    weights: Weights,
    debt_by_area: Mapping[str, int],
) -> list[AreaPriority]:
    """F-2..F-5 for every scenario area, in scenario order.

    ``debt_by_area`` may omit areas (debt 0). Unknown area ids and areas without a prediction raise
    ``ValueError`` (the service layer turns the former into a 422 before calling this).
    """
    area_ids = [a.area_id for a in scenario.areas]
    unknown = sorted(set(debt_by_area) - set(area_ids))
    if unknown:
        raise ValueError(f"debt_by_area has unknown area ids: {unknown}")
    by_area = {p.area_id: p for p in prediction.areas}
    missing = [aid for aid in area_ids if aid not in by_area]
    if missing:
        raise ValueError(f"prediction has no entry for areas: {missing}")

    debts = {aid: int(debt_by_area.get(aid, 0)) for aid in area_ids}
    max_debt = max(debts.values(), default=0)
    records: list[AreaPriority] = []
    for area in scenario.areas:
        pred = by_area[area.area_id]
        u = urgency(pred.predicted_shortfall_litres, pred.predicted_need_litres)
        d_hat = normalized_debt(debts[area.area_id], max_debt)
        v = vulnerability_used(area)
        records.append(
            AreaPriority(
                area_id=area.area_id,
                predicted_need_litres=pred.predicted_need_litres,
                expected_piped_litres=pred.expected_piped_litres,
                shortfall_litres=pred.predicted_shortfall_litres,
                debt_litres=debts[area.area_id],
                urgency=round(u, RATIO_DP),
                debt_normalized=round(d_hat, RATIO_DP),
                vulnerability=round(v, RATIO_DP),
                priority_weight=priority_weight(weights, u, d_hat, v),
            )
        )
    return records


def rank_key(record: AreaPriority) -> tuple[float, int, str]:
    """JalPriority rank order (CONTRACT 5.6): priority_weight desc, then shortfall desc, then area_id asc."""
    return (-record.priority_weight, -record.shortfall_litres, record.area_id)


def rank_areas(records: Iterable[AreaPriority]) -> list[AreaPriority]:
    """Records sorted by rank (index 0 = rank 1)."""
    return sorted(records, key=rank_key)


def bottom_third_cutoff(priorities: Sequence[float]) -> float | None:
    """Priority at or below which an area counts as "bottom third" (MEMBER_A F6).

    The bottom third of N areas is the lowest ceil(N/3) priorities; ties with the cutoff value are included
    so equal priorities are always treated alike. ``None`` when there are no areas.
    """
    if not priorities:
        return None
    ordered = sorted(priorities)
    k = -(-len(ordered) // 3)  # ceil(N / 3)
    return ordered[k - 1]


def in_bottom_third(priority: float, cutoff: float | None, top_priority: float) -> bool:
    """True when ``priority`` is at or below the cutoff and strictly below the highest priority.

    The second condition keeps the label relative: when every area has the same priority, none is "lower".
    """
    return cutoff is not None and priority <= cutoff and priority < top_priority
