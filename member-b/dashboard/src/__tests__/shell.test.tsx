import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ShellProvider, useShell } from "../shell/ShellContext";
import { Header } from "../shell/Header";
import { TabBar } from "../shell/TabBar";
import { ApprovalBar } from "../shell/ApprovalBar";
import { App } from "../App";
import { bClient } from "../api/bClient";
import { PlanRecord } from "../contract/types";

const mockPlan: PlanRecord = {
  schema_version: "1.0",
  plan_id: "pln_test_123",
  status: "recommended",
  created_at: "2026-10-10T09:15:02.600Z",
  created_by: "officer_01",
  scenario_id: "scn_sa01_w06",
  service_area_id: "sa_01",
  window_index: 6,
  supersedes_plan_id: null,
  replan_of_plan_id: null,
  disruption: null,
  is_synthetic: true,
  result: {
    schema_version: "1.0",
    result_id: "res_test_123",
    strategy: "jalpriority",
    scenario_id: "scn_sa01_w06",
    service_area_id: "sa_01",
    window_index: 6,
    generated_at: "2026-10-10T09:15:02.600Z",
    weights: { urgency: 1.0, debt: 1.0, vulnerability: 0.5, travel: 20.0 },
    solver: { engine: "ortools-cp-sat", status: "optimal", solve_time_ms: 120, time_limit_s: 8.0 },
    prediction: { model_version: "xgb-sa-v1", latency_ms: 10, run_mae_litres: 120.0 },
    totals: { supply_available_litres: 20000, tanker_capacity_available_litres: 20000, shortfall_litres: 15000, allocated_litres: 15000, unmet_litres: 0, travel_km: 25.0 },
    allocations: [
      {
        area_id: "ar_01",
        rank: 1,
        predicted_shortfall_litres: 5000,
        allocated_litres: 5000,
        unmet_litres: 0,
        coverage_ratio: 1.0,
        priority_weight: 1.8,
        urgency: 0.8,
        debt_litres_used: 0,
        debt_normalized: 0.0,
        vulnerability_used: 0.0,
        deliveries: [{ tanker_id: "tk_01", stop_order: 1, litres: 5000 }],
        reasons: [],
        reason_text: "Fully served",
        is_unserved_remainder: false,
      },
      {
        area_id: "ar_02",
        rank: 2,
        predicted_shortfall_litres: 6000,
        allocated_litres: 0,
        unmet_litres: 6000,
        coverage_ratio: 0.0,
        priority_weight: 1.2,
        urgency: 0.2,
        debt_litres_used: 0,
        debt_normalized: 0.0,
        vulnerability_used: 0.0,
        deliveries: [],
        reasons: [],
        reason_text: "Unserved",
        is_unserved_remainder: true,
      },
    ],
    routes: [],
    unserved: [],
    constraint_check: { water_cap_ok: true, capacity_ok: true, route_limit_ok: true, unavailable_tanker_ok: true, violations: [] },
  },
  metrics: {
    schema_version: "1.0",
    priority_weighted_unmet: 7200.0,
    deprivation_debt_remaining_litres: 6000,
    total_unmet_litres: 6000,
    min_coverage_ratio: 0.0,
    total_travel_km: 25.0,
    constraint_violations: 0,
    replanning_time_ms: null,
  },
  decision: null,
};

describe("Dashboard Shell and Approval Controls (M11)", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it("1 & 2. Unauthenticated user is redirected to /login", async () => {
    render(
      <MemoryRouter initialEntries={["/snapshot"]}>
        <ShellProvider>
          <App />
        </ShellProvider>
      </MemoryRouter>
    );

    expect(screen.getByTestId("login-screen")).toBeDefined();
    expect(screen.getByTestId("login-form")).toBeDefined();
  });

  it("3. Header displays product name, disclaimer badge, scenario, officer, and role", () => {
    sessionStorage.setItem("jp.b.token", "test-token");
    sessionStorage.setItem("jp.b.officerId", "officer_01");
    sessionStorage.setItem("jp.b.role", "dispatcher");
    sessionStorage.setItem("jp.b.scenarioId", "scn_sa01_w06");

    render(
      <MemoryRouter>
        <ShellProvider>
          <Header />
        </ShellProvider>
      </MemoryRouter>
    );

    expect(screen.getByText("JalPriority AI")).toBeDefined();
    expect(screen.getByText("SYNTHETIC DATA · SIMULATION RESULTS")).toBeDefined();
    expect(screen.getByText("scn_sa01_w06")).toBeDefined();
    expect(screen.getByText("officer_01")).toBeDefined();
    expect(screen.getByText("dispatcher")).toBeDefined();
  });

  it("4. Tab navigation contains all five contract tabs", () => {
    render(
      <MemoryRouter initialEntries={["/snapshot"]}>
        <TabBar />
      </MemoryRouter>
    );

    expect(screen.getByText("Crisis snapshot")).toBeDefined();
    expect(screen.getByText("Dispatch plan")).toBeDefined();
    expect(screen.getByText("Fairness & debt")).toBeDefined();
    expect(screen.getByText("Compare")).toBeDefined();
    expect(screen.getByText("Replan")).toBeDefined();
  });

  it("5. ApprovalBar supports Approve flow with confirmation modal", async () => {
    sessionStorage.setItem("jp.b.token", "test-token");
    const recordDecisionSpy = vi.spyOn(bClient, "recordDecision").mockResolvedValue({
      plan: { ...mockPlan, status: "approved" },
      next_plan: null,
      ledger_updated: true,
    });

    render(
      <MemoryRouter>
        <ShellProvider>
          <ApprovalBar plan={mockPlan} />
        </ShellProvider>
      </MemoryRouter>
    );

    expect(screen.getByTestId("plan-status").textContent).toBe("recommended");
    const approveBtn = screen.getByTestId("approve-btn");
    fireEvent.click(approveBtn);

    // Confirmation modal should open
    expect(screen.getByTestId("approve-modal")).toBeDefined();
    const confirmBtn = screen.getByTestId("confirm-approve-btn");
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(recordDecisionSpy).toHaveBeenCalledWith("test-token", "pln_test_123", "approved", null);
    });
  });

  it("6. ApprovalBar supports Edit Weights popover and submits edited decision", async () => {
    sessionStorage.setItem("jp.b.token", "test-token");
    const recordDecisionSpy = vi.spyOn(bClient, "recordDecision").mockResolvedValue({
      plan: { ...mockPlan, status: "edited" },
      next_plan: { ...mockPlan, plan_id: "pln_next_456", status: "recommended" },
      ledger_updated: false,
    });

    render(
      <MemoryRouter>
        <ShellProvider>
          <ApprovalBar plan={mockPlan} />
        </ShellProvider>
      </MemoryRouter>
    );

    const editBtn = screen.getByTestId("edit-weights-btn");
    fireEvent.click(editBtn);

    expect(screen.getByTestId("edit-weights-popover")).toBeDefined();
    const regenBtn = screen.getByText("Regenerate Plan");
    fireEvent.click(regenBtn);

    await waitFor(() => {
      expect(recordDecisionSpy).toHaveBeenCalledWith(
        "test-token",
        "pln_test_123",
        "edited",
        null,
        expect.objectContaining({ urgency: 1.0, debt: 1.0, vulnerability: 0.5, travel: 20.0 })
      );
    });
  });

  it("7. ApprovalBar Rejection requires non-empty note", async () => {
    sessionStorage.setItem("jp.b.token", "test-token");

    render(
      <MemoryRouter>
        <ShellProvider>
          <ApprovalBar plan={mockPlan} />
        </ShellProvider>
      </MemoryRouter>
    );

    const rejectBtn = screen.getByTestId("reject-btn");
    fireEvent.click(rejectBtn);

    expect(screen.getByTestId("reject-modal")).toBeDefined();
    const confirmRejectBtn = screen.getByTestId("confirm-reject-btn");

    // Click without note
    fireEvent.click(confirmRejectBtn);
    expect(screen.getByText("A rejection note is required.")).toBeDefined();

    // Type note and submit
    const recordDecisionSpy = vi.spyOn(bClient, "recordDecision").mockResolvedValue({
      plan: { ...mockPlan, status: "rejected" },
      next_plan: null,
      ledger_updated: false,
    });

    const noteInput = screen.getByTestId("reject-note-input");
    fireEvent.change(noteInput, { target: { value: "Weather shift invalidates schedule" } });
    fireEvent.click(confirmRejectBtn);

    await waitFor(() => {
      expect(recordDecisionSpy).toHaveBeenCalledWith(
        "test-token",
        "pln_test_123",
        "rejected",
        "Weather shift invalidates schedule"
      );
    });
  });

  it("8. A 401 logs the user out and clears session storage", async () => {
    sessionStorage.setItem("jp.b.token", "expired-token");
    sessionStorage.setItem("jp.b.scenarioId", "scn_sa01_w06");

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      status: 401,
      ok: false,
      json: async () => ({ error: { code: "UNAUTHORIZED", message: "Token expired" } }),
    } as Response);

    const TestComponent: React.FC = () => {
      const { onGeneratePlan, token } = useShell();
      return (
        <div>
          <span data-testid="token-val">{token || "NONE"}</span>
          <button type="button" onClick={onGeneratePlan} data-testid="trigger-btn">
            Trigger
          </button>
        </div>
      );
    };

    render(
      <ShellProvider>
        <TestComponent />
      </ShellProvider>
    );

    expect(screen.getByTestId("token-val").textContent).toBe("expired-token");
    fireEvent.click(screen.getByTestId("trigger-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("token-val").textContent).toBe("NONE");
    });
  });
});
