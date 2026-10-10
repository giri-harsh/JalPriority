import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HarnessShell } from "../harness/HarnessShell";
import { loadFixturePlan, randomPlanId, resolvePlanSource, solvePlanRecord } from "../harness/planSource";
import { ApiClient, ApiError } from "../screens/api";
import { installFetchMock, metricsFixture, planRecordFixture, requestedPaths } from "../test/support";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("plan source helpers", () => {
  it("resolves VITE_A_HARNESS_PLAN_SOURCE, defaulting to solve", () => {
    expect(resolvePlanSource("fixture")).toBe("fixture");
    expect(resolvePlanSource("solve")).toBe("solve");
    expect(resolvePlanSource(undefined)).toBe("solve");
    expect(resolvePlanSource("nonsense")).toBe("solve");
  });

  it("makes plan ids of the form pln_ + 12 hex", () => {
    const ids = new Set(Array.from({ length: 20 }, randomPlanId));
    expect(ids.size).toBe(20);
    for (const id of ids) expect(id).toMatch(/^pln_[0-9a-f]{12}$/);
  });

  it("loads the contract fixture as an independent copy", () => {
    const first = loadFixturePlan();
    expect(first).toEqual(planRecordFixture);
    first.result.allocations.length = 0;
    expect(loadFixturePlan().result.allocations).toHaveLength(3);
  });

  it("solves with default weights and history-derived debt, then wraps the result in a PlanRecord", async () => {
    const mock = installFetchMock();
    const record = await solvePlanRecord(new ApiClient("/a/v1", "tok"), "scn_sa01_w06", "officer_01");

    const [, solveInit] = mock.mock.calls.find(([input]) => String(input) === "/a/v1/plans/solve")!;
    expect(solveInit?.method).toBe("POST");
    expect((solveInit?.headers as Record<string, string>).Authorization).toBe("Bearer tok");
    expect(JSON.parse(String(solveInit?.body))).toEqual({
      scenario_id: "scn_sa01_w06",
      weights: { urgency: 1, debt: 1, vulnerability: 0.5, travel: 20 },
      debt_by_area: { ar_01: 0, ar_02: 8000, ar_03: 12000 },
    });
    expect(requestedPaths(mock)).toEqual(["/a/v1/scenarios/scn_sa01_w06", "/a/v1/plans/solve"]);

    expect(Object.keys(record).sort()).toEqual(Object.keys(planRecordFixture).sort());
    expect(record.plan_id).toMatch(/^pln_[0-9a-f]{12}$/);
    expect(record.status).toBe("recommended");
    expect(record.created_by).toBe("officer_01");
    expect(record.created_at).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
    expect(record).toMatchObject({
      scenario_id: "scn_sa01_w06",
      service_area_id: "sa_01",
      window_index: 6,
      supersedes_plan_id: null,
      replan_of_plan_id: null,
      disruption: null,
      is_synthetic: true,
      decision: null,
      schema_version: "1.0",
    });
    expect(record.result).toEqual(planRecordFixture.result);
    expect(record.metrics).toEqual(metricsFixture);
  });

  it("surfaces an expired session from the solve call", async () => {
    installFetchMock({ failing: { "/plans/solve": 401 } });
    await expect(solvePlanRecord(new ApiClient("/a/v1", "bad"), "scn_sa01_w06", "officer_01")).rejects.toMatchObject({
      status: 401,
    });
    await expect(solvePlanRecord(new ApiClient("/a/v1", "bad"), "scn_sa01_w06", "officer_01")).rejects.toBeInstanceOf(ApiError);
  });
});

describe("HarnessShell", () => {
  it("shows the synthetic-data badge, two tabs and a token warning when no token is set", () => {
    installFetchMock();
    render(<HarnessShell planSource="fixture" token="" />);
    expect(screen.getByText("SYNTHETIC DATA · SIMULATION RESULTS")).toBeInTheDocument();
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Snapshot", "Plan"]);
    expect(screen.getByRole("note")).toHaveTextContent("VITE_A_DEV_TOKEN");
  });

  it("logs scenario_loaded on-screen and to the console, and switches tabs from the snapshot button", async () => {
    installFetchMock();
    const info = vi.spyOn(console, "info").mockImplementation(() => undefined);
    render(<HarnessShell planSource="fixture" token="tok" />);
    const log = screen.getByRole("complementary", { name: "Tracked events" });
    await waitFor(() => expect(log).toHaveTextContent("scenario_loaded scn_sa01_w06"));
    expect(log).toHaveTextContent('"n_areas":3');
    expect(info).toHaveBeenCalledWith("[onTrack]", expect.objectContaining({ name: "scenario_loaded" }));

    await userEvent.setup().click(await screen.findByRole("button", { name: /Go to dispatch plan/ }));
    expect(screen.getByRole("tab", { name: "Plan" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("No dispatch plan yet")).toBeInTheDocument();
  });

  it("fixture mode: Generate plan shows the spinner, then the contract plan with disabled placeholder actions", async () => {
    installFetchMock();
    vi.spyOn(console, "info").mockImplementation(() => undefined);
    const user = userEvent.setup();
    render(<HarnessShell planSource="fixture" token="tok" />);
    await user.click(screen.getByRole("tab", { name: "Plan" }));
    await user.click(screen.getByRole("button", { name: "Generate plan" }));
    expect(screen.getByRole("status")).toHaveTextContent("Generating plan");

    const table = await screen.findByTestId("plan-table", undefined, { timeout: 3000 });
    expect(within(table).getAllByRole("button", { name: "Why?" })).toHaveLength(3);
    const slot = screen.getByTestId("actions-slot");
    const buttons = within(slot).getAllByRole("button");
    expect(buttons.map((button) => button.textContent)).toEqual(["Approve (B)", "Edit weights (B)", "Reject (B)"]);
    for (const button of buttons) expect(button).toBeDisabled();

    await user.click(within(table).getAllByRole("button", { name: "Why?" })[0]!);
    expect(screen.getByRole("complementary", { name: "Tracked events" })).toHaveTextContent("explanation_viewed scn_sa01_w06");
  });

  it("solve mode: Generate plan posts to /plans/solve and renders the returned plan", async () => {
    const mock = installFetchMock();
    vi.spyOn(console, "info").mockImplementation(() => undefined);
    const user = userEvent.setup();
    render(<HarnessShell planSource="solve" token="tok" />);
    await user.click(screen.getByRole("tab", { name: "Plan" }));
    await user.click(screen.getByRole("button", { name: "Generate plan" }));
    await screen.findByTestId("plan-table");
    expect(requestedPaths(mock)).toContain("/a/v1/plans/solve");
    expect(screen.getByTestId("plan-header")).toHaveTextContent(/pln_[0-9a-f]{12}/);
  });

  it("solve mode: a failed solve is shown inline as the generate error", async () => {
    installFetchMock({ failing: { "/plans/solve": 500 } });
    vi.spyOn(console, "info").mockImplementation(() => undefined);
    const user = userEvent.setup();
    render(<HarnessShell planSource="solve" token="tok" />);
    await user.click(screen.getByRole("tab", { name: "Plan" }));
    await user.click(screen.getByRole("button", { name: "Generate plan" }));
    expect(await screen.findByText("Something broke on the server.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate plan" })).toBeEnabled();
  });

  it("solve mode: an expired session is reported with the session-expired wording", async () => {
    installFetchMock({ failing: { "/plans/solve": 401 } });
    vi.spyOn(console, "info").mockImplementation(() => undefined);
    const user = userEvent.setup();
    render(<HarnessShell planSource="solve" token="bad" />);
    await user.click(screen.getByRole("tab", { name: "Plan" }));
    await user.click(screen.getByRole("button", { name: "Generate plan" }));
    await waitFor(() =>
      expect(screen.getAllByRole("alert").some((alert) => alert.textContent === "Session expired, please log in again")).toBe(true),
    );
  });

  it("starts without a scenario when told to and prompts for one", async () => {
    installFetchMock();
    vi.spyOn(console, "info").mockImplementation(() => undefined);
    render(<HarnessShell planSource="fixture" token="tok" initialScenarioId={null} />);
    expect(screen.getByText("No scenario selected")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("tab", { name: "Plan" }));
    await userEvent.setup().click(screen.getByRole("button", { name: "Go to Crisis snapshot" }));
    expect(screen.getByRole("tab", { name: "Snapshot" })).toHaveAttribute("aria-selected", "true");
  });
});
