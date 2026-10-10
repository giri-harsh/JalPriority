import { describe, expect, it } from "vitest";
import {
  buildMapModel,
  coverageClassOf,
  projectNodes,
  radiusFor,
  stopLabel,
  type MapNode,
} from "../screens/mapModel";
import { clone, planRecordFixture, scenarioFixture } from "../test/support";

describe("buildMapModel", () => {
  const model = buildMapModel(planRecordFixture.result, scenarioFixture);

  it("joins allocations with scenario coordinates in rank order", () => {
    expect(model.areas.map((area) => area.id)).toEqual(["ar_03", "ar_02", "ar_01"]);
    expect(model.areas[0]).toMatchObject({ name: "Ward 03 (synthetic)", lat: 28.712, lon: 77.501, shortfall: 6000, unmet: 0 });
    expect(model.depot).toMatchObject({ id: "dp_01", lat: 28.6692, lon: 77.4538 });
  });

  it("builds one polyline per assigned tanker along its path, depot to depot", () => {
    expect(model.routes.map((route) => route.tankerId)).toEqual(["tk_01", "tk_02"]);
    expect(model.routes[0]!.nodes.map((node) => node.id)).toEqual(["dp_01", "ar_03", "ar_01", "dp_01"]);
    expect(model.routes.map((route) => route.colorIndex)).toEqual([0, 1]);
  });

  it("carries stop orders per area and tanker, ready for labels", () => {
    const ar01 = model.areas.find((area) => area.id === "ar_01")!;
    expect(ar01.stops).toEqual([{ tankerId: "tk_01", stopOrder: 2, litres: 5000, colorIndex: 0 }]);
    expect(stopLabel(ar01)).toBe("tk_01 #2");
    expect(stopLabel({ stops: [] })).toBe("");
  });

  it("classifies coverage and sizes circles by shortfall", () => {
    const byId = Object.fromEntries(model.areas.map((area) => [area.id, area]));
    expect(byId.ar_03!.coverageClass).toBe("full");
    expect(byId.ar_01!.coverageClass).toBe("partial");
    expect(byId.ar_01!.radius).toBeGreaterThan(byId.ar_02!.radius);
    expect(byId.ar_02!.radius).toBeGreaterThan(byId.ar_03!.radius);
    const alloc = planRecordFixture.result.allocations[2]!;
    expect(coverageClassOf({ ...alloc, allocated_litres: 0, unmet_litres: 14000 })).toBe("none");
    expect(coverageClassOf({ ...alloc, predicted_shortfall_litres: 0, allocated_litres: 0, unmet_litres: 0 })).toBe("noneed");
    expect(radiusFor(0, 100)).toBeLessThan(radiusFor(1, 100));
    expect(radiusFor(100, 100)).toBe(24);
  });

  it("skips areas and path nodes the scenario does not know instead of crashing", () => {
    const scenario = clone(scenarioFixture);
    scenario.areas = scenario.areas.filter((area) => area.area_id !== "ar_03");
    const partial = buildMapModel(planRecordFixture.result, scenario);
    expect(partial.areas.map((area) => area.id)).toEqual(["ar_02", "ar_01"]);
    expect(partial.routes[0]!.nodes.map((node) => node.id)).toEqual(["dp_01", "ar_01", "dp_01"]);
  });
});

describe("projectNodes (schematic)", () => {
  const nodes: MapNode[] = [
    { id: "a", lat: 28.6, lon: 77.4 },
    { id: "b", lat: 28.7, lon: 77.5 },
    { id: "c", lat: 28.65, lon: 77.45 },
  ];

  it("keeps every node inside the padded box with north up and east right", () => {
    const points = projectNodes(nodes, 640, 420, 40);
    for (const point of points.values()) {
      expect(point.x).toBeGreaterThanOrEqual(40 - 1e-6);
      expect(point.x).toBeLessThanOrEqual(600 + 1e-6);
      expect(point.y).toBeGreaterThanOrEqual(40 - 1e-6);
      expect(point.y).toBeLessThanOrEqual(380 + 1e-6);
    }
    expect(points.get("b")!.y).toBeLessThan(points.get("a")!.y);
    expect(points.get("b")!.x).toBeGreaterThan(points.get("a")!.x);
    expect(points.get("c")!.x).toBeCloseTo((points.get("a")!.x + points.get("b")!.x) / 2, 6);
  });

  it("preserves the aspect ratio of the geography (longitude shrinks with latitude)", () => {
    const points = projectNodes(nodes, 640, 420, 40);
    const dx = points.get("b")!.x - points.get("a")!.x;
    const dy = points.get("a")!.y - points.get("b")!.y;
    expect(dx / dy).toBeCloseTo(Math.cos((28.65 * Math.PI) / 180), 3);
  });

  it("handles no nodes and a single node", () => {
    expect(projectNodes([], 640, 420, 40).size).toBe(0);
    const single = projectNodes([{ id: "x", lat: 1, lon: 2 }], 640, 420, 40);
    expect(single.get("x")).toBeDefined();
    expect(Number.isFinite(single.get("x")!.x)).toBe(true);
  });
});
