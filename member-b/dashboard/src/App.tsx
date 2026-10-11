import React from "react";
import {
  Routes,
  Route,
  Navigate,
  useNavigate,
} from "react-router-dom";
import { useShell } from "./shell/ShellContext";
import { Header } from "./shell/Header";
import { TabBar } from "./shell/TabBar";
import { ScreenSlot } from "./shell/ScreenSlot";
import { ApprovalBar } from "./shell/ApprovalBar";
import { LoginScreen } from "./screens/LoginScreen";
import { FairnessDebtScreen } from "./screens/FairnessDebtScreen";
import { CompareScreen } from "./screens/CompareScreen";
import { ReplanScreen } from "./screens/ReplanScreen";

// Import Member A screens via alias @jp/a-screens per CONTRACT §9.2
import { SnapshotScreen, DispatchPlanScreen } from "@jp/a-screens";
import { TabId } from "./contract/types";
import styles from "./styles/shell.module.css";

// The ONLY permitted "/a/v1" literal outside alias configuration per CONTRACT §9.4 and isolation lint
const A_API_BASE = "/a/v1";

const ProtectedLayout: React.FC = () => {
  const {
    token,
    officerId,
    role,
    scenarioId,
    setScenarioId,
    onTrack,
    plan,
    generating,
    generateError,
    onGeneratePlan,
  } = useShell();
  const navigate = useNavigate();

  if (!token) {
    return <Navigate to="/login" replace />;
  }

  const handleOpenTab = (tab: TabId) => {
    navigate(`/${tab}`);
  };

  const screenContext = {
    aApiBase: A_API_BASE,
    token: token,
    officerId: officerId || "officer_01",
    role: role || ("dispatcher" as const),
    scenarioId: scenarioId,
    onScenarioChange: setScenarioId,
    onTrack: onTrack,
    onOpenTab: handleOpenTab,
  };

  return (
    <div className={styles.jpbShell} data-testid="dashboard-shell">
      <Header />
      <TabBar />

      <Routes>
        <Route path="/" element={<Navigate to="/snapshot" replace />} />
        <Route
          path="/snapshot"
          element={
            <ScreenSlot tabId="snapshot">
              <SnapshotScreen {...screenContext} />
            </ScreenSlot>
          }
        />
        <Route
          path="/plan"
          element={
            <ScreenSlot tabId="plan">
              <DispatchPlanScreen
                {...screenContext}
                plan={plan}
                generating={generating}
                generateError={generateError}
                onGeneratePlan={onGeneratePlan}
                actionsSlot={<ApprovalBar plan={plan} />}
              />
            </ScreenSlot>
          }
        />
        <Route
          path="/fairness"
          element={
            <ScreenSlot tabId="fairness">
              <FairnessDebtScreen />
            </ScreenSlot>
          }
        />
        <Route
          path="/compare"
          element={
            <ScreenSlot tabId="compare">
              <CompareScreen />
            </ScreenSlot>
          }
        />
        <Route
          path="/replan"
          element={
            <ScreenSlot tabId="replan">
              <ReplanScreen />
            </ScreenSlot>
          }
        />
        <Route path="*" element={<Navigate to="/snapshot" replace />} />
      </Routes>
    </div>
  );
};

export const App: React.FC = () => {
  const { token } = useShell();

  return (
    <Routes>
      <Route
        path="/login"
        element={token ? <Navigate to="/snapshot" replace /> : <LoginScreen />}
      />
      <Route path="/*" element={<ProtectedLayout />} />
    </Routes>
  );
};
