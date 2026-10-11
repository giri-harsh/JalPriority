import { useEffect, useMemo, useRef } from "react";
import type { SnapshotScreenProps } from "../contract/types";
import { ApiClient } from "./api";
import type { PredictionSet, Scenario, ScenarioSummary } from "./apiTypes";
import { AreaTable } from "./components/AreaTable";
import { CoverageBar, KpiCard } from "./components/KpiCard";
import { ModelPanel } from "./components/ModelPanel";
import shared from "./components/shared.module.css";
import { Badge, ErrorNotice, Spinner } from "./components/ui";
import { alertTone, coverablePercent, formatLitres, formatNumber, formatUtc } from "./format";
import styles from "./SnapshotScreen.module.css";
import { useResource, type Resource } from "./useResource";

interface ScenarioPickerProps {
  scenarioId: string | null;
  summaries: Resource<ScenarioSummary[]>;
  onChange: (scenarioId: string) => void;
}

function ScenarioPicker({ scenarioId, summaries, onChange }: ScenarioPickerProps) {
  const options = summaries.status === "ready" ? summaries.data : [];
  const known = options.some((option) => option.scenario_id === scenarioId);
  return (
    <label className={styles.picker}>
      <span className={styles.pickerLabel}>Scenario</span>
      <select
        className={styles.select}
        value={scenarioId ?? ""}
        disabled={summaries.status !== "ready"}
        onChange={(event) => {
          if (event.target.value) onChange(event.target.value);
        }}
      >
        <option value="" disabled>
          {summaries.status === "loading" ? "Loading scenarios…" : "Select a scenario…"}
        </option>
        {scenarioId && !known ? <option value={scenarioId}>{scenarioId}</option> : null}
        {options.map((option) => (
          <option key={option.scenario_id} value={option.scenario_id}>
            {option.label} ({option.scenario_id})
          </option>
        ))}
      </select>
    </label>
  );
}

interface SnapshotBodyProps {
  scenario: Scenario;
  predictions: Resource<PredictionSet>;
}

function SnapshotBody({ scenario, predictions }: SnapshotBodyProps) {
  const supply = scenario.supply.tanker_water_available_litres;
  const available = scenario.tankers.filter((tanker) => tanker.status === "available").length;
  const level = scenario.heat.alert_level;
  const shortfall =
    predictions.status === "ready"
      ? predictions.data.areas.reduce((total, area) => total + area.predicted_shortfall_litres, 0)
      : null;
  const percent = shortfall === null ? null : coverablePercent(supply, shortfall);
  const run = predictions.status === "ready" ? predictions.data : null;

  return (
    <>
      <div className={styles.kpis}>
        <KpiCard id="supply" label="Tanker water available" value={formatLitres(supply)} hint={scenario.service_area_name} />
        <KpiCard
          id="tankers"
          label="Tankers available"
          value={`${available} / ${scenario.tankers.length}`}
          hint="available / total"
        />
        <KpiCard
          id="heat"
          label="Heat alert"
          value={
            <Badge tone={alertTone(level)} data-alert-level={level} data-testid="alert-badge">
              Level {level}
            </Badge>
          }
          hint={`Max ${scenario.heat.max_temp_c.toFixed(1)} °C · heat index ${scenario.heat.heat_index_c.toFixed(1)} °C`}
        />
        <KpiCard
          id="shortfall"
          label="Predicted shortfall"
          value={shortfall === null ? "…" : formatLitres(shortfall)}
          hint={
            percent === null
              ? "Waiting for predictions"
              : `${Math.round(percent)}% of shortfall coverable with ${formatLitres(supply)}`
          }
        >
          {percent === null ? null : <CoverageBar percent={percent} />}
        </KpiCard>
      </div>

      <section className={shared.card} aria-label="Area demand">
        <h2 className={shared.cardTitle}>Areas ranked by predicted shortfall</h2>
        <p className={shared.muted}>
          Window {scenario.window_index} from {formatUtc(scenario.window_start)} ({scenario.window_hours} h) ·{" "}
          {formatNumber(scenario.areas.length)} areas
          {run
            ? ` · model ${run.model_version}${run.run_mae_litres === null ? "" : `, run MAE ${formatLitres(run.run_mae_litres)} against the synthetic ground truth`}`
            : ""}
        </p>
        {predictions.status === "error" ? <ErrorNotice error={predictions.error} what="Predictions unavailable" /> : null}
        {predictions.status === "loading" ? <Spinner label="Loading predictions" /> : null}
        <AreaTable scenario={scenario} predictions={run} />
      </section>
    </>
  );
}

/** Screen 1: crisis snapshot. Reads scenario, predictions and model info from A's API. */
export function SnapshotScreen(props: SnapshotScreenProps) {
  const { aApiBase, token, scenarioId, onScenarioChange, onTrack, onOpenTab } = props;
  const client = useMemo(() => new ApiClient(aApiBase, token), [aApiBase, token]);

  const summaries = useResource("scenarios", (signal) => client.listScenarios(signal), [client]);
  const model = useResource("model", (signal) => client.getModel(signal), [client]);
  const scenario = useResource(scenarioId, (signal) => client.getScenario(scenarioId ?? "", signal), [client]);
  const predictions = useResource(scenarioId, (signal) => client.getPredictions(scenarioId ?? "", signal), [client]);

  const onTrackRef = useRef(onTrack);
  onTrackRef.current = onTrack;
  const announced = useRef(new Set<string>());
  useEffect(() => {
    if (scenarioId === null || scenario.status !== "ready" || announced.current.has(scenarioId)) return;
    announced.current.add(scenarioId);
    const loaded = scenario.data;
    onTrackRef.current({
      name: "scenario_loaded",
      scenario_id: scenarioId,
      props: {
        n_areas: loaded.areas.length,
        supply_litres: loaded.supply.tanker_water_available_litres,
        tankers_available: loaded.tankers.filter((tanker) => tanker.status === "available").length,
      },
    });
  }, [scenarioId, scenario]);

  return (
    <div className={shared.root} data-screen="snapshot">
      <header className={styles.header}>
        <h1 className={shared.screenTitle}>Crisis snapshot</h1>
        <div className={styles.headerActions}>
          <ScenarioPicker scenarioId={scenarioId} summaries={summaries} onChange={onScenarioChange} />
          <button
            type="button"
            className={`${shared.button} ${shared.buttonPrimary}`}
            disabled={scenarioId === null}
            onClick={() => onOpenTab("plan")}
          >
            Go to dispatch plan {"→"}
          </button>
        </div>
      </header>

      {summaries.status === "error" ? <ErrorNotice error={summaries.error} what="Scenarios unavailable" /> : null}

      {scenarioId === null ? (
        <section className={`${shared.card} ${shared.emptyState}`} data-state="no-scenario">
          <h2 className={shared.emptyTitle}>No scenario selected</h2>
          <p className={shared.emptyText}>Pick a scenario above to load the crisis snapshot: supply, tankers, heat alert and predicted shortage per area.</p>
        </section>
      ) : null}

      {scenarioId !== null && scenario.status === "loading" ? (
        <div className={shared.card}>
          <Spinner label="Loading scenario" />
        </div>
      ) : null}
      {scenario.status === "error" ? <ErrorNotice error={scenario.error} what="Scenario unavailable" /> : null}
      {scenario.status === "ready" ? <SnapshotBody scenario={scenario.data} predictions={predictions} /> : null}

      <ModelPanel model={model} />
    </div>
  );
}
