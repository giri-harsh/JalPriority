import type { Metrics, PlanResult, Weights } from "../contract/types";
import type { HistoryWindow, Scenario } from "../screens/apiTypes";

/** Policy defaults the harness stands in for (the real shell takes them from B). */
export const DEFAULT_WEIGHTS: Weights = { urgency: 1.0, debt: 1.0, vulnerability: 0.5, travel: 20.0 };

/** Debt decay lambda (CONTRACT 5.11, F-10). */
export const DEBT_DECAY = 0.3;

/** Round half to even, like Python's round(), so the harness matches B's ledger. */
export function roundHalfEven(value: number): number {
  const floor = Math.floor(value);
  const diff = value - floor;
  if (diff < 0.5) return floor;
  if (diff > 0.5) return floor + 1;
  return floor % 2 === 0 ? floor : floor + 1;
}

/** F-10 for one area and one window: `D' = round((1 - lambda) * D + unmet)`. */
export function nextDebt(debt: number, unmet: number, decay: number = DEBT_DECAY): number {
  return roundHalfEven((1 - decay) * debt + unmet);
}

/** Debt of one area after replaying its history from zero (unmet = max(0, need - received)). */
export function debtFromHistory(history: readonly HistoryWindow[], decay: number = DEBT_DECAY): number {
  return [...history]
    .sort((a, b) => a.window_index - b.window_index)
    .reduce((debt, window) => nextDebt(debt, Math.max(0, window.need_litres - window.received_litres), decay), 0);
}

/** `debt_by_area` for a solve request, derived from the scenario history. */
export function debtByAreaFromScenario(scenario: Scenario, decay: number = DEBT_DECAY): Record<string, number> {
  return Object.fromEntries(scenario.areas.map((area) => [area.area_id, debtFromHistory(area.history, decay)]));
}

function roundTo(value: number, decimals: number): number {
  return Number(value.toFixed(decimals));
}

/** F-15: number of constraint violations in a plan. */
export function countViolations(result: PlanResult): number {
  const { totals } = result;
  let violations = 0;
  const allocated = result.allocations.reduce((sum, a) => sum + a.allocated_litres, 0);
  if (allocated > totals.supply_available_litres) violations += 1;
  for (const route of result.routes) {
    if (route.load_litres > route.capacity_litres) violations += 1;
    if (route.route_km > route.max_route_km + 0.01) violations += 1;
  }
  const unavailable = new Set(result.routes.filter((r) => r.status === "unavailable").map((r) => r.tanker_id));
  for (const allocation of result.allocations) {
    violations += allocation.deliveries.filter((delivery) => unavailable.has(delivery.tanker_id)).length;
  }
  return violations;
}

/**
 * The seven plan metrics (CONTRACT 5.7) for a JalPriority result, using F-9 and F-11 to F-15.
 * `debtByArea` is the input debt of the plan; missing areas count as zero.
 */
export function computeMetrics(result: PlanResult, debtByArea: Readonly<Record<string, number>>, decay: number = DEBT_DECAY): Metrics {
  const { allocations, routes } = result;
  const needy = allocations.filter((a) => a.predicted_shortfall_litres > 0);
  return {
    schema_version: "1.0",
    priority_weighted_unmet: roundTo(
      allocations.reduce((sum, a) => sum + a.priority_weight * a.unmet_litres, 0),
      1,
    ),
    deprivation_debt_remaining_litres: allocations.reduce(
      (sum, a) => sum + nextDebt(debtByArea[a.area_id] ?? 0, a.unmet_litres, decay),
      0,
    ),
    total_unmet_litres: allocations.reduce((sum, a) => sum + a.unmet_litres, 0),
    min_coverage_ratio: needy.length === 0 ? 1.0 : Math.min(...needy.map((a) => a.coverage_ratio)),
    total_travel_km: roundTo(
      routes.reduce((sum, r) => sum + r.route_km, 0),
      2,
    ),
    constraint_violations: countViolations(result),
    replanning_time_ms: null,
  };
}
