import type { Reason, ReasonCode, Strategy } from "../contract/types";

const INTEGER = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const DECIMAL = new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 2 });

export function formatNumber(value: number): string {
  return INTEGER.format(value);
}

export function formatLitres(litres: number): string {
  return `${INTEGER.format(litres)} L`;
}

/** Signed litres for feature contributions: "+2,300 L" / "-950 L". */
export function formatSignedLitres(litres: number): string {
  const rounded = Math.round(litres);
  return `${rounded > 0 ? "+" : rounded < 0 ? "-" : ""}${INTEGER.format(Math.abs(rounded))} L`;
}

export function formatKm(km: number): string {
  return `${DECIMAL.format(km)} km`;
}

/** Ratio (0..1) as a whole percentage: 0.3571 -> "36%". */
export function formatPercent(ratio: number): string {
  return `${Math.round(ratio * 100)}%`;
}

export function formatPriority(priority: number): string {
  return priority.toFixed(2);
}

export function formatDuration(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;
}

/** "2026-10-10T09:15:02.481Z" -> "2026-10-10 09:15 UTC". */
export function formatUtc(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return `${date.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

/** How long before `referenceIso` the instant `iso` happened: "35 min", "16 h", "3 d". */
export function formatSpanBefore(iso: string, referenceIso: string): string {
  const deltaMs = new Date(referenceIso).getTime() - new Date(iso).getTime();
  if (Number.isNaN(deltaMs)) return "unknown";
  const minutes = Math.max(0, Math.round(deltaMs / 60_000));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h`;
  return `${Math.round(hours / 24)} d`;
}

const FEATURE_LABELS: Record<string, string> = {
  max_temp_c: "max temperature",
  heat_index_c: "heat index",
  alert_level: "alert level",
  heat_exposure: "heat exposure",
  population: "population",
  last_need_litres: "last need",
  mean_need_last3_litres: "3-window mean need",
  last_received_litres: "last received",
  expected_piped_litres: "expected piped",
};

export function featureLabel(feature: string): string {
  return FEATURE_LABELS[feature] ?? feature.replace(/_/g, " ");
}

const STRATEGY_LABELS: Record<Strategy, string> = {
  jalpriority: "JalPriority",
  fcfs: "First come, first served",
  nearest_first: "Nearest first",
};

export function strategyLabel(strategy: Strategy): string {
  return STRATEGY_LABELS[strategy];
}

export type Tone = "ok" | "warn" | "danger" | "accent" | "neutral";

const ALERT_TONES: Record<number, Tone> = { 0: "ok", 1: "accent", 2: "warn", 3: "danger" };

/** Badge tone for a heat alert level (0 to 3). */
export function alertTone(level: number): Tone {
  return ALERT_TONES[level] ?? "neutral";
}

/** Share of the total predicted shortfall that the available tanker water covers, 0..100. */
export function coverablePercent(supplyLitres: number, shortfallLitres: number): number {
  if (shortfallLitres <= 0) return 100;
  return Math.min(100, (supplyLitres / shortfallLitres) * 100);
}

const REASON_LABELS: Record<ReasonCode, string> = {
  HIGH_PREDICTED_SHORTFALL: "High predicted shortfall",
  HIGH_DEBT: "Accumulated debt",
  HIGH_VULNERABILITY: "High vulnerability",
  HEAT_ALERT: "Heat alert",
  LOWER_RELATIVE_PRIORITY: "Lower relative priority",
  FULLY_SERVED: "Fully served",
  PARTIALLY_SERVED: "Partially served",
  NOT_SERVED: "Not served",
  NO_SHORTFALL: "No shortfall",
  SUPPLY_EXHAUSTED: "Water supply exhausted",
  TANKER_CAPACITY_LIMIT: "Tanker capacity limit",
  ROUTE_LIMIT: "Route limit",
  TRAVEL_COST_TRADEOFF: "Travel cost trade-off",
  TIME_LIMITED_SOLUTION: "Time-limited solution",
  FCFS_ORDER: "First come, first served",
  NEAREST_FIRST_ORDER: "Nearest first",
};

const REASON_TONES: Record<ReasonCode, Tone> = {
  HIGH_PREDICTED_SHORTFALL: "accent",
  HIGH_DEBT: "accent",
  HIGH_VULNERABILITY: "accent",
  HEAT_ALERT: "accent",
  LOWER_RELATIVE_PRIORITY: "neutral",
  FULLY_SERVED: "ok",
  PARTIALLY_SERVED: "warn",
  NOT_SERVED: "danger",
  NO_SHORTFALL: "ok",
  SUPPLY_EXHAUSTED: "danger",
  TANKER_CAPACITY_LIMIT: "danger",
  ROUTE_LIMIT: "danger",
  TRAVEL_COST_TRADEOFF: "warn",
  TIME_LIMITED_SOLUTION: "warn",
  FCFS_ORDER: "neutral",
  NEAREST_FIRST_ORDER: "neutral",
};

export function reasonTone(code: ReasonCode): Tone {
  return REASON_TONES[code];
}

function numberParam(reason: Reason, key: string): number | null {
  const value = reason.params[key];
  return typeof value === "number" ? value : null;
}

/** Short chip text for a reason code, with its most telling parameter when present. */
export function reasonChipText(reason: Reason): string {
  const label = REASON_LABELS[reason.code] ?? reason.code;
  let detail: string | null = null;
  switch (reason.code) {
    case "HIGH_PREDICTED_SHORTFALL": {
      const litres = numberParam(reason, "predicted_shortfall_litres");
      detail = litres === null ? null : formatLitres(litres);
      break;
    }
    case "HIGH_DEBT": {
      const litres = numberParam(reason, "debt_litres");
      detail = litres === null ? null : formatLitres(litres);
      break;
    }
    case "HIGH_VULNERABILITY": {
      const index = numberParam(reason, "vulnerability_index");
      detail = index === null ? null : index.toFixed(2);
      break;
    }
    case "HEAT_ALERT": {
      const level = numberParam(reason, "alert_level");
      detail = level === null ? null : `level ${level}`;
      break;
    }
    case "LOWER_RELATIVE_PRIORITY": {
      const weight = numberParam(reason, "priority_weight");
      detail = weight === null ? null : formatPriority(weight);
      break;
    }
    case "FULLY_SERVED": {
      const litres = numberParam(reason, "allocated_litres");
      detail = litres === null ? null : formatLitres(litres);
      break;
    }
    case "PARTIALLY_SERVED": {
      const given = numberParam(reason, "allocated_litres");
      const wanted = numberParam(reason, "predicted_shortfall_litres");
      detail = given === null || wanted === null ? null : `${formatNumber(given)} of ${formatLitres(wanted)}`;
      break;
    }
    case "SUPPLY_EXHAUSTED":
    case "TANKER_CAPACITY_LIMIT": {
      const litres = numberParam(reason, "unmet_litres");
      detail = litres === null ? null : `${formatLitres(litres)} unmet`;
      break;
    }
    case "ROUTE_LIMIT":
    case "TRAVEL_COST_TRADEOFF": {
      const km = numberParam(reason, "distance_from_depot_km");
      detail = km === null ? null : `${formatKm(km)} from depot`;
      break;
    }
    case "TIME_LIMITED_SOLUTION": {
      const seconds = numberParam(reason, "time_limit_s");
      detail = seconds === null ? null : `${seconds} s limit`;
      break;
    }
    default:
      break;
  }
  return detail === null ? label : `${label} · ${detail}`;
}

const UNSERVED_REASONS: Partial<Record<ReasonCode, string>> = {
  SUPPLY_EXHAUSTED: "All available tanker water went to higher-priority areas first.",
  TANKER_CAPACITY_LIMIT: "All available tanker capacity is already used.",
  ROUTE_LIMIT: "No tanker could add this area within its route limit.",
  TRAVEL_COST_TRADEOFF: "The travel cost outweighed the benefit of serving the rest.",
  LOWER_RELATIVE_PRIORITY: "Higher-priority areas were served first.",
};

export function unservedReasonText(code: ReasonCode): string {
  return UNSERVED_REASONS[code] ?? REASON_LABELS[code] ?? code;
}
