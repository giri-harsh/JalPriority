import React, { useEffect, useState, useMemo, useCallback } from "react";
import { useShell } from "../shell/ShellContext";
import { bClient, WeightsResponse, LedgerResponse, APIClientError } from "../api/bClient";
import { Weights } from "../contract/types";
import {
  ResponsiveContainer,
  LineChart,
  Line,
  BarChart,
  Bar,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from "recharts";
import styles from "../styles/screens.module.css";

// Distinct color palette for up to 8 areas on the debt line chart
const AREA_COLORS = [
  "#0e7490", // Cyan / Accent
  "#b45309", // Amber
  "#15803d", // Green
  "#7c3aed", // Violet
  "#db2777", // Pink
  "#ea580c", // Orange
  "#0284c7", // Sky
  "#4b5563", // Gray
];

export const FairnessDebtScreen: React.FC = () => {
  const { token, scenarioId, plan, onGeneratePlan, setWeights: setShellWeights } = useShell();

  // Weights state
  const [weights, setLocalWeights] = useState<Weights>({
    urgency: 1.0,
    debt: 1.0,
    vulnerability: 0.5,
    travel: 20.0,
  });
  const [weightsMeta, setWeightsMeta] = useState<WeightsResponse | null>(null);
  const [weightsLoading, setWeightsLoading] = useState(false);
  const [weightsSaving, setWeightsSaving] = useState(false);
  const [statusMessage, setStatusMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);

  // Ledger state
  const [ledger, setLedger] = useState<LedgerResponse | null>(null);
  const [ledgerLoading, setLedgerLoading] = useState(false);
  const [ledgerError, setLedgerError] = useState<string | null>(null);

  // Determine current service_area_id
  const serviceAreaId = useMemo(() => {
    return plan?.service_area_id || "sa_01";
  }, [plan]);

  // Load weights from GET /b/v1/weights
  const loadWeights = useCallback(async () => {
    if (!token) return;
    setWeightsLoading(true);
    try {
      const data = await bClient.getWeights(token, serviceAreaId);
      setWeightsMeta(data);
      setLocalWeights(data.weights);
      setShellWeights(data.weights);
    } catch (err) {
      if (err instanceof APIClientError) {
        setStatusMessage({ type: "error", text: err.message });
      }
    } finally {
      setWeightsLoading(false);
    }
  }, [token, serviceAreaId, setShellWeights]);

  // Load ledger from GET /b/v1/ledger
  const loadLedger = useCallback(async () => {
    if (!token) return;
    setLedgerLoading(true);
    setLedgerError(null);
    try {
      const data = await bClient.getLedger(token, scenarioId, serviceAreaId);
      setLedger(data);
    } catch (err) {
      if (err instanceof APIClientError) {
        setLedgerError(err.message);
      } else {
        setLedgerError("Failed to fetch deprivation debt ledger.");
      }
    } finally {
      setLedgerLoading(false);
    }
  }, [token, scenarioId, serviceAreaId]);

  useEffect(() => {
    loadWeights();
  }, [loadWeights]);

  useEffect(() => {
    loadLedger();
  }, [loadLedger]);

  // Weight change handlers
  const handleSliderChange = (field: keyof Weights, value: number) => {
    setLocalWeights((prev) => ({
      ...prev,
      [field]: value,
    }));
  };

  // Save weights handler (PUT /b/v1/weights)
  const handleSaveWeights = async (): Promise<boolean> => {
    if (!token) return false;
    setWeightsSaving(true);
    setStatusMessage(null);
    try {
      const updated = await bClient.updateWeights(token, serviceAreaId, weights);
      setWeightsMeta(updated);
      setLocalWeights(updated.weights);
      setShellWeights(updated.weights);
      setStatusMessage({ type: "success", text: "Weights saved successfully." });
      return true;
    } catch (err) {
      if (err instanceof APIClientError) {
        setStatusMessage({ type: "error", text: err.message });
      } else {
        setStatusMessage({ type: "error", text: "Failed to save weights." });
      }
      return false;
    } finally {
      setWeightsSaving(false);
    }
  };

  // Save & regenerate plan
  const handleSaveAndRegenerate = async () => {
    const ok = await handleSaveWeights();
    if (ok) {
      await onGeneratePlan();
      // Reload ledger as re-solve or replanning could happen
      loadLedger();
    }
  };

  // Format Recharts debt line chart data
  const chartData = useMemo(() => {
    if (!ledger || !ledger.windows || ledger.windows.length === 0) return [];

    return ledger.windows.map((w) => {
      const point: Record<string, string | number> = {
        window: `Window ${w}`,
        window_index: w,
      };

      // Add each area's debt at window w
      ledger.areas.forEach((area) => {
        const item = area.series.find((s) => s.window_index === w);
        if (item) {
          point[area.area_id] = item.debt_litres;
          // Record source for tooltip distinction
          point[`${area.area_id}_source`] = item.source;
        } else {
          point[area.area_id] = 0;
        }
      });

      // Add total
      const tot = ledger.totals.find((t) => t.window_index === w);
      point["Total Debt"] = tot ? tot.debt_litres : 0;

      return point;
    });
  }, [ledger]);

  // Format coverage bar chart data
  const coverageData = useMemo(() => {
    if (!plan || !plan.result || !plan.result.allocations) return [];

    return plan.result.allocations.map((alloc) => ({
      area_id: alloc.area_id,
      coverage: alloc.coverage_ratio,
      coveragePct: Math.round(alloc.coverage_ratio * 100),
      allocated: alloc.allocated_litres,
      unmet: alloc.unmet_litres,
      shortfall: alloc.predicted_shortfall_litres,
      priority_weight: alloc.priority_weight,
    }));
  }, [plan]);

  const flags = weightsMeta?.flags || {
    fairness_weights: true,
    vulnerability: true,
    replan: true,
  };
  const defaults = weightsMeta?.defaults || {
    urgency: 1.0,
    debt: 1.0,
    vulnerability: 0.5,
    travel: 20.0,
  };

  return (
    <div className={styles.screenContainer} data-testid="fairness-screen">
      {/* Top Header */}
      <div className={styles.headerRow}>
        <div>
          <h2 className={styles.title}>Fairness &amp; Deprivation Debt</h2>
          <p className={styles.subtitle}>
            Multi-objective equity weighting controls, live mathematical priority formula, and historical debt progression.
          </p>
        </div>
      </div>

      {statusMessage && (
        <div
          className={`${styles.callout} ${
            statusMessage.type === "success" ? styles.calloutInfo : styles.calloutDanger
          }`}
          role="alert"
        >
          {statusMessage.text}
        </div>
      )}

      {/* Grid: Weight Controls (Left) & Formula / Debt Policy (Right) */}
      <div className={styles.grid2}>
        {/* Left Card: Weight Sliders */}
        <div className={styles.card}>
          <div className={styles.cardHeader}>
            <h3 className={styles.cardTitle}>Fairness Weights Policy</h3>
            {weightsLoading && <span className={styles.badgeMuted}>Loading...</span>}
          </div>

          {!flags.fairness_weights && (
            <div className={styles.calloutWarn}>
              Fairness weights adjustment is disabled by policy flag (B_FF_FAIRNESS_WEIGHTS=false). Default weights are enforced.
            </div>
          )}

          <div className={styles.sliderList}>
            {/* Urgency Slider */}
            <div className={styles.sliderRow}>
              <div className={styles.sliderHeader}>
                <span className={styles.sliderLabel}>Urgency Weight (α)</span>
                <div className={styles.sliderValues}>
                  <span className={styles.currentVal}>{weights.urgency.toFixed(1)}</span>
                  <span className={styles.defaultVal}>default: {defaults.urgency.toFixed(1)}</span>
                </div>
              </div>
              <input
                type="range"
                min="0"
                max="5"
                step="0.1"
                value={weights.urgency}
                disabled={!flags.fairness_weights || weightsSaving}
                onChange={(e) => handleSliderChange("urgency", parseFloat(e.target.value))}
                className={styles.sliderInput}
                aria-label="Urgency weight slider"
              />
              <p className={styles.sliderHelp}>
                Prioritizes areas with high ratio of unmet shortfall relative to total predicted need.
              </p>
            </div>

            {/* Debt Slider */}
            <div className={styles.sliderRow}>
              <div className={styles.sliderHeader}>
                <span className={styles.sliderLabel}>Deprivation Debt Weight (β)</span>
                <div className={styles.sliderValues}>
                  <span className={styles.currentVal}>{weights.debt.toFixed(1)}</span>
                  <span className={styles.defaultVal}>default: {defaults.debt.toFixed(1)}</span>
                </div>
              </div>
              <input
                type="range"
                min="0"
                max="5"
                step="0.1"
                value={weights.debt}
                disabled={!flags.fairness_weights || weightsSaving}
                onChange={(e) => handleSliderChange("debt", parseFloat(e.target.value))}
                className={styles.sliderInput}
                aria-label="Debt weight slider"
              />
              <p className={styles.sliderHelp}>
                Prioritizes wards experiencing persistent, compounding shortfall across past dispatch cycles.
              </p>
            </div>

            {/* Vulnerability Slider */}
            <div className={styles.sliderRow}>
              <div className={styles.sliderHeader}>
                <span className={styles.sliderLabel}>Vulnerability Weight (γ)</span>
                <div className={styles.sliderValues}>
                  <span className={styles.currentVal}>{weights.vulnerability.toFixed(1)}</span>
                  <span className={styles.defaultVal}>default: {defaults.vulnerability.toFixed(1)}</span>
                </div>
              </div>
              <input
                type="range"
                min="0"
                max="5"
                step="0.1"
                value={weights.vulnerability}
                disabled={!flags.fairness_weights || !flags.vulnerability || weightsSaving}
                onChange={(e) => handleSliderChange("vulnerability", parseFloat(e.target.value))}
                className={styles.sliderInput}
                aria-label="Vulnerability weight slider"
                title={!flags.vulnerability ? "Vulnerability weighting is disabled by policy flag (B_FF_VULNERABILITY=false)" : undefined}
              />
              {!flags.vulnerability ? (
                <p className={`${styles.sliderHelp} ${styles.unmetDanger}`}>
                  Vulnerability weighting is disabled by policy flag (B_FF_VULNERABILITY=false).
                </p>
              ) : (
                <p className={styles.sliderHelp}>
                  Weights socio-economic deprivation, healthcare access, and climate risk.
                </p>
              )}
            </div>

            {/* Travel Cost Slider */}
            <div className={styles.sliderRow}>
              <div className={styles.sliderHeader}>
                <span className={styles.sliderLabel}>Travel Distance Cost (δ)</span>
                <div className={styles.sliderValues}>
                  <span className={styles.currentVal}>{weights.travel.toFixed(0)}</span>
                  <span className={styles.defaultVal}>default: {defaults.travel.toFixed(0)}</span>
                </div>
              </div>
              <input
                type="range"
                min="0"
                max="500"
                step="1"
                value={weights.travel}
                disabled={!flags.fairness_weights || weightsSaving}
                onChange={(e) => handleSliderChange("travel", parseFloat(e.target.value))}
                className={styles.sliderInput}
                aria-label="Travel cost slider"
              />
              <p className={styles.sliderHelp}>
                Penalizes routing distance in litre-equivalents of unmet need per km travelled.
              </p>
            </div>
          </div>

          {/* Action Buttons */}
          <div className={styles.buttonRow}>
            <button
              type="button"
              className={`${styles.btn} ${styles.btnPrimary}`}
              onClick={handleSaveWeights}
              disabled={weightsSaving || !flags.fairness_weights}
            >
              {weightsSaving ? "Saving..." : "Save weights"}
            </button>
            <button
              type="button"
              className={`${styles.btn} ${styles.btnSuccess}`}
              onClick={handleSaveAndRegenerate}
              disabled={weightsSaving || !flags.fairness_weights}
            >
              Save &amp; regenerate plan
            </button>
          </div>
        </div>

        {/* Right Card: Formula & Debt Policy */}
        <div className={styles.card}>
          <div className={styles.cardHeader}>
            <h3 className={styles.cardTitle}>Priority Formula &amp; Debt Policy</h3>
          </div>

          {/* Live Priority Formula */}
          <div className={styles.formulaBox}>
            <div className={styles.formulaTitle}>Live Priority Formula (CONTRACT §5.11 F-5)</div>
            <div className={styles.formulaCode}>
              p = 1 + {weights.urgency.toFixed(1)}·U + {weights.debt.toFixed(1)}·D̂ +{" "}
              {flags.vulnerability ? weights.vulnerability.toFixed(1) : "0.0"}·V
            </div>
            <p style={{ margin: "8px 0 0 0", fontSize: "0.8rem", color: "var(--jp-muted)" }}>
              where U = shortfall / need, D̂ = normalized deprivation debt [0, 1], and V = vulnerability index [0, 1].
            </p>
          </div>

          {/* Policy Parameters */}
          <div className={styles.callout}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "8px" }}>
              <span>
                <strong>Debt Decay Factor (λ):</strong>{" "}
                <span style={{ fontFamily: "var(--jp-mono)", color: "var(--jp-accent)" }}>
                  {ledger?.decay ?? 0.3}
                </span>
              </span>
              <span>
                <strong>Debt Cap:</strong>{" "}
                <span style={{ fontFamily: "var(--jp-mono)", color: "var(--jp-accent)" }}>
                  {ledger?.cap_litres ? `${ledger.cap_litres.toLocaleString()} L` : "None"}
                </span>
              </span>
            </div>
            <p style={{ margin: 0, fontSize: "0.85rem", color: "var(--jp-text)", lineHeight: 1.45 }}>
              Deprivation debt measures cumulative unserved water deficit across consecutive dispatch windows to prevent
              persistent neglect of vulnerable areas. In each window, existing debt decays by policy factor (1 − λ)
              before newly unserved shortfall is added.
            </p>
          </div>

          {/* Service Area Info */}
          <div style={{ fontSize: "0.85rem", color: "var(--jp-muted)" }}>
            Service Area: <strong style={{ color: "var(--jp-text)" }}>{serviceAreaId}</strong>
            {scenarioId && (
              <>
                {" "}· Scenario: <strong style={{ color: "var(--jp-text)" }}>{scenarioId}</strong>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Debt History Line Chart */}
      <div className={styles.card}>
        <div className={styles.cardHeader}>
          <div>
            <h3 className={styles.cardTitle}>Deprivation Debt Progression Over Dispatch Windows</h3>
            <p className={styles.cardSubtitle}>
              Tracks historical accumulation and plan-driven replenishment. Solid points denote historical debt; dashed
              transitions denote approved plan updates.
            </p>
          </div>
          {ledgerLoading && <span className={styles.badgeMuted}>Loading ledger...</span>}
        </div>

        {ledgerError ? (
          <div className={styles.calloutDanger}>{ledgerError}</div>
        ) : chartData.length === 0 ? (
          <div style={{ padding: "40px", textAlign: "center", color: "var(--jp-muted)" }}>
            No debt ledger data available for this service area.
          </div>
        ) : (
          <div style={{ width: "100%", height: 320 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={{ top: 10, right: 30, left: 20, bottom: 10 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--jp-border)" />
                <XAxis dataKey="window" stroke="var(--jp-muted)" fontSize={12} />
                <YAxis stroke="var(--jp-muted)" fontSize={12} unit=" L" />
                <Tooltip
                  formatter={(val: number, name: string, item: any) => {
                    const src = item?.payload?.[`${name}_source`];
                    const srcLabel = src ? ` (${src === "approved_plan" ? "Approved Plan" : "History"})` : "";
                    return [`${val.toLocaleString()} L${srcLabel}`, name];
                  }}
                  contentStyle={{
                    backgroundColor: "var(--jp-surface)",
                    borderColor: "var(--jp-border)",
                    borderRadius: "var(--jp-radius)",
                    fontSize: "0.8rem",
                  }}
                />
                <Legend wrapperStyle={{ fontSize: "0.8rem" }} />

                {/* Individual Area Lines */}
                {ledger?.areas.map((area, idx) => (
                  <Line
                    key={area.area_id}
                    type="monotone"
                    dataKey={area.area_id}
                    name={area.name || area.area_id}
                    stroke={AREA_COLORS[idx % AREA_COLORS.length]}
                    strokeWidth={2}
                    dot={{ r: 4 }}
                    activeDot={{ r: 6 }}
                  />
                ))}

                {/* Total Line */}
                <Line
                  type="monotone"
                  dataKey="Total Debt"
                  name="Total Area Debt"
                  stroke="#111827"
                  strokeWidth={3}
                  strokeDasharray="4 4"
                  dot={{ r: 5 }}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      {/* Plan Coverage and Area Details */}
      <div className={styles.card}>
        <div className={styles.cardHeader}>
          <div>
            <h3 className={styles.cardTitle}>Current Plan Allocation &amp; Ward Coverage</h3>
            <p className={styles.cardSubtitle}>
              Per-area delivery ratios under current plan. Coverage ratios below 0.25 (25%) are highlighted in red.
            </p>
          </div>
        </div>

        {!plan ? (
          <div style={{ padding: "40px", textAlign: "center", color: "var(--jp-muted)" }}>
            No dispatch plan is currently active for this scenario. Generate or select a plan to view coverage breakdown.
          </div>
        ) : (
          <>
            {/* Coverage Bar Chart */}
            <div style={{ width: "100%", height: 260 }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={coverageData} margin={{ top: 10, right: 30, left: 10, bottom: 25 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--jp-border)" />
                  <XAxis dataKey="area_id" stroke="var(--jp-muted)" fontSize={12} />
                  <YAxis
                    stroke="var(--jp-muted)"
                    fontSize={12}
                    domain={[0, 1]}
                    tickFormatter={(v) => `${Math.round(v * 100)}%`}
                  />
                  <Tooltip
                    formatter={(val: number) => [`${(val * 100).toFixed(1)}%`, "Coverage Ratio"]}
                    contentStyle={{
                      backgroundColor: "var(--jp-surface)",
                      borderColor: "var(--jp-border)",
                      borderRadius: "var(--jp-radius)",
                      fontSize: "0.8rem",
                    }}
                  />
                  <Bar dataKey="coverage" name="Coverage Ratio" radius={[4, 4, 0, 0]}>
                    {coverageData.map((entry, index) => (
                      <Cell
                        key={`cell-${index}`}
                        fill={entry.coverage < 0.25 ? "var(--jp-danger)" : "var(--jp-accent)"}
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>

            {/* Area Table */}
            <div className={styles.tableWrapper}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>Area</th>
                    <th>Current Debt</th>
                    <th>Priority Weight (p)</th>
                    <th>Coverage Ratio</th>
                    <th>Allocated / Shortfall</th>
                    <th>Unmet Deficit</th>
                  </tr>
                </thead>
                <tbody>
                  {plan.result.allocations.map((alloc) => {
                    const areaLedger = ledger?.areas.find((a) => a.area_id === alloc.area_id);
                    const currentDebt = areaLedger?.current_debt_litres ?? alloc.debt_litres_used ?? 0;
                    const isLowCoverage = alloc.coverage_ratio < 0.25;

                    return (
                      <tr key={alloc.area_id}>
                        <td>
                          <strong>{areaLedger?.name || alloc.area_id}</strong>
                          <div style={{ fontSize: "0.75rem", color: "var(--jp-muted)" }}>{alloc.area_id}</div>
                        </td>
                        <td style={{ fontFamily: "var(--jp-mono)" }}>
                          {currentDebt.toLocaleString()} L
                        </td>
                        <td style={{ fontFamily: "var(--jp-mono)", fontWeight: 600 }}>
                          {alloc.priority_weight.toFixed(4)}
                        </td>
                        <td>
                          <span
                            className={isLowCoverage ? styles.coverageLow : undefined}
                            style={{ fontFamily: "var(--jp-mono)" }}
                          >
                            {(alloc.coverage_ratio * 100).toFixed(1)}% ({alloc.coverage_ratio.toFixed(4)})
                          </span>
                        </td>
                        <td style={{ fontFamily: "var(--jp-mono)" }}>
                          {alloc.allocated_litres.toLocaleString()} / {alloc.predicted_shortfall_litres.toLocaleString()} L
                        </td>
                        <td style={{ fontFamily: "var(--jp-mono)" }}>
                          <span className={alloc.unmet_litres > 0 ? styles.unmetDanger : undefined}>
                            {alloc.unmet_litres.toLocaleString()} L
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  );
};
