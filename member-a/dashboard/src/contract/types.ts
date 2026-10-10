import type { ReactNode } from "react";

export const SCREEN_CONTRACT_VERSION = "1.0.0";

export type TabId = "snapshot" | "plan" | "fairness" | "compare" | "replan";
export type Strategy = "jalpriority" | "fcfs" | "nearest_first";
export type PlanStatus = "recommended" | "approved" | "edited" | "rejected" | "superseded";
export type ReasonCode =
  | "HIGH_PREDICTED_SHORTFALL" | "HIGH_DEBT" | "HIGH_VULNERABILITY" | "HEAT_ALERT"
  | "LOWER_RELATIVE_PRIORITY" | "FULLY_SERVED" | "PARTIALLY_SERVED" | "NOT_SERVED"
  | "NO_SHORTFALL" | "SUPPLY_EXHAUSTED" | "TANKER_CAPACITY_LIMIT" | "ROUTE_LIMIT"
  | "TRAVEL_COST_TRADEOFF" | "TIME_LIMITED_SOLUTION" | "FCFS_ORDER" | "NEAREST_FIRST_ORDER";

export interface Weights { urgency: number; debt: number; vulnerability: number; travel: number; }
export interface Reason { code: ReasonCode; params: Record<string, number | string | boolean | null>; }
export interface Delivery { tanker_id: string; stop_order: number; litres: number; }
export interface Allocation {
  area_id: string; rank: number;
  predicted_shortfall_litres: number; allocated_litres: number; unmet_litres: number; coverage_ratio: number;
  priority_weight: number; urgency: number; debt_litres_used: number; debt_normalized: number; vulnerability_used: number;
  deliveries: Delivery[]; reasons: Reason[]; reason_text: string; is_unserved_remainder: boolean;
}
export interface RouteStop { stop_order: number; area_id: string; litres: number; }
export interface Route {
  tanker_id: string; status: "assigned" | "idle" | "unavailable";
  capacity_litres: number; max_route_km: number; load_litres: number; route_km: number;
  stops: RouteStop[]; path: string[];
}
export interface PlanResult {
  schema_version: "1.0"; result_id: string; strategy: Strategy;
  scenario_id: string; service_area_id: string; window_index: number; generated_at: string;
  weights: Weights;
  solver: { engine: string; status: "optimal" | "feasible" | "time_limited" | "heuristic"; solve_time_ms: number; time_limit_s: number | null };
  prediction: { model_version: string; latency_ms: number; run_mae_litres: number | null };
  totals: { supply_available_litres: number; tanker_capacity_available_litres: number; shortfall_litres: number; allocated_litres: number; unmet_litres: number; travel_km: number };
  allocations: Allocation[]; routes: Route[];
  unserved: { area_id: string; unmet_litres: number; reason_code: ReasonCode }[];
  constraint_check: { water_cap_ok: boolean; capacity_ok: boolean; route_limit_ok: boolean; unavailable_tanker_ok: boolean; violations: { code: string; detail: string }[] };
}
export interface Metrics {
  schema_version: "1.0";
  priority_weighted_unmet: number; deprivation_debt_remaining_litres: number; total_unmet_litres: number;
  min_coverage_ratio: number; total_travel_km: number; constraint_violations: number; replanning_time_ms: number | null;
}
export interface Decision { decision: "approved" | "edited" | "rejected"; officer_id: string; note: string | null; decided_at: string; next_plan_id: string | null; }
export interface PlanRecord {
  schema_version: "1.0"; plan_id: string; status: PlanStatus; created_at: string; created_by: string;
  scenario_id: string; service_area_id: string; window_index: number;
  supersedes_plan_id: string | null; replan_of_plan_id: string | null;
  disruption: { type: "tanker_down" | "supply_cut" | "heat_alert"; params: Record<string, number | string> } | null;
  is_synthetic: true; result: PlanResult; metrics: Metrics; decision: Decision | null;
}

export type TrackEvent =
  | { name: "scenario_loaded"; scenario_id: string; props: { n_areas: number; supply_litres: number; tankers_available: number } }
  | { name: "explanation_viewed"; scenario_id: string; props: { plan_id: string; area_id: string } };

export interface ScreenContext {
  aApiBase: string;            // always "/a/v1" in the shell
  token: string;               // send as Authorization: Bearer <token>
  officerId: string;
  role: "dispatcher" | "admin";
  scenarioId: string | null;   // currently selected scenario (shell-owned state)
  onScenarioChange: (scenarioId: string) => void;
  onTrack: (event: TrackEvent) => void;
  onOpenTab: (tab: TabId) => void;
}

export interface SnapshotScreenProps extends ScreenContext {}

export interface DispatchPlanScreenProps extends ScreenContext {
  plan: PlanRecord | null;        // latest plan for scenarioId, or null
  generating: boolean;            // true while the shell is creating a plan
  generateError: string | null;   // human-readable error from the last attempt
  onGeneratePlan: () => void;     // the shell creates a plan with current weights
  actionsSlot: ReactNode;         // B's approve/edit/reject bar; render verbatim above the plan table
}
