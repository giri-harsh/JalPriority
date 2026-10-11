import { act, fireEvent, render, renderHook, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { DispatchPlanScreen } from "../screens";
import { TILE_ERROR_LIMIT, useMapFallback } from "../screens/components/useMapFallback";
import { installFetchMock, planProps } from "../test/support";

describe("useMapFallback", () => {
  it("starts on the map and falls back to the schematic on the third tile error", () => {
    const { result } = renderHook(() => useMapFallback());
    expect(TILE_ERROR_LIMIT).toBe(3);
    expect(result.current.view).toBe("map");
    act(() => result.current.reportTileError());
    act(() => result.current.reportTileError());
    expect(result.current.view).toBe("map");
    expect(result.current.fellBack).toBe(false);
    act(() => result.current.reportTileError());
    expect(result.current.view).toBe("schematic");
    expect(result.current.fellBack).toBe(true);
  });

  it("lets the user switch manually and resets the failure count when choosing the map again", () => {
    const { result } = renderHook(() => useMapFallback());
    act(() => result.current.select("schematic"));
    expect(result.current.view).toBe("schematic");
    expect(result.current.fellBack).toBe(false);

    for (let i = 0; i < 3; i += 1) act(() => result.current.reportTileError());
    expect(result.current.fellBack).toBe(true);

    act(() => result.current.select("map"));
    expect(result.current.view).toBe("map");
    expect(result.current.fellBack).toBe(false);
    act(() => result.current.reportTileError());
    act(() => result.current.reportTileError());
    expect(result.current.view).toBe("map");
  });
});

describe("plan map in the screen", () => {
  async function renderMap() {
    installFetchMock();
    const view = render(<DispatchPlanScreen {...planProps()} />);
    const frame = await screen.findByTestId("map-frame");
    return { ...view, frame };
  }

  it("draws the Leaflet map with vector layers only: routes, area circles, red rings and a depot square", async () => {
    const { frame } = await renderMap();
    expect(frame).toHaveAttribute("data-view", "map");
    expect(frame.querySelector(".leaflet-container")).not.toBeNull();
    expect(frame.querySelectorAll(".leaflet-marker-icon")).toHaveLength(0);
    expect(frame.querySelectorAll("img.leaflet-marker-icon, .leaflet-marker-pane > *")).toHaveLength(0);
    // 2 assigned tankers -> 2 polylines; 3 areas + 1 ring (ar_01 has unmet) + depot rectangle -> paths.
    const paths = frame.querySelectorAll(".leaflet-overlay-pane path");
    expect(paths.length).toBe(2 + 3 + 1 + 1);
    expect(frame.querySelectorAll('path[class*="ring"]')).toHaveLength(1);
    expect(frame.querySelectorAll('path[class*="depot"]')).toHaveLength(1);
  });

  it("labels each served area with its tanker and stop order using permanent tooltips", async () => {
    const { frame } = await renderMap();
    const tooltips = Array.from(frame.querySelectorAll(".leaflet-tooltip")).map((node) => node.textContent);
    expect(tooltips).toContain("Ward 03 (synthetic)tk_01 #1");
    expect(tooltips).toContain("Ward 01 (synthetic)tk_01 #2");
    expect(tooltips).toContain("Ward 02 (synthetic)tk_02 #1");
  });

  it("falls back to the schematic after 3 tile errors and says so", async () => {
    const { frame } = await renderMap();
    const tiles = Array.from(frame.querySelectorAll("img.leaflet-tile"));
    expect(tiles.length).toBeGreaterThanOrEqual(3);

    fireEvent.error(tiles[0]!);
    fireEvent.error(tiles[1]!);
    expect(screen.getByTestId("map-frame")).toHaveAttribute("data-view", "map");
    expect(screen.queryByTestId("map-fallback-notice")).not.toBeInTheDocument();

    fireEvent.error(tiles[2]!);
    expect(screen.getByTestId("map-frame")).toHaveAttribute("data-view", "schematic");
    expect(screen.getByTestId("schematic-map")).toBeInTheDocument();
    expect(screen.getByTestId("map-fallback-notice")).toHaveTextContent("schematic");
    expect(screen.getByRole("button", { name: "Schematic" })).toHaveAttribute("aria-pressed", "true");
  });

  it("toggles between Map and Schematic manually", async () => {
    const { frame } = await renderMap();
    const user = userEvent.setup();
    expect(screen.getByRole("button", { name: "Map" })).toHaveAttribute("aria-pressed", "true");

    await user.click(screen.getByRole("button", { name: "Schematic" }));
    expect(screen.getByTestId("map-frame")).toHaveAttribute("data-view", "schematic");
    expect(screen.queryByTestId("map-fallback-notice")).not.toBeInTheDocument();
    expect(frame.querySelector(".leaflet-container")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Map" }));
    expect(screen.getByTestId("map-frame")).toHaveAttribute("data-view", "map");
    expect(screen.getByTestId("map-frame").querySelector(".leaflet-container")).not.toBeNull();
  });

  it("draws the same nodes and edges in the schematic", async () => {
    await renderMap();
    await userEvent.setup().click(screen.getByRole("button", { name: "Schematic" }));
    const svg = screen.getByTestId("schematic-map");
    expect(svg.querySelectorAll("polyline")).toHaveLength(2);
    expect(Array.from(svg.querySelectorAll("polyline")).map((line) => line.getAttribute("data-tanker-id"))).toEqual(["tk_01", "tk_02"]);
    const nodeIds = Array.from(svg.querySelectorAll("[data-node-id]")).map((node) => node.getAttribute("data-node-id"));
    expect(nodeIds.sort()).toEqual(["ar_01", "ar_02", "ar_03", "dp_01"]);
    expect(svg.querySelectorAll("circle[data-unmet]")).toHaveLength(1);
    expect(svg.querySelectorAll("rect")).toHaveLength(1);
    expect(within(svg as unknown as HTMLElement).getByText("Ward 03 (synthetic)")).toBeInTheDocument();
    expect(svg).toHaveTextContent("tk_01 #1");
    const points = svg.querySelector('polyline[data-tanker-id="tk_01"]')!.getAttribute("points")!.split(" ");
    expect(points).toHaveLength(4); // depot, ar_03, ar_01, depot
    expect(points[0]).toBe(points[3]);
  });
});
