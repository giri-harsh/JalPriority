import React, { createContext, useContext, useState, useEffect, useCallback, ReactNode } from "react";
import { PlanRecord, Weights, TrackEvent } from "../contract/types";
import { bClient, APIClientError } from "../api/bClient";

export interface ShellContextType {
  token: string | null;
  officerId: string | null;
  role: "dispatcher" | "admin" | null;
  scenarioId: string | null;
  weights: Weights | null;
  plan: PlanRecord | null;
  generating: boolean;
  generateError: string | null;
  login: (token: string, officerId: string, role: "dispatcher" | "admin") => void;
  logout: () => void;
  setScenarioId: (scenarioId: string) => Promise<void>;
  setWeights: (weights: Weights | null) => void;
  onGeneratePlan: () => Promise<void>;
  onTrack: (event: TrackEvent) => Promise<void>;
  updatePlan: (plan: PlanRecord | null) => void;
}

const ShellContext = createContext<ShellContextType | undefined>(undefined);

export const ShellProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [token, setToken] = useState<string | null>(() => {
    return sessionStorage.getItem("jp.b.token");
  });
  const [officerId, setOfficerId] = useState<string | null>(() => {
    return sessionStorage.getItem("jp.b.officerId");
  });
  const [role, setRole] = useState<"dispatcher" | "admin" | null>(() => {
    return (sessionStorage.getItem("jp.b.role") as "dispatcher" | "admin") || null;
  });
  const [scenarioId, setScenarioIdState] = useState<string | null>(() => {
    return sessionStorage.getItem("jp.b.scenarioId");
  });
  const [weights, setWeights] = useState<Weights | null>(null);
  const [plan, setPlan] = useState<PlanRecord | null>(null);
  const [generating, setGenerating] = useState<boolean>(false);
  const [generateError, setGenerateError] = useState<string | null>(null);

  const logout = useCallback(() => {
    setToken(null);
    setOfficerId(null);
    setRole(null);
    setPlan(null);
    setScenarioIdState(null);
    sessionStorage.removeItem("jp.b.token");
    sessionStorage.removeItem("jp.b.officerId");
    sessionStorage.removeItem("jp.b.role");
    sessionStorage.removeItem("jp.b.scenarioId");
  }, []);

  const login = useCallback((newToken: string, newOfficerId: string, newRole: "dispatcher" | "admin") => {
    setToken(newToken);
    setOfficerId(newOfficerId);
    setRole(newRole);
    sessionStorage.setItem("jp.b.token", newToken);
    sessionStorage.setItem("jp.b.officerId", newOfficerId);
    sessionStorage.setItem("jp.b.role", newRole);
  }, []);

  // Hook 401 handler into bClient
  useEffect(() => {
    bClient.setUnauthorizedHandler(() => {
      logout();
    });
    return () => {
      bClient.setUnauthorizedHandler(null);
    };
  }, [logout]);

  const loadPlanForScenario = useCallback(async (scId: string, currentToken: string | null) => {
    if (!currentToken) return;
    try {
      const latestPlan = await bClient.getLatestPlan(currentToken, scId);
      setPlan(latestPlan);
    } catch {
      setPlan(null);
    }
  }, []);

  const setScenarioId = useCallback(
    async (newScenarioId: string) => {
      setScenarioIdState(newScenarioId);
      sessionStorage.setItem("jp.b.scenarioId", newScenarioId);
      setPlan(null); // prevent stale plan data
      await loadPlanForScenario(newScenarioId, token);
    },
    [token, loadPlanForScenario]
  );

  // Restore plan for initial scenario if logged in
  useEffect(() => {
    if (token && scenarioId && !plan) {
      loadPlanForScenario(scenarioId, token);
    }
  }, [token, scenarioId, plan, loadPlanForScenario]);

  const onGeneratePlan = useCallback(async () => {
    if (!token || !scenarioId) {
      setGenerateError("Please select a scenario first.");
      return;
    }
    setGenerating(true);
    setGenerateError(null);
    try {
      const newPlan = await bClient.generatePlan(token, scenarioId, weights);
      setPlan(newPlan);
    } catch (err) {
      if (err instanceof APIClientError) {
        setGenerateError(err.message);
      } else {
        setGenerateError("Failed to generate plan");
      }
    } finally {
      setGenerating(false);
    }
  }, [token, scenarioId, weights]);

  const onTrack = useCallback(
    async (event: TrackEvent) => {
      if (!token) return;
      try {
        await bClient.trackEvent(token, event);
      } catch (err) {
        // Event logging shouldn't crash the UI unless 401
        if (err instanceof APIClientError && err.status === 401) {
          logout();
        }
      }
    },
    [token, logout]
  );

  const updatePlan = useCallback((newPlan: PlanRecord | null) => {
    setPlan(newPlan);
  }, []);

  return (
    <ShellContext.Provider
      value={{
        token,
        officerId,
        role,
        scenarioId,
        weights,
        plan,
        generating,
        generateError,
        login,
        logout,
        setScenarioId,
        setWeights,
        onGeneratePlan,
        onTrack,
        updatePlan,
      }}
    >
      {children}
    </ShellContext.Provider>
  );
};

export const useShell = (): ShellContextType => {
  const context = useContext(ShellContext);
  if (!context) {
    throw new Error("useShell must be used within a ShellProvider");
  }
  return context;
};
