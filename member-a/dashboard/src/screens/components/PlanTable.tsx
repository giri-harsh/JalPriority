import { useState } from "react";
import type { Allocation, PlanRecord } from "../../contract/types";
import { formatLitres, formatPercent, formatPriority, reasonChipText, reasonTone } from "../format";
import shared from "./shared.module.css";
import styles from "./PlanTable.module.css";
import { toneClass } from "./ui";

interface PlanTableProps {
  plan: PlanRecord;
  /** Display name for an area id (falls back to the id itself). */
  nameOf: (areaId: string) => string;
  /** Called each time a row's explanation is opened. */
  onExplain: (areaId: string) => void;
}

export function isUnserved(allocation: Allocation): boolean {
  return allocation.is_unserved_remainder || allocation.unmet_litres > 0;
}

/** Ranked allocation table with a "Why?" explanation per area. */
export function PlanTable({ plan, nameOf, onExplain }: PlanTableProps) {
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());

  const toggle = (areaId: string) => {
    const opening = !open.has(areaId);
    setOpen((current) => {
      const next = new Set(current);
      if (opening) next.add(areaId);
      else next.delete(areaId);
      return next;
    });
    if (opening) onExplain(areaId);
  };

  return (
    <section className={shared.card} aria-label="Ranked allocation" data-testid="plan-table-section">
      <h2 className={shared.cardTitle}>Ranked allocation</h2>
      <div className={shared.tableWrap}>
        <table className={shared.table} data-testid="plan-table">
          <thead>
            <tr>
              <th className={shared.num}>Rank</th>
              <th>Area</th>
              <th className={shared.num}>Priority</th>
              <th className={shared.num}>Shortfall</th>
              <th className={shared.num}>Allocated</th>
              <th className={shared.num}>Coverage</th>
              <th>Tanker, stop</th>
              <th className={shared.num}>Unmet</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {plan.result.allocations.map((allocation) => {
              const unserved = isUnserved(allocation);
              const expanded = open.has(allocation.area_id);
              const detailId = `why-${plan.plan_id}-${allocation.area_id}`;
              return (
                <RowPair
                  key={allocation.area_id}
                  allocation={allocation}
                  name={nameOf(allocation.area_id)}
                  unserved={unserved}
                  expanded={expanded}
                  detailId={detailId}
                  onToggle={() => toggle(allocation.area_id)}
                />
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

interface RowPairProps {
  allocation: Allocation;
  name: string;
  unserved: boolean;
  expanded: boolean;
  detailId: string;
  onToggle: () => void;
}

function RowPair({ allocation, name, unserved, expanded, detailId, onToggle }: RowPairProps) {
  const deliveries = [...allocation.deliveries].sort((a, b) => a.stop_order - b.stop_order);
  return (
    <>
      <tr
        className={unserved ? styles.rowDanger : undefined}
        data-area-id={allocation.area_id}
        data-unserved={unserved ? "true" : undefined}
      >
        <td className={shared.num}>{allocation.rank}</td>
        <td>
          <div className={styles.areaName}>{name}</div>
          <div className={`${shared.muted} ${shared.mono} ${styles.areaId}`}>{allocation.area_id}</div>
        </td>
        <td className={shared.num} title={`${allocation.priority_weight}`}>
          {formatPriority(allocation.priority_weight)}
        </td>
        <td className={shared.num}>{formatLitres(allocation.predicted_shortfall_litres)}</td>
        <td className={shared.num}>{formatLitres(allocation.allocated_litres)}</td>
        <td className={shared.num}>{formatPercent(allocation.coverage_ratio)}</td>
        <td>
          {deliveries.length === 0 ? (
            <span className={shared.muted}>{"—"}</span>
          ) : (
            <ul className={styles.deliveries}>
              {deliveries.map((delivery) => (
                <li key={`${delivery.tanker_id}-${delivery.stop_order}`}>
                  <span className={shared.mono}>{delivery.tanker_id}</span> · stop {delivery.stop_order} ·{" "}
                  {formatLitres(delivery.litres)}
                </li>
              ))}
            </ul>
          )}
        </td>
        <td className={`${shared.num} ${allocation.unmet_litres > 0 ? styles.unmetDanger : ""}`} data-testid="unmet-cell">
          {formatLitres(allocation.unmet_litres)}
        </td>
        <td>
          <button
            type="button"
            className={styles.whyButton}
            aria-expanded={expanded}
            aria-controls={detailId}
            onClick={onToggle}
          >
            Why?
          </button>
        </td>
      </tr>
      {expanded ? (
        <tr id={detailId} className={styles.detailRow} data-testid={`why-${allocation.area_id}`}>
          <td colSpan={9}>
            <p className={styles.reasonText}>{allocation.reason_text}</p>
            <div className={shared.chipRow}>
              {allocation.reasons.map((reason, index) => (
                <span key={`${reason.code}-${index}`} className={`${shared.badge} ${toneClass(reasonTone(reason.code))}`} data-reason-code={reason.code}>
                  {reasonChipText(reason)}
                </span>
              ))}
            </div>
          </td>
        </tr>
      ) : null}
    </>
  );
}
