import { useMemo } from "react";
import type { PlanResult } from "../../contract/types";
import type { Scenario } from "../apiTypes";
import { buildMapModel, type MapModel } from "../mapModel";
import type { Resource } from "../useResource";
import { RouteMap } from "./RouteMap";
import { SchematicMap } from "./SchematicMap";
import { tankerClass } from "./mapStyles";
import shared from "./shared.module.css";
import styles from "./maps.module.css";
import { ErrorNotice, Spinner } from "./ui";
import { useMapFallback, type MapView } from "./useMapFallback";

const VIEWS: { view: MapView; label: string }[] = [
  { view: "map", label: "Map" },
  { view: "schematic", label: "Schematic" },
];

function Legend({ model }: { model: MapModel }) {
  return (
    <ul className={styles.legend} aria-label="Map legend">
      {model.routes.map((route) => (
        <li key={route.tankerId} className={styles.legendItem}>
          <span className={`${styles.swatch} ${tankerClass(route.colorIndex)}`} aria-hidden="true" />
          {route.tankerId}
        </li>
      ))}
      <li className={styles.legendItem}>
        <span className={`${styles.dot} ${styles.dotFull}`} aria-hidden="true" /> fully served
      </li>
      <li className={styles.legendItem}>
        <span className={`${styles.dot} ${styles.dotPartial}`} aria-hidden="true" /> partly served
      </li>
      <li className={styles.legendItem}>
        <span className={`${styles.dot} ${styles.dotNone}`} aria-hidden="true" /> not served
      </li>
      <li className={styles.legendItem}>
        <span className={`${styles.dot} ${styles.dotRing}`} aria-hidden="true" /> red ring: unmet shortfall
      </li>
      <li className={styles.legendItem}>
        <span className={styles.square} aria-hidden="true" /> depot
      </li>
      <li className={styles.legendItem}>circle size: predicted shortfall</li>
    </ul>
  );
}

interface PlanMapProps {
  result: PlanResult;
  scenario: Resource<Scenario>;
}

/** Map panel: Leaflet map with an automatic and manual switch to a schematic drawing. */
export function PlanMap({ result, scenario }: PlanMapProps) {
  const { view, fellBack, reportTileError, select } = useMapFallback();
  const model = useMemo(
    () => (scenario.status === "ready" ? buildMapModel(result, scenario.data) : null),
    [result, scenario],
  );

  return (
    <section className={shared.card} aria-label="Route map">
      <div className={styles.toolbar}>
        <h2 className={styles.toolbarTitle}>Routes</h2>
        <div className={styles.toggle} role="group" aria-label="Map view">
          {VIEWS.map((option) => (
            <button
              key={option.view}
              type="button"
              className={`${styles.toggleButton} ${view === option.view ? styles.toggleButtonOn : ""}`}
              aria-pressed={view === option.view}
              onClick={() => select(option.view)}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      {fellBack ? (
        <p className={`${shared.notice} ${shared.noticeWarn}`} role="status" data-testid="map-fallback-notice">
          Map tiles could not be loaded, so the schematic is shown instead.
        </p>
      ) : null}

      {scenario.status === "error" ? <ErrorNotice error={scenario.error} what="Map coordinates unavailable" /> : null}
      {scenario.status === "loading" || scenario.status === "idle" ? (
        <div className={`${styles.frame} ${styles.placeholder}`}>
          <Spinner label="Loading map" />
        </div>
      ) : null}
      {model ? (
        <>
          <div className={styles.frame} data-testid="map-frame" data-view={view}>
            {view === "map" ? (
              <RouteMap key={`${result.result_id}:${result.scenario_id}`} model={model} onTileError={reportTileError} />
            ) : (
              <SchematicMap model={model} />
            )}
          </div>
          <Legend model={model} />
        </>
      ) : null}
    </section>
  );
}
