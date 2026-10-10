import { useMemo } from "react";
import { projectNodes, stopLabel, type MapModel } from "../mapModel";
import { areaClass, tankerClass } from "./mapStyles";
import styles from "./maps.module.css";

export const SCHEMATIC_WIDTH = 640;
export const SCHEMATIC_HEIGHT = 420;
const PADDING = 56;
const RING_GAP_PX = 5;
const DEPOT_HALF_SIDE_PX = 8;

/**
 * Node-link drawing of the plan on projected coordinates. It shows the same nodes and
 * edges as the Leaflet map and needs no tiles, so it works offline.
 */
export function SchematicMap({ model }: { model: MapModel }) {
  const positions = useMemo(
    () => projectNodes([model.depot, ...model.areas], SCHEMATIC_WIDTH, SCHEMATIC_HEIGHT, PADDING),
    [model],
  );
  const depot = positions.get(model.depot.id);

  return (
    <svg
      className={styles.schematic}
      viewBox={`0 0 ${SCHEMATIC_WIDTH} ${SCHEMATIC_HEIGHT}`}
      role="img"
      aria-label="Schematic map of dispatch routes"
      data-testid="schematic-map"
    >
      {model.routes.map((route) => {
        const pointList = route.nodes.flatMap((node) => {
          const point = positions.get(node.id);
          return point ? [`${point.x.toFixed(1)},${point.y.toFixed(1)}`] : [];
        });
        return (
          <polyline
            key={route.tankerId}
            className={`${styles.route} ${tankerClass(route.colorIndex)}`}
            points={pointList.join(" ")}
            data-tanker-id={route.tankerId}
          />
        );
      })}

      {model.areas.map((area) => {
        const point = positions.get(area.id);
        if (!point) return null;
        const label = stopLabel(area);
        return (
          <g key={area.id} data-node-id={area.id}>
            <circle
              className={areaClass(area.coverageClass)}
              cx={point.x}
              cy={point.y}
              r={area.radius}
            />
            {area.unmet > 0 ? (
              <circle className={styles.ring} cx={point.x} cy={point.y} r={area.radius + RING_GAP_PX} data-unmet="true" />
            ) : null}
            <text className={styles.nodeLabel} x={point.x} y={point.y - area.radius - RING_GAP_PX - 6} textAnchor="middle">
              {area.name}
              {label ? (
                <tspan className={styles.nodeStops} x={point.x} dy="-1.15em">
                  {label}
                </tspan>
              ) : null}
            </text>
          </g>
        );
      })}

      {depot ? (
        <g data-node-id={model.depot.id}>
          <rect
            className={styles.depot}
            x={depot.x - DEPOT_HALF_SIDE_PX}
            y={depot.y - DEPOT_HALF_SIDE_PX}
            width={DEPOT_HALF_SIDE_PX * 2}
            height={DEPOT_HALF_SIDE_PX * 2}
          />
          <text className={styles.nodeLabel} x={depot.x} y={depot.y + DEPOT_HALF_SIDE_PX + 14} textAnchor="middle">
            {model.depot.name}
          </text>
        </g>
      ) : null}
    </svg>
  );
}
