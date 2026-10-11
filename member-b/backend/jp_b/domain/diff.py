from typing import List, Dict, Any, Union
from jp_b.api.schemas import (
    PlanRecord,
    AreaDiff,
    DiffSummary,
    ReplanDiff,
    Allocation,
)


def compute_plan_diff(plan_before: PlanRecord, plan_after: PlanRecord) -> ReplanDiff:
    """
    Compute before-and-after allocation and assignment diff per CONTRACT §5.8 and MEMBER_B.md F8.
    Change classification precedence:
    - newly_served: litres_before == 0 and litres_after > 0
    - dropped: litres_before > 0 and litres_after == 0
    - increased: litres_after > litres_before
    - decreased: litres_after < litres_before
    - reassigned: same litres, but different tanker assignments or delivery orders
    - unchanged: same litres, same tankers, same orders
    litres_moved = Σ|delta| / 2
    """
    alloc_before_map: Dict[str, Allocation] = {
        a.area_id: a for a in plan_before.result.allocations
    }
    alloc_after_map: Dict[str, Allocation] = {
        a.area_id: a for a in plan_after.result.allocations
    }

    all_area_ids = sorted(list(set(alloc_before_map.keys()) | set(alloc_after_map.keys())))
    diff_areas: List[AreaDiff] = []

    for aid in all_area_ids:
        a_before = alloc_before_map.get(aid)
        a_after = alloc_after_map.get(aid)

        litres_before = a_before.allocated_litres if a_before else 0
        litres_after = a_after.allocated_litres if a_after else 0
        delta = litres_after - litres_before

        sorted_deliv_before = sorted(a_before.deliveries, key=lambda d: (d.stop_order, d.tanker_id)) if a_before else []
        tankers_before = [d.tanker_id for d in sorted_deliv_before]
        order_before = [d.stop_order for d in sorted_deliv_before]

        sorted_deliv_after = sorted(a_after.deliveries, key=lambda d: (d.stop_order, d.tanker_id)) if a_after else []
        tankers_after = [d.tanker_id for d in sorted_deliv_after]
        order_after = [d.stop_order for d in sorted_deliv_after]

        # Classify change
        if litres_before == 0 and litres_after > 0:
            change = "newly_served"
        elif litres_before > 0 and litres_after == 0:
            change = "dropped"
        elif litres_after > litres_before:
            change = "increased"
        elif litres_after < litres_before:
            change = "decreased"
        else:
            # litres_after == litres_before
            if tankers_before != tankers_after or order_before != order_after:
                change = "reassigned"
            else:
                change = "unchanged"

        diff_areas.append(
            AreaDiff(
                area_id=aid,
                litres_before=litres_before,
                litres_after=litres_after,
                delta=delta,
                tankers_before=tankers_before,
                tankers_after=tankers_after,
                order_before=order_before,
                order_after=order_after,
                change=change,
            )
        )

    areas_changed = sum(1 for d in diff_areas if d.change != "unchanged")
    litres_moved = sum(abs(d.delta) for d in diff_areas) // 2

    summary = DiffSummary(
        areas_changed=areas_changed,
        litres_moved=litres_moved,
        metrics_before=plan_before.metrics,
        metrics_after=plan_after.metrics,
    )

    return ReplanDiff(areas=diff_areas, summary=summary)
