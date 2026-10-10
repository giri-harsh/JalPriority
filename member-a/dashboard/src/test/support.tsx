import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { vi } from "vitest";
import type {
  DispatchPlanScreenProps,
  Metrics,
  PlanRecord,
  PlanResult,
  SnapshotScreenProps,
} from "../contract/types";
import type { ModelInfo, PredictionSet, Scenario, ScenarioSummary } from "../screens/apiTypes";

const HERE = dirname(fileURLToPath(import.meta.url));
/** member-a/tests/fixtures, holding the CONTRACT example payloads. */
const FIXTURES = resolve(HERE, "../../../tests/fixtures");

function readJson<T>(fileName: string): T {
  return JSON.parse(readFileSync(resolve(FIXTURES, fileName), "utf-8")) as T;
}

export const scenarioFixture = readJson<Scenario>("contract_scenario_example.json");
export const predictionFixture = readJson<PredictionSet>("contract_prediction_example.json");
export const modelFixture = readJson<ModelInfo>("contract_model_info_example.json");
export const summaryFixture = readJson<ScenarioSummary>("contract_scenario_summary_example.json");
export const metricsFixture = readJson<Metrics>("contract_metrics_example.json");
export const planRecordFixture = readJson<PlanRecord>("contract_plan_record_example.json");
/** The frozen contract, two levels above member-a/dashboard. */
export const contractMarkdownPath = resolve(HERE, "../../../../contracts/CONTRACT.md");

export function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export interface FetchMockOptions {
  /** Answer every request with 401 and the contract error envelope. */
  unauthorized?: boolean;
  /** Paths (after the base) that answer with the given HTTP status instead of their payload. */
  failing?: Record<string, number>;
  scenarios?: ScenarioSummary[];
  /** Scenario served for every scenario id (defaults to the contract example). */
  scenario?: Scenario;
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function errorEnvelope(status: number): Response {
  const code = status === 401 ? "UNAUTHORIZED" : "INTERNAL_ERROR";
  const message = status === 401 ? "Missing or invalid token." : "Something broke on the server.";
  return jsonResponse({ error: { code, message, details: {}, request_id: "req_51c0ffee0001" } }, status);
}

/** Replaces global fetch with a router that serves the contract fixtures under `/a/v1`. */
export function installFetchMock(options: FetchMockOptions = {}) {
  const mock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit): Promise<Response> => {
    const url = String(input);
    const path = url.replace(/^\/a\/v1/, "");
    if (options.unauthorized) return errorEnvelope(401);
    const failure = options.failing?.[path];
    if (failure) return errorEnvelope(failure);

    if (path === "/scenarios") return jsonResponse({ scenarios: options.scenarios ?? [summaryFixture] });
    if (path === "/model") return jsonResponse(modelFixture);
    if (path === "/plans/solve") return jsonResponse(clone(planRecordFixture.result));
    const predictions = /^\/scenarios\/([^/]+)\/predictions$/.exec(path);
    if (predictions?.[1]) return jsonResponse({ ...clone(predictionFixture), scenario_id: decodeURIComponent(predictions[1]) });
    const scenario = /^\/scenarios\/([^/]+)$/.exec(path);
    if (scenario?.[1]) return jsonResponse({ ...clone(options.scenario ?? scenarioFixture), scenario_id: decodeURIComponent(scenario[1]) });
    return jsonResponse({ error: { code: "NOT_FOUND", message: "No such route", details: {}, request_id: "req_51c0ffee0001" } }, 404);
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

export function requestedPaths(mock: ReturnType<typeof installFetchMock>): string[] {
  return mock.mock.calls.map(([input]) => String(input));
}

export function snapshotProps(overrides: Partial<SnapshotScreenProps> = {}): SnapshotScreenProps {
  return {
    aApiBase: "/a/v1",
    token: "test-token",
    officerId: "officer_01",
    role: "dispatcher",
    scenarioId: "scn_sa01_w06",
    onScenarioChange: vi.fn(),
    onTrack: vi.fn(),
    onOpenTab: vi.fn(),
    ...overrides,
  };
}

export function planProps(overrides: Partial<DispatchPlanScreenProps> = {}): DispatchPlanScreenProps {
  return {
    ...snapshotProps(),
    plan: clone(planRecordFixture),
    generating: false,
    generateError: null,
    onGeneratePlan: vi.fn(),
    actionsSlot: <button type="button">Approve (slot)</button>,
    ...overrides,
  };
}

/** A plan with `n` areas (ar_01..), 4 tankers and a few unmet areas, plus the matching scenario. */
export function generatePlan(n: number): { plan: PlanRecord; scenario: Scenario } {
  const scenario = clone(scenarioFixture);
  scenario.areas = Array.from({ length: n }, (_, i) => {
    const id = `ar_${String(i + 1).padStart(2, "0")}`;
    const base = clone(scenarioFixture.areas[i % scenarioFixture.areas.length]!);
    return { ...base, area_id: id, name: `Ward ${String(i + 1).padStart(2, "0")} (synthetic)`, lat: 28.67 + i * 0.004, lon: 77.45 + (i % 4) * 0.01 };
  });
  scenario.tankers = Array.from({ length: 4 }, (_, i) => ({
    tanker_id: `tk_0${i + 1}`,
    capacity_litres: 10000,
    status: "available" as const,
    max_route_km: 60,
  }));

  const template = planRecordFixture.result.allocations[0]!;
  const allocations = scenario.areas.map((area, i) => {
    const shortfall = 4000 + i * 700;
    const unmet = i >= n - 3 ? 1500 : 0;
    const allocated = shortfall - unmet;
    const tanker = `tk_0${(i % 4) + 1}`;
    return {
      ...clone(template),
      area_id: area.area_id,
      rank: i + 1,
      predicted_shortfall_litres: shortfall,
      allocated_litres: allocated,
      unmet_litres: unmet,
      coverage_ratio: Number((allocated / shortfall).toFixed(4)),
      is_unserved_remainder: unmet > 0,
      deliveries: [{ tanker_id: tanker, stop_order: Math.floor(i / 4) + 1, litres: allocated }],
      reason_text: `Area ${area.area_id} reason.`,
    };
  });
  const routes = scenario.tankers.map((tanker, k) => {
    const stops = allocations
      .filter((a) => a.deliveries[0]!.tanker_id === tanker.tanker_id)
      .map((a) => ({ stop_order: a.deliveries[0]!.stop_order, area_id: a.area_id, litres: a.allocated_litres }));
    return {
      tanker_id: tanker.tanker_id,
      status: "assigned" as const,
      capacity_litres: 10000,
      max_route_km: 60,
      load_litres: stops.reduce((sum, s) => sum + s.litres, 0),
      route_km: 20 + k,
      stops,
      path: [scenario.depot.depot_id, ...stops.map((s) => s.area_id), scenario.depot.depot_id],
    };
  });
  const result: PlanResult = {
    ...clone(planRecordFixture.result),
    allocations,
    routes,
    unserved: allocations
      .filter((a) => a.unmet_litres > 0)
      .map((a) => ({ area_id: a.area_id, unmet_litres: a.unmet_litres, reason_code: "SUPPLY_EXHAUSTED" as const })),
  };
  return { plan: { ...clone(planRecordFixture), result }, scenario };
}
