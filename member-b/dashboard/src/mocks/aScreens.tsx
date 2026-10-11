import React, { useEffect, useState } from "react";
import {
  SnapshotScreenProps,
  DispatchPlanScreenProps,
} from "../contract/types";
import { bClient, ScenarioSummary } from "../api/bClient";

export const A_SCREENS_CONTRACT_VERSION = "1.0.0";

export const SnapshotScreen: React.FC<SnapshotScreenProps> = ({
  token,
  scenarioId,
  onScenarioChange,
  onTrack,
}) => {
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [selectedScenario, setSelectedScenario] = useState<ScenarioSummary | null>(null);

  useEffect(() => {
    let mounted = true;
    const fetchScenarios = async () => {
      try {
        const data = await bClient.listScenarios(token);
        if (mounted) {
          setScenarios(data.scenarios);
          if (data.scenarios.length > 0 && !scenarioId) {
            onScenarioChange(data.scenarios[0].scenario_id);
          }
        }
      } catch {
        // Handled silently or empty state
      } finally {
        if (mounted) setLoading(false);
      }
    };

    fetchScenarios();
    return () => {
      mounted = false;
    };
  }, [token, scenarioId, onScenarioChange]);

  useEffect(() => {
    if (scenarios.length > 0 && scenarioId) {
      const match = scenarios.find((s) => s.scenario_id === scenarioId) || null;
      setSelectedScenario(match);
      if (match) {
        onTrack({
          name: "scenario_loaded",
          scenario_id: match.scenario_id,
          props: {
            n_areas: match.n_areas,
            supply_litres: match.supply_litres,
            tankers_available: match.tankers_available,
          },
        });
      }
    }
  }, [scenarios, scenarioId, onTrack]);

  return (
    <div style={{ padding: "8px", maxWidth: "1200px" }} data-testid="snapshot-screen-mock">
      <div style={{ marginBottom: "20px" }}>
        <h2 style={{ margin: "0 0 8px 0" }}>Crisis Snapshot (Mock A)</h2>
        <p style={{ color: "var(--jp-muted)", margin: 0 }}>
          Overview of regional water deficit, demand estimation, and active emergency conditions.
        </p>
      </div>

      <div style={{ background: "var(--jp-surface)", padding: "16px", borderRadius: "var(--jp-radius)", border: "1px solid var(--jp-border)", marginBottom: "20px" }}>
        <label htmlFor="scenario-select" style={{ display: "block", fontWeight: 600, marginBottom: "8px" }}>
          Select Scenario:
        </label>
        {loading ? (
          <div>Loading scenarios...</div>
        ) : (
          <select
            id="scenario-select"
            value={scenarioId || ""}
            onChange={(e) => onScenarioChange(e.target.value)}
            style={{
              padding: "8px 12px",
              borderRadius: "var(--jp-radius)",
              border: "1px solid var(--jp-border)",
              fontFamily: "var(--jp-font)",
              fontSize: "0.95rem",
              minWidth: "260px",
            }}
            data-testid="scenario-select"
          >
            {scenarios.map((sc) => (
              <option key={sc.scenario_id} value={sc.scenario_id}>
                {sc.scenario_id} ({sc.service_area_id} - Window {sc.window_index})
              </option>
            ))}
          </select>
        )}
      </div>

      {selectedScenario && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
            gap: "16px",
            marginBottom: "24px",
          }}
          data-testid="scenario-kpi-line"
        >
          <div style={{ background: "var(--jp-surface)", padding: "16px", borderRadius: "var(--jp-radius)", border: "1px solid var(--jp-border)" }}>
            <div style={{ color: "var(--jp-muted)", fontSize: "0.8rem", textTransform: "uppercase", fontWeight: 600 }}>Total Areas</div>
            <div style={{ fontSize: "1.5rem", fontWeight: 700, color: "var(--jp-text)" }}>{selectedScenario.n_areas}</div>
          </div>
          <div style={{ background: "var(--jp-surface)", padding: "16px", borderRadius: "var(--jp-radius)", border: "1px solid var(--jp-border)" }}>
            <div style={{ color: "var(--jp-muted)", fontSize: "0.8rem", textTransform: "uppercase", fontWeight: 600 }}>Available Supply</div>
            <div style={{ fontSize: "1.5rem", fontWeight: 700, color: "var(--jp-accent)" }}>{selectedScenario.supply_litres.toLocaleString()} L</div>
          </div>
          <div style={{ background: "var(--jp-surface)", padding: "16px", borderRadius: "var(--jp-radius)", border: "1px solid var(--jp-border)" }}>
            <div style={{ color: "var(--jp-muted)", fontSize: "0.8rem", textTransform: "uppercase", fontWeight: 600 }}>Available Tankers</div>
            <div style={{ fontSize: "1.5rem", fontWeight: 700, color: "var(--jp-text)" }}>{selectedScenario.tankers_available}</div>
          </div>
          <div style={{ background: "var(--jp-surface)", padding: "16px", borderRadius: "var(--jp-radius)", border: "1px solid var(--jp-border)" }}>
            <div style={{ color: "var(--jp-muted)", fontSize: "0.8rem", textTransform: "uppercase", fontWeight: 600 }}>Heat Alert Level</div>
            <div style={{ fontSize: "1.5rem", fontWeight: 700, color: selectedScenario.alert_level > 1 ? "var(--jp-warn)" : "var(--jp-text)" }}>
              Level {selectedScenario.alert_level}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export const DispatchPlanScreen: React.FC<DispatchPlanScreenProps> = ({
  plan,
  generating,
  generateError,
  onGeneratePlan,
  actionsSlot,
}) => {
  return (
    <div style={{ padding: "8px", maxWidth: "1200px" }} data-testid="dispatch-plan-screen-mock">
      {/* Renders actionsSlot verbatim above the plan table */}
      <div data-testid="actions-slot-container">{actionsSlot}</div>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
        <div>
          <h2 style={{ margin: "0 0 4px 0" }}>Dispatch Plan (Mock A)</h2>
          <p style={{ color: "var(--jp-muted)", margin: 0, fontSize: "0.9rem" }}>
            Optimization allocations and tanker routing schedule.
          </p>
        </div>
        <button
          type="button"
          onClick={onGeneratePlan}
          disabled={generating}
          style={{
            padding: "8px 16px",
            background: "var(--jp-accent)",
            color: "#ffffff",
            border: "none",
            borderRadius: "var(--jp-radius)",
            fontWeight: 600,
            cursor: generating ? "not-allowed" : "pointer",
            opacity: generating ? 0.6 : 1,
          }}
          data-testid="generate-plan-btn"
        >
          {generating ? "Generating..." : plan ? "Regenerate Plan" : "Generate Plan"}
        </button>
      </div>

      {generateError && (
        <div
          style={{
            background: "var(--jp-danger-weak)",
            color: "var(--jp-danger)",
            border: "1px solid var(--jp-danger)",
            padding: "12px 16px",
            borderRadius: "var(--jp-radius)",
            marginBottom: "16px",
          }}
          data-testid="generate-error"
        >
          {generateError}
        </div>
      )}

      {generating && (
        <div style={{ padding: "32px", textAlign: "center", color: "var(--jp-muted)" }}>
          Computing optimal dispatch plan using fairness-weighted MIP solver...
        </div>
      )}

      {!plan && !generating && (
        <div
          style={{
            background: "var(--jp-surface)",
            border: "1px dashed var(--jp-border)",
            borderRadius: "var(--jp-radius)",
            padding: "48px 24px",
            textAlign: "center",
          }}
          data-testid="no-plan-empty-state"
        >
          <h3 style={{ margin: "0 0 8px 0" }}>No Dispatch Plan Generated</h3>
          <p style={{ color: "var(--jp-muted)", margin: "0 0 16px 0" }}>
            Click &ldquo;Generate Plan&rdquo; to run the optimization engine for this scenario.
          </p>
        </div>
      )}

      {plan && !generating && (
        <div style={{ background: "var(--jp-surface)", borderRadius: "var(--jp-radius)", border: "1px solid var(--jp-border)", overflow: "hidden" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: "0.9rem" }} data-testid="allocations-table">
            <thead>
              <tr style={{ background: "var(--jp-bg)", borderBottom: "1px solid var(--jp-border)" }}>
                <th style={{ padding: "12px 16px" }}>Area ID</th>
                <th style={{ padding: "12px 16px" }}>Priority</th>
                <th style={{ padding: "12px 16px" }}>Shortfall</th>
                <th style={{ padding: "12px 16px" }}>Allocated</th>
                <th style={{ padding: "12px 16px" }}>Unmet</th>
                <th style={{ padding: "12px 16px" }}>Coverage</th>
                <th style={{ padding: "12px 16px" }}>Tankers</th>
              </tr>
            </thead>
            <tbody>
              {plan.result.allocations.map((alloc) => {
                const isUnserved = alloc.allocated_litres === 0 || alloc.is_unserved_remainder;
                return (
                  <tr
                    key={alloc.area_id}
                    style={{
                      borderBottom: "1px solid var(--jp-border)",
                      backgroundColor: isUnserved ? "var(--jp-danger-weak)" : "transparent",
                      color: isUnserved ? "var(--jp-danger)" : "inherit",
                    }}
                    data-testid={`alloc-row-${alloc.area_id}`}
                  >
                    <td style={{ padding: "12px 16px", fontWeight: 600 }}>{alloc.area_id}</td>
                    <td style={{ padding: "12px 16px" }}>{alloc.priority_weight.toFixed(4)}</td>
                    <td style={{ padding: "12px 16px" }}>{alloc.predicted_shortfall_litres.toLocaleString()} L</td>
                    <td style={{ padding: "12px 16px", fontWeight: 600 }}>{alloc.allocated_litres.toLocaleString()} L</td>
                    <td style={{ padding: "12px 16px" }}>{alloc.unmet_litres.toLocaleString()} L</td>
                    <td style={{ padding: "12px 16px" }}>{(alloc.coverage_ratio * 100).toFixed(1)}%</td>
                    <td style={{ padding: "12px 16px" }}>
                      {alloc.deliveries.length > 0
                        ? alloc.deliveries.map((d) => d.tanker_id).join(", ")
                        : "None"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};
