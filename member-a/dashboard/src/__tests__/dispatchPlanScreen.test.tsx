import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DispatchPlanScreen } from "../screens";
import { SESSION_EXPIRED_MESSAGE } from "../screens/api";
import { clone, installFetchMock, planProps, planRecordFixture, requestedPaths } from "../test/support";

async function renderPlan(overrides = {}) {
  const props = planProps(overrides);
  const view = render(<DispatchPlanScreen {...props} />);
  // Wait until the scenario (coordinates and names) has been fetched.
  await screen.findByTestId("map-frame");
  return { props, ...view };
}

describe("DispatchPlanScreen with a plan", () => {
  it("shows the header with strategy, solver status, solve time, totals and plan status", async () => {
    installFetchMock();
    await renderPlan();
    const header = screen.getByTestId("plan-header");
    expect(within(header).getByTestId("plan-strategy")).toHaveTextContent("JalPriority");
    expect(within(header).getByTestId("solver-status")).toHaveTextContent("optimal");
    expect(within(header).getByText("412 ms")).toBeInTheDocument();
    expect(within(header).getByText("20,000 L / 20,000 L")).toBeInTheDocument();
    expect(within(header).getByTestId("total-unmet")).toHaveTextContent("9,000 L");
    expect(within(header).getByTestId("plan-status")).toHaveAttribute("data-plan-status", "recommended");
  });

  it.each(["time_limited", "heuristic"] as const)("flags solver status %s as a warning", async (status) => {
    installFetchMock();
    const plan = clone(planRecordFixture);
    plan.result.solver.status = status;
    await renderPlan({ plan });
    const badge = screen.getByTestId("solver-status");
    expect(badge).toHaveAttribute("data-solver-status", status);
    expect(badge.className).toMatch(/toneWarn/);
  });

  it("shows an optimal solver status as ok, not as a warning", async () => {
    installFetchMock();
    await renderPlan();
    expect(screen.getByTestId("solver-status").className).not.toMatch(/toneWarn/);
  });

  it("renders the actions slot verbatim, directly above the table", async () => {
    installFetchMock();
    await renderPlan({ actionsSlot: <button type="button">Approve (slot)</button> });
    const slot = screen.getByTestId("actions-slot");
    expect(within(slot).getByRole("button", { name: "Approve (slot)" })).toBeInTheDocument();
    const tableSection = screen.getByTestId("plan-table-section");
    expect(slot.nextElementSibling).toBe(tableSection);
  });

  it("lists every allocation in rank order with priority, litres and coverage", async () => {
    installFetchMock();
    await renderPlan();
    const rows = within(screen.getByTestId("plan-table")).getAllByRole("row").filter((row) => row.hasAttribute("data-area-id"));
    expect(rows.map((row) => row.getAttribute("data-area-id"))).toEqual(["ar_03", "ar_02", "ar_01"]);
    const third = within(rows[2]!);
    expect(third.getByText("1.41")).toBeInTheDocument();
    expect(third.getByText("14,000 L")).toBeInTheDocument();
    expect(third.getByText("5,000 L")).toBeInTheDocument();
    expect(third.getByText("36%")).toBeInTheDocument();
    expect(third.getByRole("listitem")).toHaveTextContent("tk_01 · stop 2 · 5,000 L");
  });

  it("resolves area names from the scenario once it is loaded", async () => {
    installFetchMock();
    await renderPlan();
    expect(await within(screen.getByTestId("plan-table")).findByText("Ward 03 (synthetic)")).toBeInTheDocument();
  });

  it("marks unserved rows with a data attribute and a danger class, and only those rows", async () => {
    installFetchMock();
    await renderPlan();
    const table = screen.getByTestId("plan-table");
    const unserved = table.querySelectorAll('tr[data-unserved="true"]');
    expect(unserved).toHaveLength(1);
    expect(unserved[0]).toHaveAttribute("data-area-id", "ar_01");
    expect(unserved[0]!.className).toMatch(/danger/i);
    const served = table.querySelector('tr[data-area-id="ar_03"]')!;
    expect(served).not.toHaveAttribute("data-unserved");
    expect(served.className).not.toMatch(/danger/i);
    expect(within(unserved[0] as HTMLElement).getByTestId("unmet-cell").className).toMatch(/danger/i);
    expect(within(served as HTMLElement).getByTestId("unmet-cell").className).not.toMatch(/danger/i);
  });

  it("emits explanation_viewed with the exact shape when a Why? expander opens", async () => {
    installFetchMock();
    const { props } = await renderPlan();
    const user = userEvent.setup();
    const buttons = screen.getAllByRole("button", { name: "Why?" });
    expect(buttons).toHaveLength(3);
    expect(props.onTrack).not.toHaveBeenCalled();

    await user.click(buttons[2]!);
    expect(props.onTrack).toHaveBeenCalledTimes(1);
    expect(props.onTrack).toHaveBeenCalledWith({
      name: "explanation_viewed",
      scenario_id: "scn_sa01_w06",
      props: { plan_id: "pln_4b8f2a91c0de", area_id: "ar_01" },
    });
    const reasonText = planRecordFixture.result.allocations[2]!.reason_text;
    expect(screen.getByTestId("why-ar_01")).toHaveTextContent(reasonText);
    expect(screen.getByTestId("why-ar_01").querySelector('[data-reason-code="PARTIALLY_SERVED"]')).toHaveTextContent(
      "Partially served · 5,000 of 14,000 L",
    );
    expect(buttons[2]).toHaveAttribute("aria-expanded", "true");
  });

  it("does not emit when an explanation is collapsed, and emits again when it is re-opened", async () => {
    installFetchMock();
    const { props } = await renderPlan();
    const user = userEvent.setup();
    const button = screen.getAllByRole("button", { name: "Why?" })[0]!;
    await user.click(button);
    await user.click(button);
    expect(screen.queryByTestId("why-ar_03")).not.toBeInTheDocument();
    expect(props.onTrack).toHaveBeenCalledTimes(1);
    await user.click(button);
    expect(props.onTrack).toHaveBeenCalledTimes(2);
  });

  it("always shows the unserved panel in red with the unserved areas and reasons", async () => {
    installFetchMock();
    await renderPlan();
    const panel = screen.getByTestId("unserved-panel");
    expect(panel).toHaveAttribute("data-empty", "false");
    expect(panel.className).toMatch(/danger/i);
    const items = panel.querySelectorAll('li[data-unserved="true"]');
    expect(items).toHaveLength(1);
    expect(items[0]!.className).toMatch(/danger/i);
    expect(items[0]!.querySelector('[data-reason-code="SUPPLY_EXHAUSTED"]')).not.toBeNull();
    expect(panel).toHaveTextContent("9,000 L");
    expect(panel).toHaveTextContent("Water supply exhausted");
  });

  it("shows the unserved panel with a clear message when everything is covered", async () => {
    installFetchMock();
    const plan = clone(planRecordFixture);
    plan.result.unserved = [];
    await renderPlan({ plan });
    const panel = screen.getByTestId("unserved-panel");
    expect(panel).toHaveTextContent("All predicted shortfall covered");
    expect(panel).toHaveAttribute("data-empty", "true");
  });

  it("gives each tanker a briefing: ordered stops, load / capacity and route km / max", async () => {
    installFetchMock();
    await renderPlan();
    const panel = screen.getByTestId("tanker-panel");
    const tk01 = panel.querySelector('[data-tanker-id="tk_01"]') as HTMLElement;
    expect(tk01).toHaveTextContent("11,000 L / 12,000 L");
    expect(tk01).toHaveTextContent("24.4 km / 60.0 km");
    const stops = within(tk01).getAllByRole("listitem");
    expect(stops).toHaveLength(2);
    expect(stops[0]).toHaveTextContent(/1.*6,000 L/);
    expect(stops[1]).toHaveTextContent(/2.*5,000 L/);
    expect(panel.querySelector('[data-tanker-id="tk_03"]')).toHaveAttribute("data-route-status", "unavailable");
  });

  it("fetches the scenario of the plan for coordinates, relative to aApiBase, with the bearer token", async () => {
    const mock = installFetchMock();
    await renderPlan({ aApiBase: "/a/v1/", token: "tok-123" });
    expect(requestedPaths(mock)).toEqual(["/a/v1/scenarios/scn_sa01_w06"]);
    const init = mock.mock.calls[0]![1] as RequestInit;
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok-123");
  });

  it("shows a generating spinner and the error inline above an existing plan", async () => {
    installFetchMock();
    await renderPlan({ generating: true, generateError: "Solver exploded" });
    expect(screen.getByText("Generating plan…")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Solver exploded");
  });
});

describe("DispatchPlanScreen without a plan", () => {
  it("shows an empty state with a primary Generate plan button wired to onGeneratePlan", async () => {
    installFetchMock();
    const props = planProps({ plan: null });
    render(<DispatchPlanScreen {...props} />);
    const button = screen.getByRole("button", { name: "Generate plan" });
    expect(button.className).toMatch(/buttonPrimary/);
    await userEvent.setup().click(button);
    expect(props.onGeneratePlan).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("plan-table")).not.toBeInTheDocument();
  });

  it("shows a spinner and disables the button while generating", () => {
    installFetchMock();
    render(<DispatchPlanScreen {...planProps({ plan: null, generating: true })} />);
    expect(screen.getByRole("status")).toHaveTextContent("Generating plan");
    expect(screen.getByRole("button", { name: "Generate plan" })).toBeDisabled();
  });

  it("shows generateError inline", () => {
    installFetchMock();
    render(<DispatchPlanScreen {...planProps({ plan: null, generateError: "Scenario scn_x does not exist." })} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Scenario scn_x does not exist.");
    expect(screen.getByRole("button", { name: "Generate plan" })).toBeEnabled();
  });

  it("prompts to open the crisis snapshot when no scenario is selected", async () => {
    const mock = installFetchMock();
    const props = planProps({ plan: null, scenarioId: null });
    render(<DispatchPlanScreen {...props} />);
    expect(screen.getByText("No scenario selected")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Generate plan" })).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Go to Crisis snapshot" }));
    expect(props.onOpenTab).toHaveBeenCalledWith("snapshot");
    expect(mock).not.toHaveBeenCalled();
  });
});

describe("DispatchPlanScreen error handling", () => {
  it("shows the session-expired message inline on a 401 and keeps the plan visible", async () => {
    installFetchMock({ unauthorized: true });
    const props = planProps();
    render(<DispatchPlanScreen {...props} />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(SESSION_EXPIRED_MESSAGE);
    expect(alert).toHaveAttribute("data-error-kind", "session-expired");
    expect(screen.getByTestId("plan-table")).toBeInTheDocument();
    expect(screen.getByTestId("unserved-panel")).toBeInTheDocument();
  });

  it("shows other API failures inline without crashing", async () => {
    installFetchMock({ failing: { "/scenarios/scn_sa01_w06": 500 } });
    render(<DispatchPlanScreen {...planProps()} />);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Something broke on the server."));
    expect(screen.getByTestId("plan-table")).toBeInTheDocument();
  });

  it("does not call onTrack or onOpenTab on mount", async () => {
    installFetchMock();
    const { props } = await renderPlan();
    expect(props.onTrack).not.toHaveBeenCalled();
    expect(props.onOpenTab).not.toHaveBeenCalled();
    expect(vi.isMockFunction(props.onGeneratePlan)).toBe(true);
  });
});
