"""Deprivation debt from scenario history (CONTRACT 5.11 F-10, lambda = 0.3 by default).

    unmet_w = max(0, need_w - received_w)
    D_w     = round((1 - lambda) * D_{w-1} + unmet_w),   D_0 = 0 before the first history window

CONTRACT 5.11 sanity check: the 5.3 example history gives {ar_01: 0, ar_02: 8000, ar_03: 12000}.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

DEFAULT_DEBT_DECAY = 0.3


def _field(entry: Any, name: str) -> int:
    return int(entry[name] if isinstance(entry, Mapping) else getattr(entry, name))


def debt_after_history(history: Iterable[Any], lam: float = DEFAULT_DEBT_DECAY) -> int:
    """Debt after the last history window.

    ``history`` holds HistoryEntry models or plain dicts with ``window_index``, ``need_litres`` and
    ``received_litres``; it is processed in ascending ``window_index`` order.
    """
    debt = 0
    for entry in sorted(history, key=lambda e: _field(e, "window_index")):
        unmet = max(0, _field(entry, "need_litres") - _field(entry, "received_litres"))
        debt = round((1 - lam) * debt + unmet)
    return debt


def debt_by_area(scenario: Any, lam: float = DEFAULT_DEBT_DECAY) -> dict[str, int]:
    """``{area_id: debt after history}`` for every area of a Scenario (model or dict)."""
    areas = scenario["areas"] if isinstance(scenario, Mapping) else scenario.areas
    out: dict[str, int] = {}
    for area in areas:
        area_id = area["area_id"] if isinstance(area, Mapping) else area.area_id
        history = area["history"] if isinstance(area, Mapping) else area.history
        out[area_id] = debt_after_history(history, lam)
    return out
