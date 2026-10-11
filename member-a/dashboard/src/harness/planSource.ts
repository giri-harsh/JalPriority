import type { PlanRecord, PlanResult } from "../contract/types";
import type { ApiClient } from "../screens/api";
import fixtureRecord from "./fixtures/contract_plan_record_example.json";
import { computeMetrics, debtByAreaFromScenario, DEFAULT_WEIGHTS } from "./metrics";

/** `VITE_A_HARNESS_PLAN_SOURCE`: where "Generate plan" gets its plan from. */
export type PlanSourceKind = "solve" | "fixture";

export function resolvePlanSource(value: string | undefined): PlanSourceKind {
  return value === "fixture" ? "fixture" : "solve";
}

/** `pln_` followed by 12 random hex characters (CONTRACT 4.2). */
export function randomPlanId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(6));
  return `pln_${Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
}

/** The PlanRecord the contract example describes, as a fresh copy. */
export function loadFixturePlan(): PlanRecord {
  return structuredClone(fixtureRecord) as unknown as PlanRecord;
}

/**
 * Plays the part of B's plan creation: solves with default weights and the debt derived
 * from the scenario history, then wraps the PlanResult in a PlanRecord with its metrics.
 */
export async function solvePlanRecord(client: ApiClient, scenarioId: string, officerId: string): Promise<PlanRecord> {
  const scenario = await client.getScenario(scenarioId);
  const debtByArea = debtByAreaFromScenario(scenario);
  const result = await client.post<PlanResult>("/plans/solve", {
    scenario_id: scenarioId,
    weights: DEFAULT_WEIGHTS,
    debt_by_area: debtByArea,
  });
  return {
    schema_version: "1.0",
    plan_id: randomPlanId(),
    status: "recommended",
    created_at: new Date().toISOString(),
    created_by: officerId,
    scenario_id: result.scenario_id,
    service_area_id: result.service_area_id,
    window_index: result.window_index,
    supersedes_plan_id: null,
    replan_of_plan_id: null,
    disruption: null,
    is_synthetic: true,
    result,
    metrics: computeMetrics(result, debtByArea),
    decision: null,
  };
}
