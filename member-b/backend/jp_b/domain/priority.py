from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass
from jp_b.api.schemas import Weights, AreaInfo, PredictionArea

@dataclass(frozen=True)
class AreaPriorityResult:
    area_id: str
    shortfall_litres: int
    urgency: float
    debt_litres_used: int
    debt_normalized: float
    vulnerability_used: float
    priority_weight: float
    raw_urgency: float
    raw_debt_normalized: float
    raw_vulnerability: float


def compute_shortfall(predicted_need_litres: int, expected_piped_litres: int) -> int:
    """F-1 Shortfall: sf_i = max(0, predicted_need_i − expected_piped_i)"""
    return max(0, int(predicted_need_litres) - int(expected_piped_litres))


def compute_urgency(shortfall_litres: int, predicted_need_litres: int) -> float:
    """F-2 Urgency: U_i = sf_i / predicted_need_i if predicted_need_i > 0, else 0."""
    if predicted_need_litres <= 0:
        return 0.0
    return float(shortfall_litres) / float(predicted_need_litres)


def normalize_debt(debt_litres: int, max_debt_litres: int) -> float:
    """F-3 Normalised debt: D̂_i = D_i / max(1, max_j D_j)"""
    denom = max(1, int(max_debt_litres))
    return float(debt_litres) / float(denom)


def get_vulnerability(vulnerability_index: float, vulnerability_available: bool) -> float:
    """F-4 Vulnerability: V_i = vulnerability_index_i if vulnerability_available_i, else 0."""
    if not vulnerability_available:
        return 0.0
    return float(vulnerability_index)


def compute_priority(
    urgency: float,
    debt_normalized: float,
    vulnerability: float,
    weights: Weights,
) -> float:
    """
    F-5 Priority: p_i = round(1 + w.urgency·U_i + w.debt·D̂_i + w.vulnerability·V_i, 4),
    computed from unrounded U, D̂ and V.
    """
    raw_p = 1.0 + (weights.urgency * urgency) + (weights.debt * debt_normalized) + (weights.vulnerability * vulnerability)
    return round(raw_p, 4)


def compute_area_priority(
    area_id: str,
    predicted_need_litres: int,
    expected_piped_litres: int,
    debt_litres: int,
    max_debt_litres: int,
    vulnerability_index: float,
    vulnerability_available: bool,
    weights: Weights,
) -> AreaPriorityResult:
    """Compute all priority components for a single area."""
    sf = compute_shortfall(predicted_need_litres, expected_piped_litres)
    u_raw = compute_urgency(sf, predicted_need_litres)
    d_norm_raw = normalize_debt(debt_litres, max_debt_litres)
    v_raw = get_vulnerability(vulnerability_index, vulnerability_available)
    p_weight = compute_priority(u_raw, d_norm_raw, v_raw, weights)

    return AreaPriorityResult(
        area_id=area_id,
        shortfall_litres=sf,
        urgency=round(u_raw, 4),
        debt_litres_used=debt_litres,
        debt_normalized=round(d_norm_raw, 4),
        vulnerability_used=round(v_raw, 4),
        priority_weight=p_weight,
        raw_urgency=u_raw,
        raw_debt_normalized=d_norm_raw,
        raw_vulnerability=v_raw,
    )


def compute_priorities_for_scenario(
    areas: List[Union[AreaInfo, PredictionArea, dict]],
    debt_by_area: Dict[str, int],
    weights: Weights,
    predictions: Optional[List[Union[PredictionArea, dict]]] = None,
) -> Dict[str, AreaPriorityResult]:
    """
    Compute priorities for all areas in a scenario.
    Takes areas (from scenario) and optional predictions.
    """
    # Build predictions lookup if provided
    pred_map: Dict[str, Dict[str, Any]] = {}
    if predictions:
        for p in predictions:
            if isinstance(p, dict):
                pred_map[p["area_id"]] = p
            else:
                pred_map[p.area_id] = p.model_dump()

    # Determine max debt across all areas in the scenario
    area_ids = []
    for a in areas:
        aid = a["area_id"] if isinstance(a, dict) else a.area_id
        area_ids.append(aid)

    max_debt = 0
    for aid in area_ids:
        d = debt_by_area.get(aid, 0)
        if d > max_debt:
            max_debt = d

    results: Dict[str, AreaPriorityResult] = {}
    for a in areas:
        a_dict = a if isinstance(a, dict) else a.model_dump()
        aid = a_dict["area_id"]
        debt = debt_by_area.get(aid, 0)

        # Get need and piped
        if aid in pred_map:
            p_data = pred_map[aid]
            need = p_data["predicted_need_litres"]
            piped = p_data.get("expected_piped_litres", a_dict.get("expected_piped_litres", 0))
        elif "predicted_need_litres" in a_dict:
            need = a_dict["predicted_need_litres"]
            piped = a_dict.get("expected_piped_litres", 0)
        else:
            # Fall back to requested or expected piped
            need = a_dict.get("requested_litres", 0) + a_dict.get("expected_piped_litres", 0)
            piped = a_dict.get("expected_piped_litres", 0)

        v_idx = a_dict.get("vulnerability_index", 0.0)
        v_avail = a_dict.get("vulnerability_available", True)

        res = compute_area_priority(
            area_id=aid,
            predicted_need_litres=need,
            expected_piped_litres=piped,
            debt_litres=debt,
            max_debt_litres=max_debt,
            vulnerability_index=v_idx,
            vulnerability_available=v_avail,
            weights=weights,
        )
        results[aid] = res

    return results
