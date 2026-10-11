import React, { useEffect, useState, useMemo, useCallback } from "react";
import { useShell } from "../shell/ShellContext";
import { bClient, ComparisonResponse, APIClientError } from "../api/bClient";
import { Metrics } from "../contract/types";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from "recharts";
import styles from "../styles/screens.module.css";

interface MetricRowDef {
  key: keyof Metrics;
  label: string;
  direction: "lower" | "higher";
  arrow: string;
  format: (val: number | null) => string;
}

const METRIC_ROWS: MetricRowDef[] = [
  {
    key: "priority_weighted_unmet",
    label: "Priority-Weighted Unmet (PWU)",
    direction: "lower",
    arrow: "↓",
    format: (val) => (val !== null ? val.toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 }) : "N/A"),
  },
  {
    key: "deprivation_debt_remaining_litres",
    label: "Deprivation Debt Remaining",
    direction: "lower",
    arrow: "↓",
    format: (val) => (val !== null ? `${val.toLocaleString()} L` : "N/A"),
  },
  {
    key: "total_unmet_litres",
    label: "Total Unmet Need",
    direction: "lower",
    arrow: "↓",
    format: (val) => (val !== null ? `${val.toLocaleString()} L` : "N/A"),
  },
  {
    key: "min_coverage_ratio",
    label: "Minimum Ward Coverage",
    direction: "higher",
    arrow: "↑",
    format: (val) => (val !== null ? `${(val * 100).toFixed(1)}%` : "N/A"),
  },
  {
    key: "total_travel_km",
    label: "Total Fleet Travel",
    direction: "lower",
    arrow: "↓",
    format: (val) => (val !== null ? `${val.toFixed(2)} km` : "N/A"),
  },
  {
    key: "constraint_violations",
    label: "Constraint Violations",
    direction: "lower",
    arrow: "↓",
    format: (val) => (val !== null ? `${val}` : "0"),
  },
  {
    key: "replanning_time_ms",
    label: "Solve Latency",
    direction: "lower",
    arrow: "↓",
    format: (val) => (val !== null ? `${val} ms` : "—"),
  },
];

export const CompareScreen: React.FC = () => {
  const { token, scenarioId, weights } = useShell();

  const [comparison, setComparison] = useState<ComparisonResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Run comparison endpoint
  const runComparison = useCallback(async () => {
    if (!token || !scenarioId) return;
    setLoading(true);
    setError(null);
    try {
      const res = await bClient.runComparison(token, scenarioId, weights);
      setComparison(res);

      // Emit comparison_viewed event per CONTRACT §7
      bClient.trackAnyEvent(token, "comparison_viewed", scenarioId, {
        metrics_by_strategy: res.metrics,
      }).catch(() => {
        // Event tracking should not block UI
      });
    } catch (err) {
      if (err instanceof APIClientError) {
        setError(err.message);
      } else {
        setError("Failed to execute baseline strategy comparison.");
      }
    } finally {
      setLoading(false);
    }
  }, [token, scenarioId, weights]);

  // Auto-run on first open if a scenario is selected
  useEffect(() => {
    if (scenarioId && !comparison && !loading) {
      runComparison();
    }
  }, [scenarioId, comparison, loading, runComparison]);

  // Area coverage chart data
  const groupedChartData = useMemo(() => {
    if (!comparison || !comparison.results) return [];

    const jpAllocs = comparison.results.jalpriority?.allocations || [];
    const fcfsAllocs = comparison.results.fcfs?.allocations || [];
    const nfAllocs = comparison.results.nearest_first?.allocations || [];

    // Collect all unique area ids in order
    const areaIds = Array.from(
      new Set([
        ...jpAllocs.map((a) => a.area_id),
        ...fcfsAllocs.map((a) => a.area_id),
        ...nfAllocs.map((a) => a.area_id),
      ])
    ).sort();

    return areaIds.map((areaId) => {
      const jp = jpAllocs.find((a) => a.area_id === areaId);
      const fcfs = fcfsAllocs.find((a) => a.area_id === areaId);
      const nf = nfAllocs.find((a) => a.area_id === areaId);

      return {
        area_id: areaId,
        jalpriority: jp ? Math.round(jp.coverage_ratio * 100) : 0,
        fcfs: fcfs ? Math.round(fcfs.coverage_ratio * 100) : 0,
        nearest_first: nf ? Math.round(nf.coverage_ratio * 100) : 0,
      };
    });
  }, [comparison]);

  // Areas left with almost nothing (< 10% coverage)
  const starvingAreas = useMemo(() => {
    if (!comparison || !comparison.results) return { fcfs: [], nearest_first: [], jalpriority: [] };

    const getStarving = (strategy: string) => {
      const allocs = comparison.results[strategy]?.allocations || [];
      return allocs
        .filter((a) => a.coverage_ratio < 0.10)
        .map((a) => a.area_id);
    };

    return {
      fcfs: getStarving("fcfs"),
      nearest_first: getStarving("nearest_first"),
      jalpriority: getStarving("jalpriority"),
    };
  }, [comparison]);

  // Helper for computing deltas
  const formatDelta = (
    def: MetricRowDef,
    jpVal: number | null,
    baseVal: number | null
  ) => {
    if (jpVal === null || baseVal === null) return "—";
    const delta = jpVal - baseVal;
    if (delta === 0) return "0 (equal)";

    if (def.direction === "lower") {
      // Lower is better: negative delta means JalPriority was lower (better!)
      const sign = delta > 0 ? "+" : "";
      const text = `${sign}${delta.toLocaleString(undefined, { maximumFractionDigits: 1 })}`;
      return delta < 0 ? `${text} (better)` : `${text} (worse)`;
    } else {
      // Higher is better: positive delta means JalPriority was higher (better!)
      const sign = delta > 0 ? "+" : "";
      const text = `${sign}${(delta * 100).toFixed(1)}%`;
      return delta > 0 ? `${text} (better)` : `${text} (worse)`;
    }
  };

  return (
    <div className={styles.screenContainer} data-testid="compare-screen">
      {/* Top Header */}
      <div className={styles.headerRow}>
        <div>
          <h2 className={styles.title}>Strategy Comparison</h2>
          <p className={styles.subtitle}>
            Benchmarking JalPriority AI equity-driven optimization against FCFS (First-Come, First-Served) and
            Nearest-First dispatch heuristics on identical scenario resources.
          </p>
        </div>
        <button
          type="button"
          className={`${styles.btn} ${styles.btnPrimary}`}
          onClick={runComparison}
          disabled={loading || !scenarioId}
        >
          {loading ? "Running Comparison..." : "Run comparison"}
        </button>
      </div>

      {!scenarioId && (
        <div className={styles.calloutWarn}>
          Please select a scenario from the Crisis Snapshot screen to run the strategy comparison.
        </div>
      )}

      {error && (
        <div className={styles.calloutDanger} role="alert">
          {error}
        </div>
      )}

      {comparison && (
        <>
          {/* Callout: Areas left with almost nothing (< 10% coverage) */}
          <div className={styles.calloutDanger}>
            <div style={{ fontWeight: 700, fontSize: "0.95rem", marginBottom: "6px" }}>
              Critical Equity Impact: Areas left with almost nothing (&lt; 10% coverage)
            </div>
            <div style={{ display: "flex", gap: "24px", flexWrap: "wrap", fontSize: "0.85rem" }}>
              <div>
                <strong>FCFS Baseline:</strong>{" "}
                {starvingAreas.fcfs.length > 0 ? (
                  <span style={{ fontFamily: "var(--jp-mono)", color: "var(--jp-danger)" }}>
                    {starvingAreas.fcfs.join(", ")} ({starvingAreas.fcfs.length} areas starved)
                  </span>
                ) : (
                  <span>None</span>
                )}
              </div>
              <div>
                <strong>Nearest-First Baseline:</strong>{" "}
                {starvingAreas.nearest_first.length > 0 ? (
                  <span style={{ fontFamily: "var(--jp-mono)", color: "var(--jp-danger)" }}>
                    {starvingAreas.nearest_first.join(", ")} ({starvingAreas.nearest_first.length} areas starved)
                  </span>
                ) : (
                  <span>None</span>
                )}
              </div>
              <div>
                <strong>JalPriority AI:</strong>{" "}
                {starvingAreas.jalpriority.length > 0 ? (
                  <span style={{ fontFamily: "var(--jp-mono)", color: "var(--jp-danger)" }}>
                    {starvingAreas.jalpriority.join(", ")}
                  </span>
                ) : (
                  <span style={{ color: "var(--jp-ok)", fontWeight: 700 }}>
                    0 areas starved (universal baseline protection)
                  </span>
                )}
              </div>
            </div>
          </div>

          {/* Grouped Bar Chart: Per-Area Coverage */}
          <div className={styles.card}>
            <div className={styles.cardHeader}>
              <div>
                <h3 className={styles.cardTitle}>Per-Ward Coverage Comparison by Dispatch Strategy</h3>
                <p className={styles.cardSubtitle}>
                  Compares delivery percentages across wards. FCFS and Nearest-First heuristics exhaust water early on
                  favoured areas, leaving vulnerable wards completely unserved.
                </p>
              </div>
            </div>

            <div style={{ width: "100%", height: 320 }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={groupedChartData} margin={{ top: 10, right: 30, left: 10, bottom: 25 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--jp-border)" />
                  <XAxis dataKey="area_id" stroke="var(--jp-muted)" fontSize={12} />
                  <YAxis
                    stroke="var(--jp-muted)"
                    fontSize={12}
                    unit="%"
                    domain={[0, 100]}
                  />
                  <Tooltip
                    formatter={(val: number) => [`${val}%`, ""]}
                    contentStyle={{
                      backgroundColor: "var(--jp-surface)",
                      borderColor: "var(--jp-border)",
                      borderRadius: "var(--jp-radius)",
                      fontSize: "0.8rem",
                    }}
                  />
                  <Legend wrapperStyle={{ fontSize: "0.85rem" }} />
                  <Bar dataKey="fcfs" name="FCFS" fill="var(--jp-strat-fcfs)" radius={[2, 2, 0, 0]} />
                  <Bar dataKey="nearest_first" name="Nearest-First" fill="var(--jp-strat-nearest)" radius={[2, 2, 0, 0]} />
                  <Bar dataKey="jalpriority" name="JalPriority AI" fill="var(--jp-strat-jalpriority)" radius={[2, 2, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* 7-Metric Benchmark Table */}
          <div className={styles.card}>
            <div className={styles.cardHeader}>
              <div>
                <h3 className={styles.cardTitle}>Multi-Objective Performance Scorecard</h3>
                <p className={styles.cardSubtitle}>
                  Comparison across all 7 normative contract metrics. The winning strategy for each metric is highlighted.
                </p>
              </div>
            </div>

            <div className={styles.tableWrapper}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>Metric</th>
                    <th style={{ textAlign: "center" }}>Goal</th>
                    <th style={{ color: "var(--jp-strat-fcfs)" }}>FCFS Heuristic</th>
                    <th style={{ color: "var(--jp-strat-nearest)" }}>Nearest-First Heuristic</th>
                    <th style={{ color: "var(--jp-strat-jalpriority)", fontWeight: 700 }}>JalPriority AI</th>
                    <th>Δ vs FCFS</th>
                    <th>Δ vs Nearest-First</th>
                  </tr>
                </thead>
                <tbody>
                  {METRIC_ROWS.map((row) => {
                    const fcfsM = comparison.metrics.fcfs;
                    const nfM = comparison.metrics.nearest_first;
                    const jpM = comparison.metrics.jalpriority;

                    const bestStrategy = comparison.best_by_metric[row.key];

                    const fcfsVal = fcfsM ? (fcfsM[row.key] as number | null) : null;
                    const nfVal = nfM ? (nfM[row.key] as number | null) : null;
                    const jpVal = jpM ? (jpM[row.key] as number | null) : null;

                    const isConstraint = row.key === "constraint_violations";

                    return (
                      <tr key={row.key}>
                        <td>
                          <strong>{row.label}</strong>
                        </td>
                        <td style={{ textAlign: "center", fontFamily: "var(--jp-mono)", fontWeight: 600 }}>
                          {row.arrow} {row.direction === "lower" ? "Min" : "Max"}
                        </td>
                        <td
                          className={bestStrategy === "fcfs" ? styles.bestCell : undefined}
                          style={{ fontFamily: "var(--jp-mono)" }}
                        >
                          {isConstraint ? (
                            fcfsVal === 0 ? (
                              <span className={styles.badgeOk}>Pass (0)</span>
                            ) : (
                              <span className={styles.badgeDanger}>Fail ({fcfsVal})</span>
                            )
                          ) : (
                            row.format(fcfsVal)
                          )}
                        </td>
                        <td
                          className={bestStrategy === "nearest_first" ? styles.bestCell : undefined}
                          style={{ fontFamily: "var(--jp-mono)" }}
                        >
                          {isConstraint ? (
                            nfVal === 0 ? (
                              <span className={styles.badgeOk}>Pass (0)</span>
                            ) : (
                              <span className={styles.badgeDanger}>Fail ({nfVal})</span>
                            )
                          ) : (
                            row.format(nfVal)
                          )}
                        </td>
                        <td
                          className={bestStrategy === "jalpriority" ? styles.bestCell : undefined}
                          style={{ fontFamily: "var(--jp-mono)", fontWeight: 700 }}
                        >
                          {isConstraint ? (
                            jpVal === 0 ? (
                              <span className={styles.badgeOk}>Pass (0)</span>
                            ) : (
                              <span className={styles.badgeDanger}>Fail ({jpVal})</span>
                            )
                          ) : (
                            row.format(jpVal)
                          )}
                        </td>
                        <td style={{ fontFamily: "var(--jp-mono)", fontSize: "0.8rem" }}>
                          {formatDelta(row, jpVal, fcfsVal)}
                        </td>
                        <td style={{ fontFamily: "var(--jp-mono)", fontSize: "0.8rem" }}>
                          {formatDelta(row, jpVal, nfVal)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* Footer Disclaimer */}
          <div className={styles.disclaimerFooter}>
            Synthetic scenario, simulation results, same resources and same yardstick for all strategies.
          </div>
        </>
      )}
    </div>
  );
};
