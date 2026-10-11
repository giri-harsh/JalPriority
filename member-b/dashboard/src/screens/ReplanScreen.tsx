import React, { useEffect, useState, useMemo, useCallback } from "react";
import { useNavigate, Link } from "react-router-dom";
import { useShell } from "../shell/ShellContext";
import {
  bClient,
  ReplanResponse,
  ScenarioDetails,
  APIClientError,
} from "../api/bClient";
import { PlanRecord } from "../contract/types";
import { ApprovalBar } from "../shell/ApprovalBar";
import styles from "../styles/screens.module.css";

type DisruptionType = "tanker_down" | "supply_cut" | "heat_alert";

export const ReplanScreen: React.FC = () => {
  const {
    token,
    scenarioId,
    plan,
    updatePlan,
    setScenarioId,
  } = useShell();
  const navigate = useNavigate();

  // Scenario details (for available tankers, current heat level, supply)
  const [scenarioDetails, setScenarioDetails] = useState<ScenarioDetails | null>(null);
  const [scenarioLoading, setScenarioLoading] = useState(false);

  // Disruption inputs
  const [disruptionType, setDisruptionType] = useState<DisruptionType>("tanker_down");
  const [selectedTanker, setSelectedTanker] = useState<string>("");
  const [supplyCutFraction, setSupplyCutFraction] = useState<number>(0.3); // 30% default
  const [selectedHeatAlert, setSelectedHeatAlert] = useState<number>(3);

  // Replan execution state
  const [replanResult, setReplanResult] = useState<ReplanResponse | null>(null);
  const [replanning, setReplanning] = useState(false);
  const [replanError, setReplanError] = useState<string | null>(null);

  // Fetch scenario details for disruption parameters
  const loadScenarioDetails = useCallback(async () => {
    if (!token || !scenarioId) return;
    setScenarioLoading(true);
    try {
      const data = await bClient.getScenario(token, scenarioId);
      setScenarioDetails(data);

      // Initialize selected tanker to first available
      const availTankers = data.tankers.filter((t) => t.is_available);
      if (availTankers.length > 0) {
        setSelectedTanker(availTankers[0].tanker_id);
      }

      // Initialize heat alert to something different from current
      const currentLevel = data.heat?.alert_level ?? 2;
      const otherLevels = [0, 1, 2, 3].filter((lvl) => lvl !== currentLevel);
      if (otherLevels.length > 0) {
        setSelectedHeatAlert(otherLevels[otherLevels.length - 1]);
      }
    } catch {
      // Non-blocking fallback
    } finally {
      setScenarioLoading(false);
    }
  }, [token, scenarioId]);

  useEffect(() => {
    loadScenarioDetails();
  }, [loadScenarioDetails]);

  // Current alert level from scenario
  const currentHeatAlert = scenarioDetails?.heat?.alert_level ?? 2;

  // Available tankers for tanker_down disruption
  const availableTankers = useMemo(() => {
    return scenarioDetails?.tankers.filter((t) => t.is_available) || [];
  }, [scenarioDetails]);

  // Handle Apply & Replan
  const handleApplyReplan = async () => {
    if (!token || !plan) return;
    setReplanning(true);
    setReplanError(null);

    let params: Record<string, unknown> = {};
    if (disruptionType === "tanker_down") {
      params = { tanker_id: selectedTanker || availableTankers[0]?.tanker_id || "tk_01" };
    } else if (disruptionType === "supply_cut") {
      params = { fraction: Math.round(supplyCutFraction * 100) / 100 };
    } else if (disruptionType === "heat_alert") {
      params = { alert_level: selectedHeatAlert };
    }

    try {
      const res = await bClient.replan(token, plan.plan_id, {
        type: disruptionType,
        params,
      });
      setReplanResult(res);
    } catch (err) {
      if (err instanceof APIClientError) {
        setReplanError(err.message);
      } else {
        setReplanError("Failed to compute dynamic replan.");
      }
    } finally {
      setReplanning(false);
    }
  };

  // Handle Open Revised Plan
  const handleOpenRevisedPlan = async () => {
    if (!replanResult) return;
    // Set derived scenario and new plan into shell state
    await setScenarioId(replanResult.derived_scenario_id);
    updatePlan(replanResult.new_plan);
    navigate("/plan");
  };

  // Decision success callback for the new plan
  const handleNewPlanDecision = (updatedPlan: PlanRecord) => {
    if (replanResult) {
      setReplanResult({
        ...replanResult,
        new_plan: updatedPlan,
      });
    }
  };

  // Helper for change status styling
  const getChangeBadge = (change: string) => {
    switch (change) {
      case "dropped":
        return <span className={`${styles.badge} ${styles.badgeDanger}`}>Dropped</span>;
      case "decreased":
        return <span className={`${styles.badge} ${styles.badgeWarn}`}>Decreased</span>;
      case "newly_served":
        return <span className={`${styles.badge} ${styles.badgeOk}`}>Newly Served</span>;
      case "increased":
        return <span className={`${styles.badge} ${styles.badgeOk}`}>Increased</span>;
      case "reassigned":
        return <span className={`${styles.badge} ${styles.badgeWarn}`}>Reassigned</span>;
      case "unchanged":
      default:
        return <span className={`${styles.badge} ${styles.badgeMuted}`}>Unchanged</span>;
    }
  };

  // Empty state if no plan exists
  if (!plan) {
    return (
      <div className={styles.screenContainer} data-testid="replan-screen">
        <div className={styles.headerRow}>
          <div>
            <h2 className={styles.title}>Disruption Replanning</h2>
            <p className={styles.subtitle}>
              Dynamic crisis event response, solver re-optimization under 5 seconds, and full allocation diff breakdown.
            </p>
          </div>
        </div>

        <div className={styles.card} style={{ textAlign: "center", padding: "60px 20px" }}>
          <div style={{ fontSize: "1.1rem", fontWeight: 600, color: "var(--jp-text)", marginBottom: "8px" }}>
            No Active Dispatch Plan
          </div>
          <p style={{ color: "var(--jp-muted)", maxWidth: "500px", margin: "0 auto 20px auto" }}>
            To simulate disruptions (such as tanker breakdowns, municipal supply cuts, or heat alerts), you must first
            generate or load a plan in the Dispatch Plan screen.
          </p>
          <div>
            <Link to="/plan" className={`${styles.btn} ${styles.btnPrimary}`}>
              Go to Dispatch Plan tab
            </Link>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.screenContainer} data-testid="replan-screen">
      {/* Top Header */}
      <div className={styles.headerRow}>
        <div>
          <h2 className={styles.title}>Disruption Replanning</h2>
          <p className={styles.subtitle}>
            Dynamic crisis event simulation, solver re-optimization under 5 seconds, and allocation delta diff audit.
          </p>
        </div>
      </div>

      {/* Plan Summary Banner */}
      <div className={styles.card}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "12px" }}>
          <div>
            <span style={{ fontSize: "0.8rem", color: "var(--jp-muted)", textTransform: "uppercase", letterSpacing: "0.05em", fontWeight: 700 }}>
              Active Baseline Plan
            </span>
            <div style={{ fontSize: "1.1rem", fontWeight: 700, fontFamily: "var(--jp-mono)", color: "var(--jp-accent)" }}>
              {plan.plan_id}
            </div>
          </div>
          <div style={{ display: "flex", gap: "20px", flexWrap: "wrap", fontSize: "0.85rem" }}>
            <div>
              <span style={{ color: "var(--jp-muted)" }}>Scenario:</span>{" "}
              <strong>{plan.scenario_id}</strong>
            </div>
            <div>
              <span style={{ color: "var(--jp-muted)" }}>Status:</span>{" "}
              <span className={styles.badgeOk}>{plan.status}</span>
            </div>
            <div>
              <span style={{ color: "var(--jp-muted)" }}>Total Supply:</span>{" "}
              <strong>{plan.result.totals.supply_available_litres.toLocaleString()} L</strong>
            </div>
            <div>
              <span style={{ color: "var(--jp-muted)" }}>Delivered:</span>{" "}
              <strong>{plan.result.totals.allocated_litres.toLocaleString()} L</strong>
            </div>
            <div>
              <span style={{ color: "var(--jp-muted)" }}>Fleet Travel:</span>{" "}
              <strong>{plan.result.totals.travel_km.toFixed(1)} km</strong>
            </div>
          </div>
        </div>
      </div>

      {/* Disruption Simulation Controls */}
      <div className={styles.card}>
        <div className={styles.cardHeader}>
          <h3 className={styles.cardTitle}>Simulate Crisis Event Disruption</h3>
          {scenarioLoading && <span className={styles.badgeMuted}>Loading scenario details...</span>}
        </div>

        <div style={{ display: "flex", gap: "16px", flexWrap: "wrap" }}>
          {/* Disruption Type Radios */}
          <label style={{ display: "flex", alignItems: "center", gap: "8px", cursor: "pointer", fontSize: "0.9rem" }}>
            <input
              type="radio"
              name="disruptionType"
              value="tanker_down"
              checked={disruptionType === "tanker_down"}
              onChange={() => setDisruptionType("tanker_down")}
            />
            <strong>Tanker Down</strong> (Vehicle Breakdown)
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: "8px", cursor: "pointer", fontSize: "0.9rem" }}>
            <input
              type="radio"
              name="disruptionType"
              value="supply_cut"
              checked={disruptionType === "supply_cut"}
              onChange={() => setDisruptionType("supply_cut")}
            />
            <strong>Supply Cut</strong> (Bulk Reservoir Reduction)
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: "8px", cursor: "pointer", fontSize: "0.9rem" }}>
            <input
              type="radio"
              name="disruptionType"
              value="heat_alert"
              checked={disruptionType === "heat_alert"}
              onChange={() => setDisruptionType("heat_alert")}
            />
            <strong>Heat Alert</strong> (Demand Surge)
          </label>
        </div>

        {/* Dynamic Disruption Parameter Controls */}
        <div style={{ backgroundColor: "var(--jp-bg)", padding: "16px", borderRadius: "var(--jp-radius)", border: "1px solid var(--jp-border)" }}>
          {disruptionType === "tanker_down" && (
            <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
              <label style={{ fontSize: "0.85rem", fontWeight: 600 }}>Select Tanker Taken Out of Service:</label>
              <select
                value={selectedTanker}
                onChange={(e) => setSelectedTanker(e.target.value)}
                style={{
                  padding: "8px 12px",
                  borderRadius: "var(--jp-radius)",
                  border: "1px solid var(--jp-border)",
                  fontSize: "0.9rem",
                  maxWidth: "320px",
                }}
                aria-label="Select tanker taken out of service"
              >
                {availableTankers.map((t) => (
                  <option key={t.tanker_id} value={t.tanker_id}>
                    {t.tanker_id} ({t.capacity_litres.toLocaleString()} L capacity, max {t.max_route_km} km)
                  </option>
                ))}
              </select>
              <p className={styles.sliderHelp}>
                Simulates an unexpected mechanical failure or road incident taking this tanker off duty.
              </p>
            </div>
          )}

          {disruptionType === "supply_cut" && (
            <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", maxWidth: "400px" }}>
                <label style={{ fontSize: "0.85rem", fontWeight: 600 }}>Supply Reduction Fraction:</label>
                <strong style={{ fontFamily: "var(--jp-mono)", color: "var(--jp-danger)" }}>
                  {Math.round(supplyCutFraction * 100)}% cut
                </strong>
              </div>
              <input
                type="range"
                min="0.05"
                max="0.90"
                step="0.05"
                value={supplyCutFraction}
                onChange={(e) => setSupplyCutFraction(parseFloat(e.target.value))}
                style={{ maxWidth: "400px" }}
                aria-label="Supply reduction fraction slider"
              />
              <p className={styles.sliderHelp}>
                Reduces total bulk water supply from reservoir by {Math.round(supplyCutFraction * 100)}% (Range: 5%–90%).
              </p>
            </div>
          )}

          {disruptionType === "heat_alert" && (
            <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
              <label style={{ fontSize: "0.85rem", fontWeight: 600 }}>
                Escalate Heat Alert Level (Current: Level {currentHeatAlert}):
              </label>
              <select
                value={selectedHeatAlert}
                onChange={(e) => setSelectedHeatAlert(parseInt(e.target.value, 10))}
                style={{
                  padding: "8px 12px",
                  borderRadius: "var(--jp-radius)",
                  border: "1px solid var(--jp-border)",
                  fontSize: "0.9rem",
                  maxWidth: "320px",
                }}
                aria-label="Escalate heat alert level"
              >
                {[0, 1, 2, 3]
                  .filter((lvl) => lvl !== currentHeatAlert)
                  .map((lvl) => (
                    <option key={lvl} value={lvl}>
                      Level {lvl} {lvl > currentHeatAlert ? "(Demand surge)" : "(Demand easing)"}
                    </option>
                  ))}
              </select>
              <p className={styles.sliderHelp}>
                Adjusts ambient temperatures and heat index, triggering heightened demand forecasts across all wards.
              </p>
            </div>
          )}
        </div>

        {/* Action Button */}
        <div>
          <button
            type="button"
            className={`${styles.btn} ${styles.btnPrimary}`}
            onClick={handleApplyReplan}
            disabled={replanning}
          >
            {replanning ? "Re-optimizing Plan (< 5s)..." : "Apply & replan"}
          </button>
        </div>
      </div>

      {replanError && (
        <div className={styles.calloutDanger} role="alert">
          {replanError}
        </div>
      )}

      {/* Replan Results & Diff Display */}
      {replanResult && (
        <>
          {/* Top KPI Card: Replanning Latency & Summary */}
          <div className={styles.grid3}>
            {/* Latency Readout Card */}
            <div
              className={`${styles.readoutCard} ${
                replanResult.replanning_time_ms < 5000 ? styles.readoutFast : styles.readoutSlow
              }`}
            >
              <div className={styles.readoutNumber}>
                {replanResult.replanning_time_ms.toLocaleString()} ms
              </div>
              <div className={styles.readoutLabel}>
                Replanning Latency {replanResult.replanning_time_ms < 5000 ? "(< 5s Target Met)" : "(> 5s Target)"}
              </div>
            </div>

            {/* Litres Moved Card */}
            <div className={styles.card} style={{ justifyContent: "center", alignItems: "center", textAlign: "center" }}>
              <div style={{ fontSize: "1.8rem", fontWeight: 800, fontFamily: "var(--jp-mono)", color: "var(--jp-accent)" }}>
                {replanResult.diff.summary.litres_moved.toLocaleString()} L
              </div>
              <div style={{ fontSize: "0.8rem", fontWeight: 700, color: "var(--jp-muted)", textTransform: "uppercase" }}>
                Total Litres Reallocated
              </div>
            </div>

            {/* Areas Changed Card */}
            <div className={styles.card} style={{ justifyContent: "center", alignItems: "center", textAlign: "center" }}>
              <div style={{ fontSize: "1.8rem", fontWeight: 800, fontFamily: "var(--jp-mono)", color: "var(--jp-text)" }}>
                {replanResult.diff.summary.areas_changed}
              </div>
              <div style={{ fontSize: "0.8rem", fontWeight: 700, color: "var(--jp-muted)", textTransform: "uppercase" }}>
                Wards Affected by Diff
              </div>
            </div>
          </div>

          {/* Metrics Before vs After Comparison */}
          <div className={styles.card}>
            <div className={styles.cardHeader}>
              <h3 className={styles.cardTitle}>Plan Metrics Shift (Before vs After Disruption)</h3>
            </div>

            <div className={styles.tableWrapper}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>Metric</th>
                    <th>Before Disruption</th>
                    <th>After Replanning</th>
                    <th>Net Shift</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td><strong>Priority-Weighted Unmet (PWU)</strong></td>
                    <td style={{ fontFamily: "var(--jp-mono)" }}>
                      {replanResult.diff.summary.metrics_before.priority_weighted_unmet.toLocaleString()}
                    </td>
                    <td style={{ fontFamily: "var(--jp-mono)" }}>
                      {replanResult.diff.summary.metrics_after.priority_weighted_unmet.toLocaleString()}
                    </td>
                    <td style={{ fontFamily: "var(--jp-mono)" }}>
                      {(
                        replanResult.diff.summary.metrics_after.priority_weighted_unmet -
                        replanResult.diff.summary.metrics_before.priority_weighted_unmet
                      ).toFixed(1)}
                    </td>
                  </tr>
                  <tr>
                    <td><strong>Deprivation Debt Remaining</strong></td>
                    <td style={{ fontFamily: "var(--jp-mono)" }}>
                      {replanResult.diff.summary.metrics_before.deprivation_debt_remaining_litres.toLocaleString()} L
                    </td>
                    <td style={{ fontFamily: "var(--jp-mono)" }}>
                      {replanResult.diff.summary.metrics_after.deprivation_debt_remaining_litres.toLocaleString()} L
                    </td>
                    <td style={{ fontFamily: "var(--jp-mono)" }}>
                      {(
                        replanResult.diff.summary.metrics_after.deprivation_debt_remaining_litres -
                        replanResult.diff.summary.metrics_before.deprivation_debt_remaining_litres
                      ).toLocaleString()} L
                    </td>
                  </tr>
                  <tr>
                    <td><strong>Total Unmet Need</strong></td>
                    <td style={{ fontFamily: "var(--jp-mono)" }}>
                      {replanResult.diff.summary.metrics_before.total_unmet_litres.toLocaleString()} L
                    </td>
                    <td style={{ fontFamily: "var(--jp-mono)" }}>
                      {replanResult.diff.summary.metrics_after.total_unmet_litres.toLocaleString()} L
                    </td>
                    <td style={{ fontFamily: "var(--jp-mono)" }}>
                      {(
                        replanResult.diff.summary.metrics_after.total_unmet_litres -
                        replanResult.diff.summary.metrics_before.total_unmet_litres
                      ).toLocaleString()} L
                    </td>
                  </tr>
                  <tr>
                    <td><strong>Minimum Ward Coverage</strong></td>
                    <td style={{ fontFamily: "var(--jp-mono)" }}>
                      {(replanResult.diff.summary.metrics_before.min_coverage_ratio * 100).toFixed(1)}%
                    </td>
                    <td style={{ fontFamily: "var(--jp-mono)" }}>
                      {(replanResult.diff.summary.metrics_after.min_coverage_ratio * 100).toFixed(1)}%
                    </td>
                    <td style={{ fontFamily: "var(--jp-mono)" }}>
                      {(
                        (replanResult.diff.summary.metrics_after.min_coverage_ratio -
                          replanResult.diff.summary.metrics_before.min_coverage_ratio) *
                        100
                      ).toFixed(1)}%
                    </td>
                  </tr>
                  <tr>
                    <td><strong>Fleet Travel Distance</strong></td>
                    <td style={{ fontFamily: "var(--jp-mono)" }}>
                      {replanResult.diff.summary.metrics_before.total_travel_km.toFixed(1)} km
                    </td>
                    <td style={{ fontFamily: "var(--jp-mono)" }}>
                      {replanResult.diff.summary.metrics_after.total_travel_km.toFixed(1)} km
                    </td>
                    <td style={{ fontFamily: "var(--jp-mono)" }}>
                      {(
                        replanResult.diff.summary.metrics_after.total_travel_km -
                        replanResult.diff.summary.metrics_before.total_travel_km
                      ).toFixed(1)} km
                    </td>
                  </tr>
                  <tr>
                    <td><strong>Constraint Violations</strong></td>
                    <td style={{ fontFamily: "var(--jp-mono)" }}>
                      {replanResult.diff.summary.metrics_before.constraint_violations === 0 ? (
                        <span className={styles.badgeOk}>Pass (0)</span>
                      ) : (
                        <span className={styles.badgeDanger}>{replanResult.diff.summary.metrics_before.constraint_violations}</span>
                      )}
                    </td>
                    <td style={{ fontFamily: "var(--jp-mono)" }}>
                      {replanResult.diff.summary.metrics_after.constraint_violations === 0 ? (
                        <span className={styles.badgeOk}>Pass (0)</span>
                      ) : (
                        <span className={styles.badgeDanger}>{replanResult.diff.summary.metrics_after.constraint_violations}</span>
                      )}
                    </td>
                    <td style={{ fontFamily: "var(--jp-mono)" }}>0</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          {/* Allocation Delta Diff Table */}
          <div className={styles.card}>
            <div className={styles.cardHeader}>
              <h3 className={styles.cardTitle}>Ward Allocation Diff Breakdown</h3>
            </div>

            <div className={styles.tableWrapper}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>Ward Area</th>
                    <th>Litres Before</th>
                    <th>Litres After</th>
                    <th>Delta</th>
                    <th>Tankers (Before → After)</th>
                    <th>Stop Order (Before → After)</th>
                    <th>Change Status</th>
                  </tr>
                </thead>
                <tbody>
                  {replanResult.diff.areas.map((area) => {
                    const isNeg = area.delta < 0;
                    const isPos = area.delta > 0;

                    return (
                      <tr key={area.area_id}>
                        <td>
                          <strong style={{ fontFamily: "var(--jp-mono)" }}>{area.area_id}</strong>
                        </td>
                        <td style={{ fontFamily: "var(--jp-mono)" }}>
                          {area.litres_before.toLocaleString()} L
                        </td>
                        <td style={{ fontFamily: "var(--jp-mono)" }}>
                          {area.litres_after.toLocaleString()} L
                        </td>
                        <td
                          style={{
                            fontFamily: "var(--jp-mono)",
                            fontWeight: 700,
                            color: isNeg ? "var(--jp-danger)" : isPos ? "var(--jp-ok)" : "var(--jp-muted)",
                          }}
                        >
                          {isPos ? `+${area.delta.toLocaleString()}` : area.delta.toLocaleString()} L
                        </td>
                        <td style={{ fontFamily: "var(--jp-mono)", fontSize: "0.8rem" }}>
                          {area.tankers_before.length > 0 ? area.tankers_before.join(", ") : "None"} →{" "}
                          {area.tankers_after.length > 0 ? area.tankers_after.join(", ") : "None"}
                        </td>
                        <td style={{ fontFamily: "var(--jp-mono)", fontSize: "0.8rem" }}>
                          {area.order_before.length > 0 ? `#${area.order_before.join(", #")}` : "—"} →{" "}
                          {area.order_after.length > 0 ? `#${area.order_after.join(", #")}` : "—"}
                        </td>
                        <td>{getChangeBadge(area.change)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* Revised Plan Controls & Approval */}
          <div className={styles.card}>
            <div className={styles.cardHeader}>
              <div>
                <h3 className={styles.cardTitle}>Revised Plan Review &amp; Human Approval</h3>
                <p className={styles.cardSubtitle}>
                  Plan ID: <span style={{ fontFamily: "var(--jp-mono)", fontWeight: 700 }}>{replanResult.new_plan.plan_id}</span> ·
                  Derived Scenario: <span style={{ fontFamily: "var(--jp-mono)" }}>{replanResult.derived_scenario_id}</span>
                </p>
              </div>
              <button
                type="button"
                className={`${styles.btn} ${styles.btnPrimary}`}
                onClick={handleOpenRevisedPlan}
              >
                Open revised plan in Dispatch tab
              </button>
            </div>

            {/* In-place ApprovalBar for Revised Plan */}
            <ApprovalBar
              plan={replanResult.new_plan}
              onDecisionSuccess={handleNewPlanDecision}
            />
          </div>
        </>
      )}
    </div>
  );
};
