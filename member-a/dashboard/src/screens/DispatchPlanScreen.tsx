import { useCallback, useMemo } from "react";
import type { DispatchPlanScreenProps, PlanRecord } from "../contract/types";
import { ApiClient } from "./api";
import { PlanHeader } from "./components/PlanHeader";
import { PlanMap } from "./components/PlanMap";
import { PlanTable } from "./components/PlanTable";
import shared from "./components/shared.module.css";
import { TankerPanel } from "./components/TankerPanel";
import { Spinner } from "./components/ui";
import { UnservedPanel } from "./components/UnservedPanel";
import styles from "./DispatchPlanScreen.module.css";
import { useResource } from "./useResource";

interface EmptyPlanProps {
  scenarioId: string | null;
  generating: boolean;
  generateError: string | null;
  onGeneratePlan: () => void;
  onOpenSnapshot: () => void;
}

function EmptyPlan({ scenarioId, generating, generateError, onGeneratePlan, onOpenSnapshot }: EmptyPlanProps) {
  if (scenarioId === null) {
    return (
      <section className={`${shared.card} ${shared.emptyState}`} data-state="no-scenario">
        <h2 className={shared.emptyTitle}>No scenario selected</h2>
        <p className={shared.emptyText}>Choose a scenario on the Crisis snapshot first, then come back to generate a dispatch plan.</p>
        <button type="button" className={`${shared.button} ${shared.buttonPrimary}`} onClick={onOpenSnapshot}>
          Go to Crisis snapshot
        </button>
      </section>
    );
  }
  return (
    <section className={`${shared.card} ${shared.emptyState}`} data-state="no-plan">
      <h2 className={shared.emptyTitle}>No dispatch plan yet</h2>
      <p className={shared.emptyText}>
        Generate a plan for <span className={shared.mono}>{scenarioId}</span> to see the ranked allocation, tanker routes and any shortage left unserved.
      </p>
      {generateError ? (
        <div className={`${shared.notice} ${shared.noticeDanger}`} role="alert" data-error-kind="generate-failed">
          {generateError}
        </div>
      ) : null}
      {generating ? <Spinner label="Generating plan…" /> : null}
      <button
        type="button"
        className={`${shared.button} ${shared.buttonPrimary}`}
        disabled={generating}
        onClick={onGeneratePlan}
      >
        Generate plan
      </button>
    </section>
  );
}

type PlanViewProps = Pick<DispatchPlanScreenProps, "generating" | "generateError" | "actionsSlot" | "onTrack"> & {
  plan: PlanRecord;
  client: ApiClient;
};

function PlanView({ plan, client, generating, generateError, actionsSlot, onTrack }: PlanViewProps) {
  const { result } = plan;

  const scenario = useResource(plan.scenario_id, (signal) => client.getScenario(plan.scenario_id, signal), [client]);

  const names = useMemo(() => {
    const map = new Map<string, string>();
    if (scenario.status === "ready") {
      map.set(scenario.data.depot.depot_id, scenario.data.depot.name);
      for (const area of scenario.data.areas) map.set(area.area_id, area.name);
    }
    return map;
  }, [scenario]);
  const nameOf = useCallback((id: string) => names.get(id) ?? id, [names]);

  const explain = useCallback(
    (areaId: string) =>
      onTrack({
        name: "explanation_viewed",
        scenario_id: plan.scenario_id,
        props: { plan_id: plan.plan_id, area_id: areaId },
      }),
    [onTrack, plan.scenario_id, plan.plan_id],
  );

  return (
    <>
      {generating ? (
        <div className={shared.card}>
          <Spinner label="Generating plan…" />
        </div>
      ) : null}
      {generateError ? (
        <div className={`${shared.notice} ${shared.noticeDanger}`} role="alert" data-error-kind="generate-failed">
          {generateError}
        </div>
      ) : null}

      <PlanHeader plan={plan} />

      <div className={styles.grid}>
        <PlanMap result={result} scenario={scenario} />
        <div className={styles.side}>
          <UnservedPanel result={result} nameOf={nameOf} />
          <TankerPanel routes={result.routes} nameOf={nameOf} />
        </div>
      </div>

      <div className={styles.actions} data-testid="actions-slot">
        {actionsSlot}
      </div>
      <PlanTable plan={plan} nameOf={nameOf} onExplain={explain} />
    </>
  );
}

/** Screen 2: the dispatch plan, rendered from the PlanRecord the shell passes in. */
export function DispatchPlanScreen(props: DispatchPlanScreenProps) {
  const { aApiBase, token, scenarioId, plan, generating, generateError, actionsSlot, onGeneratePlan, onTrack, onOpenTab } = props;
  const client = useMemo(() => new ApiClient(aApiBase, token), [aApiBase, token]);

  return (
    <div className={shared.root} data-screen="plan">
      {plan ? (
        <PlanView
          plan={plan}
          client={client}
          generating={generating}
          generateError={generateError}
          actionsSlot={actionsSlot}
          onTrack={onTrack}
        />
      ) : (
        <>
          <h1 className={shared.screenTitle}>Dispatch plan</h1>
          <EmptyPlan
            scenarioId={scenarioId}
            generating={generating}
            generateError={generateError}
            onGeneratePlan={onGeneratePlan}
            onOpenSnapshot={() => onOpenTab("snapshot")}
          />
        </>
      )}
    </div>
  );
}
