import type { Route } from "../../contract/types";
import { formatKm, formatLitres } from "../format";
import { tankerClass } from "./mapStyles";
import mapStyles from "./maps.module.css";
import shared from "./shared.module.css";
import styles from "./TankerPanel.module.css";
import { Badge } from "./ui";

interface TankerPanelProps {
  routes: readonly Route[];
  nameOf: (nodeId: string) => string;
}

function RouteCard({ route, colorIndex, nameOf }: { route: Route; colorIndex: number; nameOf: (id: string) => string }) {
  const loadPercent = route.capacity_litres > 0 ? Math.min(100, (route.load_litres / route.capacity_litres) * 100) : 0;
  const overLoad = route.load_litres > route.capacity_litres;
  const overRoute = route.route_km > route.max_route_km;
  const stops = [...route.stops].sort((a, b) => a.stop_order - b.stop_order);

  return (
    <li className={styles.card} data-tanker-id={route.tanker_id} data-route-status={route.status}>
      <div className={styles.cardHeader}>
        <span className={`${mapStyles.swatch} ${tankerClass(colorIndex)}`} aria-hidden="true" />
        <strong className={shared.mono}>{route.tanker_id}</strong>
        <Badge tone={route.status === "assigned" ? "accent" : route.status === "idle" ? "neutral" : "warn"}>
          {route.status}
        </Badge>
      </div>

      {route.status === "assigned" ? (
        <>
          <dl className={styles.facts}>
            <div>
              <dt>Load</dt>
              <dd className={overLoad ? styles.over : undefined}>
                {formatLitres(route.load_litres)} / {formatLitres(route.capacity_litres)}
              </dd>
            </div>
            <div>
              <dt>Route</dt>
              <dd className={overRoute ? styles.over : undefined}>
                {formatKm(route.route_km)} / {formatKm(route.max_route_km)}
              </dd>
            </div>
          </dl>
          <div
            className={styles.loadBar}
            role="progressbar"
            aria-label={`${route.tanker_id} load`}
            aria-valuemin={0}
            aria-valuemax={route.capacity_litres}
            aria-valuenow={route.load_litres}
          >
            <div className={styles.loadFill} style={{ width: `${loadPercent}%` }} />
          </div>
          <ol className={styles.stops}>
            {stops.map((stop) => (
              <li key={stop.stop_order} className={styles.stop}>
                <span className={styles.stopOrder}>{stop.stop_order}</span>
                <span className={styles.stopName}>{nameOf(stop.area_id)}</span>
                <span className={shared.num}>{formatLitres(stop.litres)}</span>
              </li>
            ))}
          </ol>
          <p className={`${shared.muted} ${styles.path}`}>{route.path.join(" → ")}</p>
        </>
      ) : (
        <p className={shared.muted}>
          {route.status === "idle" ? "No deliveries assigned." : "Not available this window."} Capacity{" "}
          {formatLitres(route.capacity_litres)}.
        </p>
      )}
    </li>
  );
}

/** Driver briefing: ordered stops with litres, load against capacity and route length per tanker. */
export function TankerPanel({ routes, nameOf }: TankerPanelProps) {
  return (
    <section className={shared.card} aria-label="Tanker assignments" data-testid="tanker-panel">
      <h2 className={shared.cardTitle}>Tankers</h2>
      {routes.length === 0 ? (
        <p className={shared.muted}>No tankers in this plan.</p>
      ) : (
        <ul className={styles.list}>
          {routes.map((route, index) => (
            <RouteCard key={route.tanker_id} route={route} colorIndex={index} nameOf={nameOf} />
          ))}
        </ul>
      )}
    </section>
  );
}
