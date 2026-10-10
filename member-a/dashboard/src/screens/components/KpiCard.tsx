import type { ReactNode } from "react";
import shared from "./shared.module.css";
import styles from "./KpiCard.module.css";

interface KpiCardProps {
  id: string;
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  children?: ReactNode;
}

/** One headline number of the crisis snapshot. */
export function KpiCard({ id, label, value, hint, children }: KpiCardProps) {
  return (
    <div className={`${shared.card} ${styles.kpi}`} role="group" aria-label={label} data-kpi={id}>
      <div className={styles.label}>{label}</div>
      <div className={styles.value}>{value}</div>
      {hint ? <div className={styles.hint}>{hint}</div> : null}
      {children}
    </div>
  );
}

interface CoverageBarProps {
  /** Share of the shortfall the available water can cover, 0..100. */
  percent: number;
}

/** "% of shortfall coverable" bar; turns red below 50%. */
export function CoverageBar({ percent }: CoverageBarProps) {
  const clamped = Math.max(0, Math.min(100, percent));
  return (
    <div
      className={styles.bar}
      role="progressbar"
      aria-label="Share of predicted shortfall coverable with available water"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(clamped)}
    >
      <div
        className={`${styles.barFill} ${clamped < 50 ? styles.barFillDanger : clamped < 100 ? styles.barFillWarn : ""}`}
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}
