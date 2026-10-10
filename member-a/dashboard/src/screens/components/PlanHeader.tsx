import type { ReactNode } from "react";
import type { PlanRecord, PlanStatus } from "../../contract/types";
import { formatDuration, formatKm, formatLitres, formatUtc, strategyLabel, type Tone } from "../format";
import shared from "./shared.module.css";
import styles from "./PlanHeader.module.css";
import { Badge } from "./ui";

const STATUS_TONES: Record<PlanStatus, Tone> = {
  recommended: "accent",
  approved: "ok",
  edited: "warn",
  rejected: "danger",
  superseded: "neutral",
};

/** `time_limited` and `heuristic` mean the plan is feasible but possibly not optimal. */
export function solverTone(status: PlanRecord["result"]["solver"]["status"]): Tone {
  return status === "time_limited" || status === "heuristic" ? "warn" : "ok";
}

function Stat({ label, children, danger = false }: { label: string; children: ReactNode; danger?: boolean }) {
  return (
    <div className={styles.stat}>
      <dt className={styles.statLabel}>{label}</dt>
      <dd className={`${styles.statValue} ${danger ? styles.statDanger : ""}`}>{children}</dd>
    </div>
  );
}

/** Plan summary: strategy, solver outcome, litres allocated against supply, unmet shortage and status. */
export function PlanHeader({ plan }: { plan: PlanRecord }) {
  const { result } = plan;
  const { totals, solver, constraint_check: check } = result;
  const satisfied = check.violations.length === 0 && check.water_cap_ok && check.capacity_ok && check.route_limit_ok && check.unavailable_tanker_ok;

  return (
    <header className={`${shared.card} ${styles.header}`} data-testid="plan-header">
      <div className={styles.titleRow}>
        <h1 className={shared.screenTitle}>Dispatch plan</h1>
        <Badge tone="accent" data-testid="plan-strategy">
          {strategyLabel(result.strategy)}
        </Badge>
        <Badge tone={STATUS_TONES[plan.status]} data-plan-status={plan.status} data-testid="plan-status">
          {plan.status}
        </Badge>
        <span className={`${shared.muted} ${styles.meta}`}>
          <span className={shared.mono}>{plan.plan_id}</span> · window {plan.window_index} · scenario{" "}
          <span className={shared.mono}>{plan.scenario_id}</span> · created {formatUtc(plan.created_at)} by {plan.created_by}
        </span>
      </div>
      <dl className={styles.stats}>
        <Stat label="Solver">
          <span className={styles.solver}>
            <span className={shared.mono}>{solver.engine}</span>
            <Badge tone={solverTone(solver.status)} data-solver-status={solver.status} data-testid="solver-status">
              {solver.status}
            </Badge>
          </span>
        </Stat>
        <Stat label="Solve time">{formatDuration(solver.solve_time_ms)}</Stat>
        <Stat label="Allocated / supply">
          {formatLitres(totals.allocated_litres)} / {formatLitres(totals.supply_available_litres)}
        </Stat>
        <Stat label="Total unmet" danger={totals.unmet_litres > 0}>
          <span data-testid="total-unmet">{formatLitres(totals.unmet_litres)}</span>
        </Stat>
        <Stat label="Predicted shortfall">{formatLitres(totals.shortfall_litres)}</Stat>
        <Stat label="Travel">{formatKm(totals.travel_km)}</Stat>
        <Stat label="Constraints">
          {satisfied ? (
            <Badge tone="ok" data-testid="constraint-status">
              all satisfied
            </Badge>
          ) : (
            <Badge tone="danger" title={check.violations.map((v) => v.detail).join("\n")} data-testid="constraint-status">
              {check.violations.length} violation{check.violations.length === 1 ? "" : "s"}
            </Badge>
          )}
        </Stat>
      </dl>
    </header>
  );
}
