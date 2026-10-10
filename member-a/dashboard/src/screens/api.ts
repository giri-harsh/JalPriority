import type { ModelInfo, PredictionSet, Scenario, ScenarioSummary } from "./apiTypes";

export const SESSION_EXPIRED_MESSAGE = "Session expired, please log in again";

/** Error raised for any failed call to A's API. `status` is 0 when the server was unreachable. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(status: number, message: string, code: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }

  get sessionExpired(): boolean {
    return this.status === 401;
  }
}

/** Text the screens show for a failed request. */
export function describeError(error: unknown): string {
  if (error instanceof ApiError && error.sessionExpired) return SESSION_EXPIRED_MESSAGE;
  if (error instanceof Error && error.message) return error.message;
  return "Unexpected error";
}

export function isSessionExpired(error: unknown): boolean {
  return error instanceof ApiError && error.sessionExpired;
}

async function errorFromResponse(response: Response): Promise<ApiError> {
  let message = `Request failed (HTTP ${response.status})`;
  let code: string | null = null;
  try {
    const body: unknown = await response.json();
    const envelope = (body as { error?: { code?: unknown; message?: unknown } } | null)?.error;
    if (typeof envelope?.message === "string") message = envelope.message;
    if (typeof envelope?.code === "string") code = envelope.code;
  } catch {
    // Non-JSON error body: keep the generic message.
  }
  return new ApiError(response.status, message, code);
}

/** Minimal JSON client for A's API. Every URL is relative to the `aApiBase` prop. */
export class ApiClient {
  private readonly base: string;
  private readonly token: string;

  constructor(base: string, token: string) {
    this.base = base.replace(/\/+$/, "");
    this.token = token;
  }

  async request<T>(method: "GET" | "POST", path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (this.token) headers.Authorization = `Bearer ${this.token}`;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    let response: Response;
    try {
      response = await fetch(`${this.base}${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal,
      });
    } catch (error) {
      if (signal?.aborted) throw error;
      throw new ApiError(0, "Cannot reach the dispatch API. Check that the service is running.");
    }
    if (!response.ok) throw await errorFromResponse(response);
    return (await response.json()) as T;
  }

  get<T>(path: string, signal?: AbortSignal): Promise<T> {
    return this.request<T>("GET", path, undefined, signal);
  }

  post<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
    return this.request<T>("POST", path, body, signal);
  }

  async listScenarios(signal?: AbortSignal): Promise<ScenarioSummary[]> {
    const payload = await this.get<{ scenarios: ScenarioSummary[] }>("/scenarios", signal);
    return payload.scenarios;
  }

  getScenario(scenarioId: string, signal?: AbortSignal): Promise<Scenario> {
    return this.get<Scenario>(`/scenarios/${encodeURIComponent(scenarioId)}`, signal);
  }

  getPredictions(scenarioId: string, signal?: AbortSignal): Promise<PredictionSet> {
    return this.get<PredictionSet>(`/scenarios/${encodeURIComponent(scenarioId)}/predictions`, signal);
  }

  getModel(signal?: AbortSignal): Promise<ModelInfo> {
    return this.get<ModelInfo>("/model", signal);
  }
}
