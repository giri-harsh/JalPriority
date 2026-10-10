import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DispatchPlanScreen } from "../screens";
import { generatePlan, installFetchMock, planProps } from "../test/support";

// Target: a 10-area plan renders in under 300 ms once its props arrive. The first-paint check
// keeps that budget. The check that includes the Leaflet map (fetch, layers, tooltips) gets a
// generous 1 s for jsdom and slow CI machines; it takes around 150 ms on a developer laptop.
const FIRST_PAINT_BUDGET_MS = 300;
const WITH_MAP_BUDGET_MS = 1000;

describe("DispatchPlanScreen performance", () => {
  it("renders a generated 10-area plan within budget, then adds the map when coordinates arrive", async () => {
    const { plan, scenario } = generatePlan(10);
    installFetchMock({ scenario });
    expect(plan.result.allocations).toHaveLength(10);

    // Warm-up render so module and JIT start-up cost is not charged to the measurement.
    render(<DispatchPlanScreen {...planProps({ plan })} />).unmount();

    const started = performance.now();
    render(<DispatchPlanScreen {...planProps({ plan })} />);
    const firstPaint = performance.now() - started;
    expect(screen.getAllByRole("button", { name: "Why?" })).toHaveLength(10);
    expect(screen.getByTestId("tanker-panel")).toBeInTheDocument();
    expect(screen.getByTestId("unserved-panel")).toBeInTheDocument();
    expect(firstPaint).toBeLessThan(FIRST_PAINT_BUDGET_MS);

    await screen.findByTestId("map-frame");
    const withMap = performance.now() - started;
    expect(screen.getByTestId("map-frame").querySelectorAll(".leaflet-overlay-pane path").length).toBeGreaterThan(10);
    expect(within(screen.getByTestId("plan-table")).getByText("Ward 10 (synthetic)")).toBeInTheDocument();
    expect(withMap).toBeLessThan(WITH_MAP_BUDGET_MS);
  });

  it("marks the generated plan's last three areas as unserved", async () => {
    const { plan, scenario } = generatePlan(10);
    installFetchMock({ scenario });
    render(<DispatchPlanScreen {...planProps({ plan })} />);
    await screen.findByTestId("map-frame");
    expect(screen.getByTestId("plan-table").querySelectorAll('tr[data-unserved="true"]')).toHaveLength(3);
    expect(screen.getByTestId("unserved-panel").querySelectorAll('li[data-unserved="true"]')).toHaveLength(3);
  });
});
