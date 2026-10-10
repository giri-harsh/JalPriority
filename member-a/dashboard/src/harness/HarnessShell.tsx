import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PlanRecord, ScreenContext, TabId, TrackEvent } from "../contract/types";
import { SESSION_EXPIRED_MESSAGE, ApiClient, describeError, isSessionExpired } from "../screens/api";
import { DispatchPlanScreen, SnapshotScreen } from "../screens";
import styles from "./HarnessShell.module.css";
import { loadFixturePlan, resolvePlanSource, solvePlanRecord, type PlanSourceKind } from "./planSource";

const DEFAULT_SCENARIO_ID = "scn_sa01_w06";
const OFFICER_ID = "officer_01";
const FIXTURE_DELAY_MS = 350;
const MAX_EVENTS = 25;

const TABS: { id: Extract<TabId, "snapshot" | "plan">; label: string }[] = [
  { id: "snapshot", label: "Snapshot" },
  { id: "plan", label: "Plan" },
];

interface LoggedEvent {
  seq: number;
  at: string;
  event: TrackEvent;
}

export interface HarnessShellProps {
  /** Overrides VITE_A_HARNESS_PLAN_SOURCE (used by tests). */
  planSource?: PlanSourceKind;
  /** Overrides VITE_A_DEV_TOKEN (used by tests). */
  token?: string;
  initialScenarioId?: string | null;
}

/** Stand-in for B's shell: header, two tabs, shell-owned state and a log of tracked events. */
export function HarnessShell({
  planSource = resolvePlanSource(import.meta.env.VITE_A_HARNESS_PLAN_SOURCE),
  token = import.meta.env.VITE_A_DEV_TOKEN ?? "",
  initialScenarioId = DEFAULT_SCENARIO_ID,
}: HarnessShellProps) {
  const [tab, setTab] = useState<"snapshot" | "plan">("snapshot");
  const [scenarioId, setScenarioId] = useState<string | null>(initialScenarioId);
  const [plan, setPlan] = useState<PlanRecord | null>(null);
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState<string | null>(null);
  const [events, setEvents] = useState<LoggedEvent[]>([]);
  const sequence = useRef(0);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const client = useMemo(() => new ApiClient("/a/v1", token), [token]);

  const onTrack = useCallback((event: TrackEvent) => {
    console.info("[onTrack]", event);
    sequence.current += 1;
    const entry: LoggedEvent = { seq: sequence.current, at: new Date().toISOString(), event };
    setEvents((current) => [entry, ...current].slice(0, MAX_EVENTS));
  }, []);

  const onScenarioChange = useCallback((next: string) => {
    setScenarioId(next);
    setPlan(null);
    setGenerateError(null);
  }, []);

  const onOpenTab = useCallback((next: TabId) => {
    if (next === "snapshot" || next === "plan") setTab(next);
  }, []);

  const onGeneratePlan = useCallback(async () => {
    if (scenarioId === null) return;
    setGenerating(true);
    setGenerateError(null);
    try {
      let record: PlanRecord;
      if (planSource === "fixture") {
        await new Promise((resolve) => setTimeout(resolve, FIXTURE_DELAY_MS));
        record = loadFixturePlan();
      } else {
        record = await solvePlanRecord(client, scenarioId, OFFICER_ID);
      }
      if (!mounted.current) return;
      setPlan(record);
      if (record.scenario_id !== scenarioId) setScenarioId(record.scenario_id);
    } catch (error) {
      if (mounted.current) setGenerateError(isSessionExpired(error) ? SESSION_EXPIRED_MESSAGE : describeError(error));
    } finally {
      if (mounted.current) setGenerating(false);
    }
  }, [client, planSource, scenarioId]);

  const context: ScreenContext = {
    aApiBase: "/a/v1",
    token,
    officerId: OFFICER_ID,
    role: "dispatcher",
    scenarioId,
    onScenarioChange,
    onTrack,
    onOpenTab,
  };

  return (
    <div className={styles.shell}>
      <header className={styles.header}>
        <div className={styles.brand}>
          <strong>JalPriority AI</strong>
          <span className={styles.muted}>Member A screen harness · plan source: {planSource}</span>
        </div>
        <span className={styles.badge}>SYNTHETIC DATA · SIMULATION RESULTS</span>
        <nav className={styles.tabs} role="tablist" aria-label="Screens">
          {TABS.map((item) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={tab === item.id}
              className={`${styles.tab} ${tab === item.id ? styles.tabActive : ""}`}
              onClick={() => setTab(item.id)}
            >
              {item.label}
            </button>
          ))}
        </nav>
      </header>

      {token === "" ? (
        <p className={styles.warning} role="note">
          VITE_A_DEV_TOKEN is not set: requests to the API will be answered with 401. Mint one with scripts/mint_dev_token.py.
        </p>
      ) : null}

      <main className={styles.slot} data-tab={tab}>
        {tab === "snapshot" ? (
          <SnapshotScreen {...context} />
        ) : (
          <DispatchPlanScreen
            {...context}
            plan={plan}
            generating={generating}
            generateError={generateError}
            onGeneratePlan={() => void onGeneratePlan()}
            actionsSlot={
              <div className={styles.actions} role="group" aria-label="Plan actions (placeholder for B)">
                <button type="button" disabled>
                  Approve (B)
                </button>
                <button type="button" disabled>
                  Edit weights (B)
                </button>
                <button type="button" disabled>
                  Reject (B)
                </button>
              </div>
            }
          />
        )}
      </main>

      <aside className={styles.events} aria-label="Tracked events">
        <h2 className={styles.eventsTitle}>onTrack events</h2>
        {events.length === 0 ? (
          <p className={styles.muted}>No events yet.</p>
        ) : (
          <ol className={styles.eventList}>
            {events.map(({ seq, at, event }) => (
              <li key={seq}>
                <code>
                  {at.slice(11, 23)} {event.name} {event.scenario_id} {JSON.stringify(event.props)}
                </code>
              </li>
            ))}
          </ol>
        )}
      </aside>
    </div>
  );
}
