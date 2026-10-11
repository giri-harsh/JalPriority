import type { Allocation, PlanResult } from "../contract/types";
import type { Scenario } from "./apiTypes";

/** How an area ended up being served; drives the marker colour on both maps. */
export type CoverageClass = "full" | "partial" | "none" | "noneed";

export interface MapNode {
  id: string;
  lat: number;
  lon: number;
}

export interface MapStop {
  tankerId: string;
  stopOrder: number;
  litres: number;
  colorIndex: number;
}

export interface MapArea extends MapNode {
  name: string;
  shortfall: number;
  allocated: number;
  unmet: number;
  coverage: number;
  coverageClass: CoverageClass;
  radius: number;
  stops: MapStop[];
}

export interface MapRoute {
  tankerId: string;
  colorIndex: number;
  nodes: MapNode[];
}

export interface MapModel {
  depot: MapNode & { name: string };
  areas: MapArea[];
  routes: MapRoute[];
}

export interface Point {
  x: number;
  y: number;
}

/** Number of distinct tanker styles defined in the CSS modules. */
export const TANKER_STYLE_COUNT = 6;

const MIN_RADIUS = 7;
const MAX_RADIUS = 24;
const NO_SHORTFALL_RADIUS = 5;

export function coverageClassOf(allocation: Allocation): CoverageClass {
  if (allocation.predicted_shortfall_litres <= 0) return "noneed";
  if (allocation.unmet_litres <= 0) return "full";
  return allocation.allocated_litres > 0 ? "partial" : "none";
}

/** Marker radius in px: proportional to the square root of the shortfall. */
export function radiusFor(shortfall: number, maxShortfall: number): number {
  if (shortfall <= 0 || maxShortfall <= 0) return NO_SHORTFALL_RADIUS;
  return MIN_RADIUS + (MAX_RADIUS - MIN_RADIUS) * Math.sqrt(shortfall / maxShortfall);
}

/** Label shown next to an area: "tk_01 #1 · tk_02 #3", or "" when nothing is delivered. */
export function stopLabel(area: Pick<MapArea, "stops">): string {
  return area.stops.map((stop) => `${stop.tankerId} #${stop.stopOrder}`).join(" · ");
}

/** Joins a plan with the scenario coordinates into the shared model drawn by both maps. */
export function buildMapModel(result: PlanResult, scenario: Scenario): MapModel {
  const colorOf = new Map(result.routes.map((route, index) => [route.tanker_id, index]));
  const scenarioAreas = new Map(scenario.areas.map((area) => [area.area_id, area]));
  const maxShortfall = Math.max(0, ...result.allocations.map((a) => a.predicted_shortfall_litres));

  const areas: MapArea[] = [];
  for (const allocation of result.allocations) {
    const source = scenarioAreas.get(allocation.area_id);
    if (!source) continue;
    areas.push({
      id: allocation.area_id,
      name: source.name,
      lat: source.lat,
      lon: source.lon,
      shortfall: allocation.predicted_shortfall_litres,
      allocated: allocation.allocated_litres,
      unmet: allocation.unmet_litres,
      coverage: allocation.coverage_ratio,
      coverageClass: coverageClassOf(allocation),
      radius: radiusFor(allocation.predicted_shortfall_litres, maxShortfall),
      stops: [...allocation.deliveries]
        .sort((a, b) => a.stop_order - b.stop_order)
        .map((delivery) => ({
          tankerId: delivery.tanker_id,
          stopOrder: delivery.stop_order,
          litres: delivery.litres,
          colorIndex: colorOf.get(delivery.tanker_id) ?? 0,
        })),
    });
  }

  const depot = { id: scenario.depot.depot_id, name: scenario.depot.name, lat: scenario.depot.lat, lon: scenario.depot.lon };
  const nodeById = new Map<string, MapNode>([[depot.id, depot], ...areas.map((a): [string, MapNode] => [a.id, a])]);

  const routes: MapRoute[] = [];
  for (const route of result.routes) {
    const nodes = route.path.flatMap((id) => {
      const node = nodeById.get(id);
      return node ? [node] : [];
    });
    if (nodes.length >= 2) {
      routes.push({ tankerId: route.tanker_id, colorIndex: colorOf.get(route.tanker_id) ?? 0, nodes });
    }
  }
  return { depot, areas, routes };
}

/**
 * Equirectangular projection of nodes into a `width` x `height` box with `padding`,
 * keeping the aspect ratio and centring the drawing. Used by the schematic map.
 */
export function projectNodes(nodes: readonly MapNode[], width: number, height: number, padding: number): Map<string, Point> {
  const projected = new Map<string, Point>();
  if (nodes.length === 0) return projected;

  const lats = nodes.map((n) => n.lat);
  const lons = nodes.map((n) => n.lon);
  const minLat = Math.min(...lats);
  const maxLat = Math.max(...lats);
  const minLon = Math.min(...lons);
  const maxLon = Math.max(...lons);
  const lonScale = Math.cos((((minLat + maxLat) / 2) * Math.PI) / 180);
  const spanX = Math.max((maxLon - minLon) * lonScale, 1e-9);
  const spanY = Math.max(maxLat - minLat, 1e-9);
  const scale = Math.min((width - 2 * padding) / spanX, (height - 2 * padding) / spanY);
  const offsetX = (width - spanX * scale) / 2;
  const offsetY = (height - spanY * scale) / 2;

  for (const node of nodes) {
    projected.set(node.id, {
      x: offsetX + (node.lon - minLon) * lonScale * scale,
      y: offsetY + (maxLat - node.lat) * scale,
    });
  }
  return projected;
}
