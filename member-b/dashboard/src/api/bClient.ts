import {
  PlanRecord,
  PlanResult,
  Metrics,
  Weights,
  TrackEvent,
} from "../contract/types";

export interface LoginResult {
  token: string;
  officer_id: string;
  role: "dispatcher" | "admin";
  expires_at: string;
}

export interface ScenarioSummary {
  scenario_id: string;
  service_area_id: string;
  window_index: number;
  n_areas: number;
  supply_litres: number;
  tankers_available: number;
  alert_level: number;
}

export interface ScenarioTanker {
  tanker_id: string;
  capacity_litres: number;
  max_route_km: number;
  is_available: boolean;
}

export interface ScenarioHeat {
  alert_level: number;
  max_temp_c: number;
  heat_index_c: number;
}

export interface ScenarioArea {
  area_id: string;
  name: string;
  requested_litres: number;
  expected_piped_litres: number;
  vulnerability_index: number;
  vulnerability_available: boolean;
  request_time: string;
}

export interface ScenarioDetails {
  scenario_id: string;
  parent_scenario_id?: string | null;
  service_area_id: string;
  service_area_name: string;
  window_index: number;
  window_start: string;
  window_hours: number;
  is_synthetic: boolean;
  seed: number;
  supply_litres: number;
  heat: ScenarioHeat;
  tankers: ScenarioTanker[];
  areas: ScenarioArea[];
}

export interface DecisionResponse {
  plan: PlanRecord;
  next_plan: PlanRecord | null;
  ledger_updated: boolean;
}

export interface WeightsResponse {
  service_area_id: string;
  weights: Weights;
  updated_at: string;
  updated_by: string;
  flags: {
    fairness_weights: boolean;
    vulnerability: boolean;
    replan: boolean;
  };
  defaults: Weights;
  debt_policy: {
    decay: number;
    cap_litres: number | null;
  };
}

export interface LedgerSeriesPoint {
  window_index: number;
  debt_litres: number;
  unmet_litres: number;
  source: "history" | "approved_plan";
  plan_id: string | null;
}

export interface LedgerArea {
  area_id: string;
  name: string;
  current_debt_litres: number;
  series: LedgerSeriesPoint[];
}

export interface LedgerTotalPoint {
  window_index: number;
  debt_litres: number;
}

export interface LedgerResponse {
  service_area_id: string;
  decay: number;
  cap_litres: number | null;
  windows: number[];
  areas: LedgerArea[];
  totals: LedgerTotalPoint[];
}

export interface ComparisonResponse {
  schema_version: string;
  scenario_id: string;
  weights: Weights;
  generated_at: string;
  is_simulation: boolean;
  plan_id: string;
  results: Record<string, PlanResult>;
  metrics: Record<string, Metrics>;
  best_by_metric: Record<string, string>;
}

export interface AreaDiff {
  area_id: string;
  litres_before: number;
  litres_after: number;
  delta: number;
  tankers_before: string[];
  tankers_after: string[];
  order_before: number[];
  order_after: number[];
  change: "increased" | "decreased" | "unchanged" | "newly_served" | "dropped" | "reassigned";
}

export interface DiffSummary {
  areas_changed: number;
  litres_moved: number;
  metrics_before: Metrics;
  metrics_after: Metrics;
}

export interface ReplanDiff {
  areas: AreaDiff[];
  summary: DiffSummary;
}

export interface ReplanResponse {
  schema_version: string;
  previous_plan_id: string;
  derived_scenario_id: string;
  disruption: {
    type: "tanker_down" | "supply_cut" | "heat_alert";
    params: Record<string, unknown>;
  };
  replanning_time_ms: number;
  new_plan: PlanRecord;
  diff: ReplanDiff;
}

export class APIClientError extends Error {
  code: string;
  status: number;
  details?: Record<string, unknown>;

  constructor(message: string, code: string, status: number, details?: Record<string, unknown>) {
    super(message);
    this.name = "APIClientError";
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

type UnauthorizedHandler = () => void;

class BClient {
  private onUnauthorized: UnauthorizedHandler | null = null;

  setUnauthorizedHandler(handler: UnauthorizedHandler | null) {
    this.onUnauthorized = handler;
  }

  private async request<T>(
    endpoint: string,
    options: RequestInit = {},
    token?: string | null
  ): Promise<T> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...(options.headers as Record<string, string>),
    };

    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }

    const res = await fetch(endpoint, {
      ...options,
      headers,
    });

    if (res.status === 401) {
      if (this.onUnauthorized) {
        this.onUnauthorized();
      }
      throw new APIClientError("Session expired or unauthorized", "UNAUTHORIZED", 401);
    }

    let data: unknown;
    try {
      data = await res.json();
    } catch {
      data = null;
    }

    if (!res.ok) {
      const errObj = (data as { error?: { code?: string; message?: string; details?: Record<string, unknown> } })?.error;
      const code = errObj?.code || "HTTP_ERROR";
      const message = errObj?.message || `HTTP ${res.status}`;
      const details = errObj?.details;
      throw new APIClientError(message, code, res.status, details);
    }

    return data as T;
  }

  async login(username: string, password: string): Promise<LoginResult> {
    return this.request<LoginResult>("/b/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
  }

  async getMe(token: string): Promise<{ officer_id: string; role: "dispatcher" | "admin"; expires_at: string }> {
    return this.request<{ officer_id: string; role: "dispatcher" | "admin"; expires_at: string }>(
      "/b/v1/auth/me",
      { method: "GET" },
      token
    );
  }

  async listScenarios(token: string): Promise<{ scenarios: ScenarioSummary[] }> {
    return this.request<{ scenarios: ScenarioSummary[] }>(
      "/b/v1/scenarios",
      { method: "GET" },
      token
    );
  }

  async getLatestPlan(token: string, scenarioId: string): Promise<PlanRecord | null> {
    const res = await this.request<{ plans: PlanRecord[] }>(
      `/b/v1/plans?scenario_id=${encodeURIComponent(scenarioId)}&latest=true`,
      { method: "GET" },
      token
    );
    return res.plans && res.plans.length > 0 ? res.plans[0] : null;
  }

  async generatePlan(
    token: string,
    scenarioId: string,
    weights?: Weights | null
  ): Promise<PlanRecord> {
    const body: Record<string, unknown> = { scenario_id: scenarioId };
    if (weights) {
      body.weights = weights;
    }
    return this.request<PlanRecord>(
      "/b/v1/plans",
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      token
    );
  }

  async recordDecision(
    token: string,
    planId: string,
    decision: "approved" | "edited" | "rejected",
    note?: string | null,
    revisedWeights?: Weights | null
  ): Promise<DecisionResponse> {
    const body: Record<string, unknown> = { decision };
    if (note !== undefined) {
      body.note = note;
    }
    if (revisedWeights !== undefined) {
      body.revised_weights = revisedWeights;
    }
    return this.request<DecisionResponse>(
      `/b/v1/plans/${encodeURIComponent(planId)}/decision`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      token
    );
  }

  async trackEvent(token: string, event: TrackEvent): Promise<{ event_id: string }> {
    return this.request<{ event_id: string }>(
      "/b/v1/events",
      {
        method: "POST",
        body: JSON.stringify({
          name: event.name,
          scenario_id: event.scenario_id,
          props: event.props,
        }),
      },
      token
    );
  }

  async trackAnyEvent(
    token: string,
    name: string,
    scenarioId?: string | null,
    props: Record<string, unknown> = {}
  ): Promise<{ event_id: string }> {
    return this.request<{ event_id: string }>(
      "/b/v1/events",
      {
        method: "POST",
        body: JSON.stringify({
          name,
          scenario_id: scenarioId || null,
          props,
        }),
      },
      token
    );
  }

  async getWeights(token: string, serviceAreaId: string): Promise<WeightsResponse> {
    return this.request<WeightsResponse>(
      `/b/v1/weights?service_area_id=${encodeURIComponent(serviceAreaId)}`,
      { method: "GET" },
      token
    );
  }

  async updateWeights(
    token: string,
    serviceAreaId: string,
    weights: Weights
  ): Promise<WeightsResponse> {
    return this.request<WeightsResponse>(
      "/b/v1/weights",
      {
        method: "PUT",
        body: JSON.stringify({
          service_area_id: serviceAreaId,
          weights,
        }),
      },
      token
    );
  }

  async getLedger(
    token: string,
    scenarioId?: string | null,
    serviceAreaId?: string | null
  ): Promise<LedgerResponse> {
    const params = new URLSearchParams();
    if (scenarioId) params.set("scenario_id", scenarioId);
    if (serviceAreaId) params.set("service_area_id", serviceAreaId);
    return this.request<LedgerResponse>(
      `/b/v1/ledger?${params.toString()}`,
      { method: "GET" },
      token
    );
  }

  async runComparison(
    token: string,
    scenarioId: string,
    weights?: Weights | null
  ): Promise<ComparisonResponse> {
    const body: Record<string, unknown> = { scenario_id: scenarioId };
    if (weights) {
      body.weights = weights;
    }
    return this.request<ComparisonResponse>(
      "/b/v1/comparisons",
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      token
    );
  }

  async replan(
    token: string,
    planId: string,
    disruption: {
      type: "tanker_down" | "supply_cut" | "heat_alert";
      params: Record<string, unknown>;
    }
  ): Promise<ReplanResponse> {
    return this.request<ReplanResponse>(
      "/b/v1/replans",
      {
        method: "POST",
        body: JSON.stringify({
          plan_id: planId,
          disruption,
        }),
      },
      token
    );
  }

  async getScenario(token: string, scenarioId: string): Promise<ScenarioDetails> {
    return this.request<ScenarioDetails>(
      `/b/v1/scenarios/${encodeURIComponent(scenarioId)}`,
      { method: "GET" },
      token
    );
  }
}

export const bClient = new BClient();
