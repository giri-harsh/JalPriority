import json
import math
import hashlib
import secrets
from decimal import Decimal
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Union

from jp_b.api.schemas import (
    Scenario,
    ScenarioSummary,
    PredictionSet,
    PredictionArea,
    FeatureContribution,
    ModelInfo,
    PlanResult,
    SolverInfo,
    PredictionInfo,
    Totals,
    Allocation,
    Delivery,
    Reason,
    Route,
    RouteStop,
    Unserved,
    ConstraintCheck,
    SolveRequest,
    Disruption,
    DisruptionApplied
)
from jp_b.api.errors import APIError

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

class AMockClient:
    """Mock Member A client conforming to CONTRACT §5 and §6 and MEMBER_B §5.1."""

    def __init__(self, fixtures_dir: Optional[Path] = None):
        self._fixtures_dir = fixtures_dir or _FIXTURES_DIR
        self._scenarios: Dict[str, Scenario] = {}
        self._derived_scenarios: Dict[str, Scenario] = {}
        self._load_fixtures()

    def _load_fixtures(self):
        # 1. Load scn_contract_example.json (or fallback to contract_scenario_example.json)
        contract_scn_path = self._fixtures_dir / "scn_contract_example.json"
        if not contract_scn_path.exists():
            contract_scn_path = self._fixtures_dir / "contract_scenario_example.json"
        with open(contract_scn_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Ensure scn_contract_example is available
            data_contract = dict(data)
            data_contract["scenario_id"] = "scn_contract_example"
            scn_contract = Scenario.model_validate(data_contract)
            self._scenarios["scn_contract_example"] = scn_contract

        # 2. Load scn_sa01_w06.json (8 areas)
        sa01_path = self._fixtures_dir / "scn_sa01_w06.json"
        if sa01_path.exists():
            with open(sa01_path, "r", encoding="utf-8") as f:
                scn_sa01 = Scenario.model_validate_json(f.read())
                self._scenarios["scn_sa01_w06"] = scn_sa01

        # 3. ModelInfo fixture
        model_info_path = self._fixtures_dir / "contract_model_info_example.json"
        with open(model_info_path, "r", encoding="utf-8") as f:
            self._model_info_data = json.load(f)

        # 4. Contract prediction fixture
        pred_path = self._fixtures_dir / "contract_prediction_example.json"
        with open(pred_path, "r", encoding="utf-8") as f:
            self._contract_pred_data = json.load(f)

        # 5. Contract solve/plan_result fixture
        result_path = self._fixtures_dir / "contract_plan_result_example.json"
        with open(result_path, "r", encoding="utf-8") as f:
            self._contract_plan_result_data = json.load(f)

    async def health(self) -> Dict[str, Any]:
        """Check upstream Member A health per §6.1."""
        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        return {
            "status": "ok",
            "service": "jp-a",
            "version": "1.0.0",
            "model_version": "xgb-sa-v1",
            "time": now
        }

    async def list_scenarios(self, include_derived: bool = False) -> List[ScenarioSummary]:
        """List scenario summaries per §6.2."""
        summaries: List[ScenarioSummary] = []
        all_scenarios = list(self._scenarios.values())
        if include_derived:
            all_scenarios.extend(self._derived_scenarios.values())

        for sc in all_scenarios:
            avail_tankers = sum(1 for t in sc.tankers if t.status == "available")
            if sc.scenario_id == "scn_sa01_w06":
                label = "Main demo · heat alert 2 · supply ≈55% of shortfall"
            elif sc.scenario_id == "scn_contract_example":
                label = "Contract example · heat alert 2 · 3 areas"
            elif sc.parent_scenario_id:
                label = f"Derived from {sc.parent_scenario_id}"
            else:
                label = f"{sc.service_area_name} · alert {sc.heat.alert_level}"

            summary = ScenarioSummary(
                schema_version="1.0",
                scenario_id=sc.scenario_id,
                parent_scenario_id=sc.parent_scenario_id,
                service_area_id=sc.service_area_id,
                window_index=sc.window_index,
                label=label,
                n_areas=len(sc.areas),
                supply_litres=sc.supply.tanker_water_available_litres,
                tankers_available=avail_tankers,
                alert_level=sc.heat.alert_level,
                is_synthetic=True
            )
            summaries.append(summary)

        summaries.sort(key=lambda s: s.scenario_id)
        return summaries

    async def get_scenario(self, scenario_id: str) -> Scenario:
        """Fetch Scenario by ID per §6.3."""
        if scenario_id in self._derived_scenarios:
            return self._derived_scenarios[scenario_id].model_copy(deep=True)
        if scenario_id in self._scenarios:
            return self._scenarios[scenario_id].model_copy(deep=True)
        raise APIError(
            code="SCENARIO_NOT_FOUND",
            status_code=404,
            message=f"Scenario {scenario_id} does not exist.",
            details={"scenario_id": scenario_id}
        )

    async def apply_disruption(
        self,
        scenario_id: str,
        disruption: Union[Disruption, Dict[str, Any]]
    ) -> Scenario:
        """Apply disruption per CONTRACT §5.12."""
        parent = await self.get_scenario(scenario_id)

        if isinstance(disruption, Disruption):
            dtype = disruption.type
            params = disruption.params
        elif isinstance(disruption, dict):
            dtype = disruption.get("type")
            params = disruption.get("params") or {}
        else:
            raise APIError("DISRUPTION_INVALID", status_code=422, message="Invalid disruption body")

        # Validate disruption per §5.12
        if dtype == "tanker_down":
            tanker_id = params.get("tanker_id")
            if not tanker_id:
                raise APIError("DISRUPTION_INVALID", status_code=422, message="Missing tanker_id")
            target_tanker = next((t for t in parent.tankers if t.tanker_id == tanker_id), None)
            if not target_tanker:
                raise APIError("DISRUPTION_INVALID", status_code=422, message=f"Unknown tanker_id {tanker_id}")
            if target_tanker.status == "unavailable":
                raise APIError("DISRUPTION_NOT_APPLICABLE", status_code=409, message=f"Tanker {tanker_id} is already unavailable")

        elif dtype == "supply_cut":
            fraction = params.get("fraction")
            if fraction is None or not isinstance(fraction, (int, float)) or isinstance(fraction, bool) or not (0.05 <= fraction <= 0.9):
                raise APIError("DISRUPTION_INVALID", status_code=422, message="fraction must be between 0.05 and 0.9")

        elif dtype == "heat_alert":
            alert_level = params.get("alert_level")
            if alert_level is None or not isinstance(alert_level, int) or isinstance(alert_level, bool) or not (0 <= alert_level <= 3):
                raise APIError("DISRUPTION_INVALID", status_code=422, message="alert_level must be an integer between 0 and 3")
            if alert_level == parent.heat.alert_level:
                raise APIError("DISRUPTION_NOT_APPLICABLE", status_code=409, message="Heat alert level is already at this level")

        else:
            raise APIError("DISRUPTION_INVALID", status_code=422, message=f"Unknown disruption type: {dtype}")

        # Compute derived scenario id and hash
        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        applied_record = DisruptionApplied(
            type=dtype,
            params=params,
            applied_at=datetime.fromisoformat(now.replace("Z", "+00:00"))
        )
        new_disruptions = [d for d in parent.disruptions_applied] + [applied_record]

        root = parent.scenario_id.split("__")[0]
        k = len(new_disruptions)
        applied_json_list = [{"params": d.params, "type": d.type} for d in new_disruptions]
        # Sort keys and ensure strict formatting
        canonical_json = json.dumps([{"type": d.type, "params": d.params} for d in new_disruptions], sort_keys=True, separators=(",", ":"))
        h = hashlib.sha1(canonical_json.encode("utf-8")).hexdigest()[:6]
        derived_id = f"{root}__d{k}_{h}"

        # Idempotency: return existing if already computed
        if derived_id in self._derived_scenarios:
            return self._derived_scenarios[derived_id].model_copy(deep=True)

        derived = parent.model_copy(deep=True)
        derived.scenario_id = derived_id
        derived.parent_scenario_id = parent.scenario_id
        derived.disruptions_applied = new_disruptions

        # Apply disruption changes
        if dtype == "tanker_down":
            for t in derived.tankers:
                if t.tanker_id == params["tanker_id"]:
                    t.status = "unavailable"
        elif dtype == "supply_cut":
            f = Decimal(str(params["fraction"]))
            S = Decimal(str(derived.supply.tanker_water_available_litres))
            s_new = math.floor(float((S * (Decimal("1") - f)) / Decimal("100"))) * 100
            derived.supply.tanker_water_available_litres = int(s_new)
        elif dtype == "heat_alert":
            new_level = int(params["alert_level"])
            delta = new_level - derived.heat.alert_level
            derived.heat.alert_level = new_level
            derived.heat.max_temp_c = round(derived.heat.max_temp_c + 1.5 * delta, 1)
            derived.heat.heat_index_c = round(derived.heat.heat_index_c + 2.5 * delta, 1)

        self._derived_scenarios[derived_id] = derived
        return derived.model_copy(deep=True)

    async def get_predictions(self, scenario_id: str) -> PredictionSet:
        """Fetch PredictionSet per §5.4 and MEMBER_B §5.1."""
        scenario = await self.get_scenario(scenario_id)

        # Exception for scn_contract_example: return verbatim §5.4 fixture
        if scenario_id == "scn_contract_example":
            data = dict(self._contract_pred_data)
            data["scenario_id"] = "scn_contract_example"
            return PredictionSet.model_validate(data)

        # Dynamic predictions calculation
        areas_pred: List[PredictionArea] = []
        for area in scenario.areas:
            last_history_need = area.history[-1].need_litres if area.history else area.requested_litres
            pred_need = int(round(last_history_need * (1.0 + 0.04 * scenario.heat.alert_level) / 100.0) * 100)
            sf_i = max(0, pred_need - area.expected_piped_litres)

            top_features = [
                FeatureContribution(feature="heat_index_c", contribution_litres=round(0.10 * pred_need, 1)),
                FeatureContribution(feature="last_need_litres", contribution_litres=round(0.06 * pred_need, 1)),
                FeatureContribution(feature="population", contribution_litres=round(0.03 * pred_need, 1)),
            ]

            areas_pred.append(PredictionArea(
                area_id=area.area_id,
                predicted_need_litres=pred_need,
                expected_piped_litres=area.expected_piped_litres,
                predicted_shortfall_litres=sf_i,
                baseline_last_need_litres=last_history_need,
                top_features=top_features
            ))

        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        return PredictionSet(
            schema_version="1.0",
            scenario_id=scenario_id,
            model_version="mock-0",
            generated_at=datetime.fromisoformat(now.replace("Z", "+00:00")),
            latency_ms=1,
            run_mae_litres=None,
            is_simulation=True,
            areas=areas_pred
        )

    async def solve(
        self,
        solve_request: Union[SolveRequest, Dict[str, Any]]
    ) -> PlanResult:
        """Greedy mock solver obeying contract guarantees and verbatim exception per §5.1."""
        if isinstance(solve_request, dict):
            req = SolveRequest.model_validate(solve_request)
        else:
            req = solve_request

        scenario = await self.get_scenario(req.scenario_id)

        # Verbatim exception for scn_contract_example with default weights and debt
        is_contract_default = (
            req.scenario_id == "scn_contract_example" and
            req.weights.urgency == 1.0 and
            req.weights.debt == 1.0 and
            req.weights.vulnerability == 0.5 and
            req.weights.travel == 20.0 and
            req.debt_by_area.get("ar_01", 0) == 0 and
            req.debt_by_area.get("ar_02", 0) == 8000 and
            req.debt_by_area.get("ar_03", 0) == 12000
        )
        if is_contract_default:
            data = dict(self._contract_plan_result_data)
            data["scenario_id"] = req.scenario_id
            return PlanResult.model_validate(data)

        # Compute predictions
        predictions = await self.get_predictions(req.scenario_id)
        pred_map = {a.area_id: a for a in predictions.areas}

        # Calculate priority for each area
        max_debt = max([req.debt_by_area.get(a.area_id, 0) for a in scenario.areas] + [1])
        area_calc = []
        for area in scenario.areas:
            pred = pred_map.get(area.area_id)
            sf_i = pred.predicted_shortfall_litres if pred else 0
            need_i = pred.predicted_need_litres if pred else 1

            u_i = sf_i / need_i if need_i > 0 else 0.0
            d_i = req.debt_by_area.get(area.area_id, 0)
            d_norm = d_i / max_debt
            v_i = area.vulnerability_index if area.vulnerability_available else 0.0

            p_i = round(1.0 + req.weights.urgency * u_i + req.weights.debt * d_norm + req.weights.vulnerability * v_i, 4)

            area_calc.append({
                "area": area,
                "sf_i": sf_i,
                "urgency": round(u_i, 4),
                "debt_used": d_i,
                "debt_norm": round(d_norm, 4),
                "v_used": round(v_i, 4),
                "priority": p_i
            })

        # Sort by priority desc, sf_i desc, area_id asc
        area_calc.sort(key=lambda x: (-x["priority"], -x["sf_i"], x["area"].area_id))

        # Tankers available
        avail_tankers = [t for t in scenario.tankers if t.status == "available"]
        avail_tankers.sort(key=lambda t: t.tanker_id)

        tanker_cap_left = {t.tanker_id: t.capacity_litres for t in avail_tankers}
        water_left = scenario.supply.tanker_water_available_litres

        deliveries_by_area: Dict[str, List[Delivery]] = {a["area"].area_id: [] for a in area_calc}
        deliveries_by_tanker: Dict[str, List[Dict[str, Any]]] = {t.tanker_id: [] for t in avail_tankers}


        # Greedily allocate water
        for item in area_calc:
            aid = item["area"].area_id
            target = min(item["sf_i"], water_left)
            target = (target // 100) * 100

            for t in avail_tankers:
                if target <= 0:
                    break
                can_take = min(target, tanker_cap_left[t.tanker_id])
                can_take = (can_take // 100) * 100
                if can_take > 0:
                    stop_order = len(deliveries_by_tanker[t.tanker_id]) + 1
                    delivery = Delivery(tanker_id=t.tanker_id, stop_order=stop_order, litres=can_take)
                    deliveries_by_area.setdefault(aid, []).append(delivery)
                    deliveries_by_tanker[t.tanker_id].append({
                        "stop_order": stop_order,
                        "area_id": aid,
                        "litres": can_take
                    })
                    tanker_cap_left[t.tanker_id] -= can_take
                    water_left -= can_take
                    target -= can_take

        # Build Routes
        routes: List[Route] = []
        depot_id = scenario.depot.depot_id

        for t in sorted(scenario.tankers, key=lambda x: x.tanker_id):
            if t.status == "unavailable":
                routes.append(Route(
                    tanker_id=t.tanker_id,
                    status="unavailable",
                    capacity_litres=t.capacity_litres,
                    max_route_km=t.max_route_km,
                    load_litres=0,
                    route_km=0.0,
                    stops=[],
                    path=[]
                ))
            else:
                stops_data = deliveries_by_tanker.get(t.tanker_id, [])
                if stops_data:
                    stops = [RouteStop(stop_order=s["stop_order"], area_id=s["area_id"], litres=s["litres"]) for s in stops_data]
                    path = [depot_id] + [s["area_id"] for s in stops_data] + [depot_id]
                    dist_km = 0.0
                    for i in range(len(path) - 1):
                        u, v = path[i], path[i+1]
                        dist_km += scenario.distance_km.get(u, {}).get(v, 0.0)
                    dist_km = round(dist_km, 2)
                    routes.append(Route(
                        tanker_id=t.tanker_id,
                        status="assigned",
                        capacity_litres=t.capacity_litres,
                        max_route_km=t.max_route_km,
                        load_litres=sum(s["litres"] for s in stops_data),
                        route_km=dist_km,
                        stops=stops,
                        path=path
                    ))
                else:
                    routes.append(Route(
                        tanker_id=t.tanker_id,
                        status="idle",
                        capacity_litres=t.capacity_litres,
                        max_route_km=t.max_route_km,
                        load_litres=0,
                        route_km=0.0,
                        stops=[],
                        path=[]
                    ))

        # Build Allocations
        allocations: List[Allocation] = []
        unserved_list: List[Unserved] = []

        for rank_idx, item in enumerate(area_calc, start=1):
            aid = item["area"].area_id
            sf_i = item["sf_i"]
            area_dels = deliveries_by_area.get(aid, [])
            allocated = sum(d.litres for d in area_dels)
            unmet = max(0, sf_i - allocated)
            cov_ratio = 1.0 if sf_i == 0 else round(min(1.0, allocated / sf_i), 4)

            reasons: List[Reason] = []
            if item["debt_used"] > 0:
                reasons.append(Reason(code="HIGH_DEBT", params={"debt_litres": item["debt_used"]}))
            if item["v_used"] >= 0.5:
                reasons.append(Reason(code="HIGH_VULNERABILITY", params={"vulnerability_index": item["v_used"]}))

            if allocated == sf_i and sf_i > 0:
                reasons.append(Reason(code="FULLY_SERVED", params={"allocated_litres": allocated}))
                reason_text = f"Served in full ({allocated:,} L). Priority {item['priority']:.2f}."
            elif 0 < allocated < sf_i:
                reasons.append(Reason(code="PARTIALLY_SERVED", params={"allocated_litres": allocated, "predicted_shortfall_litres": sf_i}))
                reasons.append(Reason(code="LOWER_RELATIVE_PRIORITY", params={"priority_weight": item["priority"]}))
                reason_text = f"Partially served: {allocated:,} of {sf_i:,} L. Unmet: {unmet:,} L."
            elif sf_i == 0:
                reasons.append(Reason(code="NO_SHORTFALL", params={}))
                reason_text = "No predicted shortfall for this area."
            else:
                reasons.append(Reason(code="NOT_SERVED", params={"predicted_shortfall_litres": sf_i}))
                reasons.append(Reason(code="SUPPLY_EXHAUSTED", params={"supply_available_litres": scenario.supply.tanker_water_available_litres, "unmet_litres": unmet}))
                reason_text = f"Not served: {unmet:,} L unmet due to water supply constraints."

            allocations.append(Allocation(
                area_id=aid,
                rank=rank_idx,
                predicted_shortfall_litres=sf_i,
                allocated_litres=allocated,
                unmet_litres=unmet,
                coverage_ratio=cov_ratio,
                priority_weight=item["priority"],
                urgency=item["urgency"],
                debt_litres_used=item["debt_used"],
                debt_normalized=item["debt_norm"],
                vulnerability_used=item["v_used"],
                deliveries=area_dels,
                reasons=reasons,
                reason_text=reason_text,
                is_unserved_remainder=(unmet > 0)
            ))

            if unmet > 0:
                unserved_list.append(Unserved(
                    area_id=aid,
                    unmet_litres=unmet,
                    reason_code="SUPPLY_EXHAUSTED"
                ))

        total_alloc = sum(a.allocated_litres for a in allocations)
        total_unmet = sum(a.unmet_litres for a in allocations)
        total_shortfall = sum(a.predicted_shortfall_litres for a in allocations)
        total_travel = round(sum(r.route_km for r in routes), 2)
        tanker_cap_avail = sum(t.capacity_litres for t in avail_tankers)

        totals = Totals(
            supply_available_litres=scenario.supply.tanker_water_available_litres,
            tanker_capacity_available_litres=tanker_cap_avail,
            shortfall_litres=total_shortfall,
            allocated_litres=total_alloc,
            unmet_litres=total_unmet,
            travel_km=total_travel
        )

        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        result_id = f"res_{secrets.token_hex(6)}"

        return PlanResult(
            schema_version="1.0",
            result_id=result_id,
            strategy="jalpriority",
            scenario_id=req.scenario_id,
            service_area_id=scenario.service_area_id,
            window_index=scenario.window_index,
            generated_at=datetime.fromisoformat(now.replace("Z", "+00:00")),
            weights=req.weights,
            solver=SolverInfo(
                engine="mock",
                status="heuristic",
                solve_time_ms=15,
                time_limit_s=req.time_limit_s
            ),
            prediction=PredictionInfo(
                model_version=predictions.model_version,
                latency_ms=predictions.latency_ms,
                run_mae_litres=predictions.run_mae_litres
            ),
            totals=totals,
            allocations=allocations,
            routes=routes,
            unserved=unserved_list,
            constraint_check=ConstraintCheck(
                water_cap_ok=True,
                capacity_ok=True,
                route_limit_ok=True,
                unavailable_tanker_ok=True,
                violations=[]
            )
        )

    async def get_model_info(self) -> ModelInfo:
        """Fetch model metadata per §5.5."""
        return ModelInfo.model_validate(self._model_info_data)
