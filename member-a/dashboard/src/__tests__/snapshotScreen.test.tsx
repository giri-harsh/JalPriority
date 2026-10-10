import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { describe, expect, it } from "vitest";
import { SnapshotScreen } from "../screens";
import { SESSION_EXPIRED_MESSAGE } from "../screens/api";
import { clone, installFetchMock, modelFixture, requestedPaths, scenarioFixture, snapshotProps, summaryFixture } from "../test/support";

async function renderLoaded(overrides = {}) {
  const props = snapshotProps(overrides);
  const view = render(<SnapshotScreen {...props} />);
  await screen.findByRole("table", { name: "Areas" });
  return { props, ...view };
}

function areaIds(): string[] {
  return within(screen.getByRole("table", { name: "Areas" }))
    .getAllByRole("row")
    .flatMap((row) => (row.getAttribute("data-area-id") ? [row.getAttribute("data-area-id") as string] : []));
}

describe("SnapshotScreen", () => {
  it("loads the scenario, its predictions and the model for the selected scenario", async () => {
    const mock = installFetchMock();
    await renderLoaded();
    expect(requestedPaths(mock).sort()).toEqual(
      ["/a/v1/model", "/a/v1/scenarios", "/a/v1/scenarios/scn_sa01_w06", "/a/v1/scenarios/scn_sa01_w06/predictions"].sort(),
    );
  });

  it("shows the KPI cards: supply, tankers available / total, heat alert badge and coverable share", async () => {
    installFetchMock();
    await renderLoaded();
    expect(screen.getByRole("group", { name: "Tanker water available" })).toHaveTextContent("20,000 L");
    expect(screen.getByRole("group", { name: "Tankers available" })).toHaveTextContent("2 / 3");
    const badge = screen.getByTestId("alert-badge");
    expect(badge).toHaveTextContent("Level 2");
    expect(badge).toHaveAttribute("data-alert-level", "2");
    const shortfall = screen.getByRole("group", { name: "Predicted shortfall" });
    expect(shortfall).toHaveTextContent("29,000 L");
    expect(shortfall).toHaveTextContent("69% of shortfall coverable");
    expect(within(shortfall).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "69");
  });

  it("lists areas by predicted shortfall descending by default, with feature chips and relative delivery time", async () => {
    installFetchMock();
    await renderLoaded();
    expect(areaIds()).toEqual(["ar_01", "ar_02", "ar_03"]);
    const first = screen.getByRole("table", { name: "Areas" }).querySelector('tr[data-area-id="ar_01"]') as HTMLElement;
    expect(first).toHaveTextContent("Ward 01 (synthetic)");
    expect(first).toHaveTextContent("18,000");
    expect(first).toHaveTextContent("8,000 L");
    expect(first).toHaveTextContent("16 h before window");
    expect(first).toHaveTextContent("20,000 L");
    expect(first).toHaveTextContent("54,000 L");
    expect(first).toHaveTextContent("40,000 L");
    expect(first).toHaveTextContent("14,000 L");
    expect(first).toHaveTextContent("heat index +2,300 L");
    expect(first).toHaveTextContent("population +1,900 L");
    expect(first).toHaveTextContent("last need +1,200 L");
    const third = screen.getByRole("table", { name: "Areas" }).querySelector('tr[data-area-id="ar_03"]') as HTMLElement;
    expect(third).toHaveTextContent("No delivery yet");
  });

  it("sorts when a column header is clicked and toggles the direction", async () => {
    installFetchMock();
    await renderLoaded();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /^Population/ }));
    expect(areaIds()).toEqual(["ar_01", "ar_02", "ar_03"]);
    expect(screen.getByRole("columnheader", { name: /Population/ })).toHaveAttribute("aria-sort", "descending");
    await user.click(screen.getByRole("button", { name: /^Population/ }));
    expect(areaIds()).toEqual(["ar_03", "ar_02", "ar_01"]);
    expect(screen.getByRole("columnheader", { name: /Population/ })).toHaveAttribute("aria-sort", "ascending");
    await user.click(screen.getByRole("button", { name: /^Area/ }));
    expect(areaIds()).toEqual(["ar_01", "ar_02", "ar_03"]);
    await user.click(screen.getByRole("button", { name: /^Predicted shortfall/ }));
    expect(areaIds()).toEqual(["ar_01", "ar_02", "ar_03"]);
    await user.click(screen.getByRole("button", { name: /^Predicted shortfall/ }));
    expect(areaIds()).toEqual(["ar_03", "ar_02", "ar_01"]);
  });

  it("shows the model panel with both models' MAE and RMSE and the simulation disclaimer", async () => {
    installFetchMock();
    await renderLoaded();
    const panel = await screen.findByRole("region", { name: "Prediction model" });
    const table = await within(panel).findByRole("table", { name: "Model error metrics" });
    expect(table).toHaveTextContent("XGBoost");
    expect(table).toHaveTextContent("1,520 L");
    expect(table).toHaveTextContent("2,106 L");
    expect(table).toHaveTextContent("Last observed need (baseline)");
    expect(table).toHaveTextContent("2,891 L");
    expect(table).toHaveTextContent("3,954 L");
    expect(within(panel).getByTestId("model-disclaimer")).toHaveTextContent(modelFixture.disclaimer);
    expect(panel).toHaveTextContent("47% lower than the last-observed baseline");
    expect(panel.textContent).not.toMatch(/real-world accuracy of/i);
  });

  it("reports honestly when the model does not beat the baseline", async () => {
    const worse = clone(modelFixture);
    worse.metrics.xgboost.mae_litres = 3000;
    const { maeComparison } = await import("../screens/components/ModelPanel");
    expect(maeComparison(worse)).toContain("higher than the last-observed baseline");
  });

  it("calls onOpenTab('plan') from the Go to dispatch plan button", async () => {
    installFetchMock();
    const { props } = await renderLoaded();
    await userEvent.setup().click(screen.getByRole("button", { name: /Go to dispatch plan/ }));
    expect(props.onOpenTab).toHaveBeenCalledWith("plan");
  });

  it("calls onScenarioChange from the scenario picker", async () => {
    const other = { ...summaryFixture, scenario_id: "scn_sa01_w06_severe", label: "Severe" };
    installFetchMock({ scenarios: [summaryFixture, other] });
    const { props } = await renderLoaded();
    const select = await screen.findByRole("combobox", { name: "Scenario" });
    await waitFor(() => expect(select).toBeEnabled());
    expect(select).toHaveValue("scn_sa01_w06");
    await userEvent.setup().selectOptions(select, "scn_sa01_w06_severe");
    expect(props.onScenarioChange).toHaveBeenCalledWith("scn_sa01_w06_severe");
  });
});

describe("SnapshotScreen scenario_loaded event", () => {
  it("emits once with the exact props, even after re-renders and under StrictMode", async () => {
    installFetchMock();
    const props = snapshotProps();
    const view = render(
      <StrictMode>
        <SnapshotScreen {...props} />
      </StrictMode>,
    );
    await screen.findByRole("table", { name: "Areas" });
    expect(props.onTrack).toHaveBeenCalledTimes(1);
    expect(props.onTrack).toHaveBeenCalledWith({
      name: "scenario_loaded",
      scenario_id: "scn_sa01_w06",
      props: { n_areas: scenarioFixture.areas.length, supply_litres: 20000, tankers_available: 2 },
    });
    view.rerender(
      <StrictMode>
        <SnapshotScreen {...props} onTrack={props.onTrack} />
      </StrictMode>,
    );
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(props.onTrack).toHaveBeenCalledTimes(1);
  });

  it("emits once per scenario id: a new id emits again, returning to a seen id does not", async () => {
    installFetchMock();
    const props = snapshotProps();
    const view = render(<SnapshotScreen {...props} />);
    await waitFor(() => expect(props.onTrack).toHaveBeenCalledTimes(1));

    view.rerender(<SnapshotScreen {...props} scenarioId="scn_sa01_w06_severe" />);
    await waitFor(() => expect(props.onTrack).toHaveBeenCalledTimes(2));
    expect(props.onTrack).toHaveBeenLastCalledWith(
      expect.objectContaining({ name: "scenario_loaded", scenario_id: "scn_sa01_w06_severe" }),
    );

    view.rerender(<SnapshotScreen {...props} scenarioId="scn_sa01_w06" />);
    await screen.findByRole("table", { name: "Areas" });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(props.onTrack).toHaveBeenCalledTimes(2);
  });

  it("emits again after a remount (once per mount)", async () => {
    installFetchMock();
    const props = snapshotProps();
    const first = render(<SnapshotScreen {...props} />);
    await waitFor(() => expect(props.onTrack).toHaveBeenCalledTimes(1));
    first.unmount();
    render(<SnapshotScreen {...props} />);
    await waitFor(() => expect(props.onTrack).toHaveBeenCalledTimes(2));
  });

  it("never reports a stale scenario while the next one loads", async () => {
    installFetchMock();
    const props = snapshotProps();
    const view = render(<SnapshotScreen {...props} />);
    await screen.findByRole("table", { name: "Areas" });
    view.rerender(<SnapshotScreen {...props} scenarioId="scn_sa01_w06_mild" />);
    expect(screen.queryByRole("table", { name: "Areas" })).not.toBeInTheDocument();
    expect(screen.getByText("Loading scenario")).toBeInTheDocument();
  });
});

describe("SnapshotScreen states", () => {
  it("prompts to pick a scenario when scenarioId is null and does not load one", async () => {
    const mock = installFetchMock();
    const props = snapshotProps({ scenarioId: null });
    render(<SnapshotScreen {...props} />);
    expect(screen.getByText("No scenario selected")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Go to dispatch plan/ })).toBeDisabled();
    await screen.findByRole("table", { name: "Model error metrics" });
    expect(requestedPaths(mock).filter((path) => path.startsWith("/a/v1/scenarios/"))).toEqual([]);
    expect(props.onTrack).not.toHaveBeenCalled();
  });

  it("shows loading indicators while requests are pending", () => {
    installFetchMock();
    render(<SnapshotScreen {...snapshotProps()} />);
    expect(screen.getByText("Loading scenario")).toBeInTheDocument();
    expect(screen.getByText("Loading model details")).toBeInTheDocument();
  });

  it("shows the session-expired message inline on a 401 without crashing", async () => {
    installFetchMock({ unauthorized: true });
    render(<SnapshotScreen {...snapshotProps()} />);
    await waitFor(() => expect(screen.getAllByRole("alert").length).toBeGreaterThan(0));
    for (const alert of screen.getAllByRole("alert")) {
      expect(alert).toHaveTextContent(SESSION_EXPIRED_MESSAGE);
      expect(alert).toHaveAttribute("data-error-kind", "session-expired");
    }
    expect(screen.getByRole("heading", { name: "Crisis snapshot" })).toBeInTheDocument();
  });

  it("shows a prediction failure inline while keeping the scenario data", async () => {
    installFetchMock({ failing: { "/scenarios/scn_sa01_w06/predictions": 503 } });
    render(<SnapshotScreen {...snapshotProps()} />);
    const table = await screen.findByRole("table", { name: "Areas" });
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Predictions unavailable"));
    expect(table).toHaveTextContent("Ward 01 (synthetic)");
  });

  it("shows a model failure inline without hiding the snapshot", async () => {
    installFetchMock({ failing: { "/model": 503 } });
    render(<SnapshotScreen {...snapshotProps()} />);
    await screen.findByRole("table", { name: "Areas" });
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Model details unavailable"));
  });

  it("shows a scenario failure inline", async () => {
    installFetchMock({ failing: { "/scenarios/scn_sa01_w06": 500 } });
    render(<SnapshotScreen {...snapshotProps()} />);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Scenario unavailable"));
    expect(screen.queryByRole("table", { name: "Areas" })).not.toBeInTheDocument();
  });

  it("includes a selected scenario that is missing from the list (for example a derived one)", async () => {
    installFetchMock();
    render(<SnapshotScreen {...snapshotProps({ scenarioId: "scn_sa01_w06__d1_3f9a2c" })} />);
    const select = await screen.findByRole("combobox", { name: "Scenario" });
    await waitFor(() => expect(select).toBeEnabled());
    expect(select).toHaveValue("scn_sa01_w06__d1_3f9a2c");
  });
});
