import { useMemo, useState } from "react";
import type { AreaPrediction, PredictionSet, Scenario, ScenarioArea } from "../apiTypes";
import { featureLabel, formatLitres, formatNumber, formatSignedLitres, formatSpanBefore } from "../format";
import shared from "./shared.module.css";
import styles from "./AreaTable.module.css";
import { Chip } from "./ui";

export interface AreaRow {
  area: ScenarioArea;
  prediction: AreaPrediction | null;
}

export type SortKey = "name" | "population" | "last_delivery" | "requested" | "need" | "piped" | "shortfall";
export type SortDirection = "asc" | "desc";

export interface SortState {
  key: SortKey;
  direction: SortDirection;
}

export const DEFAULT_SORT: SortState = { key: "shortfall", direction: "desc" };

export function buildAreaRows(scenario: Scenario, predictions: PredictionSet | null): AreaRow[] {
  const byArea = new Map(predictions?.areas.map((p) => [p.area_id, p]) ?? []);
  return scenario.areas.map((area) => ({ area, prediction: byArea.get(area.area_id) ?? null }));
}

function sortValue(row: AreaRow, key: SortKey): number | string {
  switch (key) {
    case "name":
      return row.area.name;
    case "population":
      return row.area.population;
    case "last_delivery":
      return row.area.last_delivery_litres;
    case "requested":
      return row.area.requested_litres;
    case "need":
      return row.prediction?.predicted_need_litres ?? -1;
    case "piped":
      return row.prediction?.expected_piped_litres ?? row.area.expected_piped_litres;
    case "shortfall":
      return row.prediction?.predicted_shortfall_litres ?? -1;
  }
}

/** Stable sort; ties fall back to the area id so the order is deterministic. */
export function sortAreaRows(rows: readonly AreaRow[], sort: SortState): AreaRow[] {
  const factor = sort.direction === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const left = sortValue(a, sort.key);
    const right = sortValue(b, sort.key);
    const order =
      typeof left === "string" && typeof right === "string" ? left.localeCompare(right) : Number(left) - Number(right);
    return order !== 0 ? order * factor : a.area.area_id.localeCompare(b.area.area_id);
  });
}

const COLUMNS: { key: SortKey; label: string; numeric: boolean }[] = [
  { key: "name", label: "Area", numeric: false },
  { key: "population", label: "Population", numeric: true },
  { key: "last_delivery", label: "Last delivery", numeric: true },
  { key: "requested", label: "Requested", numeric: true },
  { key: "need", label: "Predicted need", numeric: true },
  { key: "piped", label: "Expected piped", numeric: true },
  { key: "shortfall", label: "Predicted shortfall", numeric: true },
];

interface AreaTableProps {
  scenario: Scenario;
  predictions: PredictionSet | null;
}

/** Sortable per-area table of the crisis snapshot (shortfall descending by default). */
export function AreaTable({ scenario, predictions }: AreaTableProps) {
  const [sort, setSort] = useState<SortState>(DEFAULT_SORT);
  const rows = useMemo(
    () => sortAreaRows(buildAreaRows(scenario, predictions), sort),
    [scenario, predictions, sort],
  );

  const toggle = (key: SortKey) =>
    setSort((current) =>
      current.key === key
        ? { key, direction: current.direction === "asc" ? "desc" : "asc" }
        : { key, direction: key === "name" ? "asc" : "desc" },
    );

  if (rows.length === 0) {
    return <p className={shared.muted}>This scenario has no areas.</p>;
  }

  return (
    <div className={shared.tableWrap}>
      <table className={shared.table} aria-label="Areas">
        <thead>
          <tr>
            {COLUMNS.map((column) => (
              <th
                key={column.key}
                className={column.numeric ? shared.num : undefined}
                aria-sort={sort.key === column.key ? (sort.direction === "asc" ? "ascending" : "descending") : "none"}
              >
                <button type="button" className={styles.sortButton} onClick={() => toggle(column.key)}>
                  {column.label}
                  <span aria-hidden="true" className={styles.sortMark}>
                    {sort.key === column.key ? (sort.direction === "asc" ? "▲" : "▼") : ""}
                  </span>
                </button>
              </th>
            ))}
            <th>Top drivers</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ area, prediction }) => (
            <tr key={area.area_id} data-area-id={area.area_id}>
              <td>
                <div className={styles.areaName}>{area.name}</div>
                <div className={`${shared.muted} ${shared.mono} ${styles.areaId}`}>{area.area_id}</div>
              </td>
              <td className={shared.num}>{formatNumber(area.population)}</td>
              <td className={shared.num}>
                {area.last_delivery_at ? (
                  <>
                    <div>{formatLitres(area.last_delivery_litres)}</div>
                    <div className={`${shared.muted} ${styles.subtle}`}>
                      {formatSpanBefore(area.last_delivery_at, scenario.window_start)} before window
                    </div>
                  </>
                ) : (
                  <span className={shared.muted}>No delivery yet</span>
                )}
              </td>
              <td className={shared.num}>{formatLitres(area.requested_litres)}</td>
              <td className={shared.num}>{prediction ? formatLitres(prediction.predicted_need_litres) : "…"}</td>
              <td className={shared.num}>
                {formatLitres(prediction?.expected_piped_litres ?? area.expected_piped_litres)}
              </td>
              <td className={`${shared.num} ${styles.shortfall}`}>
                {prediction ? formatLitres(prediction.predicted_shortfall_litres) : "…"}
              </td>
              <td>
                {prediction ? (
                  <div className={shared.chipRow}>
                    {prediction.top_features.map((feature) => (
                      <Chip key={feature.feature} title={feature.feature}>
                        {featureLabel(feature.feature)} {formatSignedLitres(feature.contribution_litres)}
                      </Chip>
                    ))}
                  </div>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
