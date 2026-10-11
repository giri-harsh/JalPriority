from typing import Dict, List, Optional, Any, Union
from datetime import datetime, timezone

from jp_b.repositories.base import LedgerRepository
from jp_b.api.schemas import (
    LedgerResponse,
    LedgerArea,
    LedgerSeriesPoint,
    LedgerTotalPoint,
)
from jp_b.domain.metrics import update_debt


class LedgerService:
    def __init__(self, ledger_repo: LedgerRepository):
        self.ledger_repo = ledger_repo

    def seed_history(
        self,
        scenario_data: dict,
        decay: float = 0.3,
        cap_litres: Optional[int] = None,
    ) -> List[dict]:
        """
        Seed historical windows for a service area per CONTRACT §5.11 & MEMBER_B.md F5.
        Computes windows 1..(current - 1) from areas[].history with F-10:
        u = max(0, need - received)
        D_w = round((1 - λ) * D_{w-1} + u)
        """
        service_area_id = scenario_data["service_area_id"]
        areas = scenario_data.get("areas", [])

        created_rows: List[dict] = []
        for area in areas:
            aid = area["area_id"]
            aname = area.get("name", aid)
            history = sorted(area.get("history", []), key=lambda h: int(h["window_index"]))

            current_debt = 0
            for h in history:
                w = int(h["window_index"])
                need = int(h.get("need_litres", 0))
                received = int(h.get("received_litres", 0))
                unmet = max(0, need - received)
                new_debt = update_debt(current_debt, unmet, decay=decay, cap_litres=cap_litres)

                row = {
                    "service_area_id": service_area_id,
                    "area_id": aid,
                    "area_name": aname,
                    "window_index": w,
                    "debt_litres": new_debt,
                    "unmet_litres": unmet,
                    "source": "history",
                    "plan_id": None,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                self.ledger_repo.save(service_area_id, aid, w, row)
                created_rows.append(row)
                current_debt = new_debt

        return created_rows

    def get_debt_by_area(self, service_area_id: str, window_index: int) -> Dict[str, int]:
        """
        Get debt by area after window (window_index - 1) per CONTRACT §5.11 and MEMBER_B.md B2.
        """
        target_window = window_index - 1
        rows = self.ledger_repo.get_all_for_service_area(service_area_id)
        debt_by_area: Dict[str, int] = {}
        for r in rows:
            if int(r.get("window_index", 0)) == target_window:
                debt_by_area[r["area_id"]] = int(r.get("debt_litres", 0))
        return debt_by_area

    def update_on_approval(
        self,
        plan_data: dict,
        scenario_data: Optional[dict] = None,
        decay: float = 0.3,
        cap_litres: Optional[int] = None,
    ) -> List[dict]:
        """
        Update ledger for a plan's window upon approval (F5).
        Replaces rows for that window (idempotent, supports reapproval and replanning).
        """
        service_area_id = plan_data["service_area_id"]
        window_index = int(plan_data["window_index"])

        # Fetch prior debt across areas
        existing_rows = self.ledger_repo.get_all_for_service_area(service_area_id)

        # Build map of prior window debt for each area
        prior_debts: Dict[str, int] = {}
        for r in existing_rows:
            aid = r["area_id"]
            w = int(r["window_index"])
            # Prior window is window_index - 1
            if w == window_index - 1:
                prior_debts[aid] = int(r.get("debt_litres", 0))

        # Also map area names from scenario if available
        area_names: Dict[str, str] = {}
        if scenario_data and "areas" in scenario_data:
            for a in scenario_data["areas"]:
                area_names[a["area_id"]] = a.get("name", a["area_id"])
        for r in existing_rows:
            if "area_name" in r and r["area_id"] not in area_names:
                area_names[r["area_id"]] = r["area_name"]

        # Allocations from plan result or plan data
        allocations = []
        if "result" in plan_data and isinstance(plan_data["result"], dict):
            allocations = plan_data["result"].get("allocations", [])
        elif "allocations" in plan_data:
            allocations = plan_data.get("allocations", [])

        new_rows: List[dict] = []
        now_iso = datetime.now(timezone.utc).isoformat()
        plan_id = plan_data.get("plan_id")

        for a in allocations:
            aid = a["area_id"]
            unmet = int(a.get("unmet_litres", 0))
            prior_debt = prior_debts.get(aid, 0)
            new_debt = update_debt(prior_debt, unmet, decay=decay, cap_litres=cap_litres)

            row = {
                "service_area_id": service_area_id,
                "area_id": aid,
                "area_name": area_names.get(aid, aid),
                "window_index": window_index,
                "debt_litres": new_debt,
                "unmet_litres": unmet,
                "source": "approved_plan",
                "plan_id": plan_id,
                "updated_at": now_iso,
            }
            new_rows.append(row)

        # Replace rows for this window
        self.ledger_repo.replace_window(service_area_id, window_index, new_rows)
        return new_rows

    def get_ledger(
        self,
        service_area_id: str,
        scenario_data: Optional[dict] = None,
        decay: float = 0.3,
        cap_litres: Optional[int] = None,
    ) -> LedgerResponse:
        """
        Get structured ledger for a service area, seeding from scenario history if needed.
        """
        existing_rows = self.ledger_repo.get_all_for_service_area(service_area_id)

        # If not yet seeded and scenario provided, seed now
        if not existing_rows and scenario_data:
            self.seed_history(scenario_data, decay=decay, cap_litres=cap_litres)
            existing_rows = self.ledger_repo.get_all_for_service_area(service_area_id)

        if not existing_rows:
            return LedgerResponse(
                service_area_id=service_area_id,
                decay=decay,
                cap_litres=cap_litres,
                windows=[],
                areas=[],
                totals=[],
            )

        # Group rows by area_id
        areas_map: Dict[str, List[dict]] = {}
        for r in existing_rows:
            aid = r["area_id"]
            if aid not in areas_map:
                areas_map[aid] = []
            areas_map[aid].append(r)

        area_models: List[LedgerArea] = []
        for aid in sorted(areas_map.keys()):
            a_rows = sorted(areas_map[aid], key=lambda x: int(x["window_index"]))
            series: List[LedgerSeriesPoint] = []
            for r in a_rows:
                series.append(
                    LedgerSeriesPoint(
                        window_index=int(r["window_index"]),
                        debt_litres=int(r.get("debt_litres", 0)),
                        unmet_litres=int(r.get("unmet_litres", 0)),
                        source=r.get("source", "history"),
                        plan_id=r.get("plan_id"),
                    )
                )
            current_debt = series[-1].debt_litres if series else 0
            aname = a_rows[-1].get("area_name", aid)
            area_models.append(
                LedgerArea(
                    area_id=aid,
                    name=aname,
                    current_debt_litres=current_debt,
                    series=series,
                )
            )

        # Collect unique windows ascending
        windows = sorted(list({int(r["window_index"]) for r in existing_rows}))

        # Compute totals per window
        totals: List[LedgerTotalPoint] = []
        for w in windows:
            tot_debt = sum(int(r.get("debt_litres", 0)) for r in existing_rows if int(r["window_index"]) == w)
            totals.append(LedgerTotalPoint(window_index=w, debt_litres=tot_debt))

        return LedgerResponse(
            service_area_id=service_area_id,
            decay=decay,
            cap_litres=cap_litres,
            windows=windows,
            areas=area_models,
            totals=totals,
        )
