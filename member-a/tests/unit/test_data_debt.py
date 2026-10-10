"""Deprivation debt helper (CONTRACT 5.11 F-10 with lambda 0.3)."""
from __future__ import annotations

from jp_a.data.debt import debt_after_history, debt_by_area
from jp_a.domain.entities import HistoryEntry, Scenario


def test_contract_example_ledger_seeding(load_fixture):
    scenario = Scenario.model_validate(load_fixture("contract_scenario_example"))
    assert debt_by_area(scenario) == {"ar_01": 0, "ar_02": 8000, "ar_03": 12000}


def test_accepts_plain_dicts(load_fixture):
    assert debt_by_area(load_fixture("contract_scenario_example")) == {"ar_01": 0, "ar_02": 8000, "ar_03": 12000}


def test_ar_03_step_by_step():
    history = [
        {"window_index": 4, "need_litres": 20000, "received_litres": 14000},
        {"window_index": 5, "need_litres": 20500, "received_litres": 12700},
    ]
    assert debt_after_history(history[:1]) == 6000
    assert debt_after_history(history) == 12000  # 0.7 * 6000 + 7800


def test_processes_windows_in_ascending_order():
    rows = [
        HistoryEntry(window_index=2, need_litres=1000, received_litres=1000, max_temp_c=40.0, alert_level=1),
        HistoryEntry(window_index=1, need_litres=1000, received_litres=0, max_temp_c=40.0, alert_level=1),
    ]
    assert debt_after_history(rows) == 700  # 1000 unmet in w1, decayed once in w2


def test_over_delivery_never_creates_negative_debt():
    assert debt_after_history([{"window_index": 1, "need_litres": 100, "received_litres": 500}]) == 0


def test_empty_history_and_custom_decay():
    assert debt_after_history([]) == 0
    history = [
        {"window_index": 1, "need_litres": 1000, "received_litres": 0},
        {"window_index": 2, "need_litres": 0, "received_litres": 0},
    ]
    assert debt_after_history(history, lam=0.5) == 500
    assert debt_after_history(history, lam=0.0) == 1000
