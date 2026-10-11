import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ShellProvider } from "../shell/ShellContext";
import { FairnessDebtScreen } from "../screens/FairnessDebtScreen";
import { CompareScreen } from "../screens/CompareScreen";
import { ReplanScreen } from "../screens/ReplanScreen";
import { bClient } from "../api/bClient";
import { PlanRecord } from "../contract/types";

// Mock resize observer for Recharts in jsdom
(globalThis as any).ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
};

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
        allocated_litres: 1000,
        unmet_litres: 5000,
        coverage_ratio: 0.1667,
        priority_weight: 2.2,
        urgency: 1.0,
        debt_litres_used: 8000,
        debt_normalized: 0.6667,
        vulnerability_used: 0.5,
        deliveries: [{ tanker_id: "tk_02", stop_order: 1, litres: 1000 }],
        reasons: [],
        reason_text: "Partially served",
        is_unserved_remainder: true,
      },
    ],
    routes: [],
    unserved: [],
    constraint_check: {
      water_cap_ok: true,
      capacity_ok: true,
      route_limit_ok: true,
      unavailable_tanker_ok: true,
      violations: [],
    },
  },
  metrics: {
    schema_version: "1.0",
    priority_weighted_unmet: 11000.0,
    deprivation_debt_remaining_litres: 10600,
    total_unmet_litres: 5000,
    min_coverage_ratio: 0.1667,
    total_travel_km: 25.0,
    constraint_violations: 0,
    replanning_time_ms: null,
  },
  decision: null,
};

describe("Dashboard M12 Screens", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    sessionStorage.clear();
    sessionStorage.setItem("jp.b.token", "test-token");
    sessionStorage.setItem("jp.b.scenarioId", "scn_sa01_w06");
  });

  describe("Screen 3: Fairness & Debt", () => {
    it("renders weight controls, live priority formula, and plain language explanations", async () => {
      vi.spyOn(bClient, "getWeights").mockResolvedValueOnce({
        service_area_id: "sa_01",
        weights: { urgency: 1.0, debt: 1.0, vulnerability: 0.5, travel: 20.0 },
        updated_at: "2026-10-10T09:00:00Z",
        updated_by: "officer_01",
        flags: { fairness_weights: true, vulnerability: true, replan: true },
        defaults: { urgency: 1.0, debt: 1.0, vulnerability: 0.5, travel: 20.0 },
        debt_policy: { decay: 0.3, cap_litres: null },
      });

      vi.spyOn(bClient, "getLedger").mockResolvedValueOnce({
        service_area_id: "sa_01",
        decay: 0.3,
        cap_litres: null,
        windows: [4, 5],
        areas: [
          {
            area_id: "ar_01",
            name: "Ward 01",
            current_debt_litres: 0,
            series: [{ window_index: 5, debt_litres: 0, unmet_litres: 0, source: "history", plan_id: null }],
          },
        ],
        totals: [{ window_index: 5, debt_litres: 0 }],
      });

      render(
        <MemoryRouter>
          <ShellProvider>
            <FairnessDebtScreen />
          </ShellProvider>
        </MemoryRouter>
      );

      expect(screen.getByText(/Fairness & Deprivation Debt/i)).toBeDefined();
      await waitFor(() => {
        expect(screen.getByText(/Urgency Weight/i)).toBeDefined();
      });

      expect(screen.getByText(/Deprivation Debt Weight/i)).toBeDefined();
      expect(screen.getByText(/Vulnerability Weight/i)).toBeDefined();
      expect(screen.getByText(/Travel Distance Cost/i)).toBeDefined();

      // Check live formula
      expect(screen.getByText(/p = 1 \+ 1.0·U \+ 1.0·D̂ \+ 0.5·V/i)).toBeDefined();

      // Check explanation text
      expect(screen.getByText(/Deprivation debt measures cumulative unserved water deficit/i)).toBeDefined();
      expect(screen.getByText(/Save weights/i)).toBeDefined();
      expect(screen.getByText(/Save & regenerate plan/i)).toBeDefined();
    });

    it("saves updated weights when Save weights is clicked", async () => {
      vi.spyOn(bClient, "getWeights").mockResolvedValueOnce({
        service_area_id: "sa_01",
        weights: { urgency: 1.0, debt: 1.0, vulnerability: 0.5, travel: 20.0 },
        updated_at: "2026-10-10T09:00:00Z",
        updated_by: "officer_01",
        flags: { fairness_weights: true, vulnerability: true, replan: true },
        defaults: { urgency: 1.0, debt: 1.0, vulnerability: 0.5, travel: 20.0 },
        debt_policy: { decay: 0.3, cap_litres: null },
      });

      const updateSpy = vi.spyOn(bClient, "updateWeights").mockResolvedValueOnce({
        service_area_id: "sa_01",
        weights: { urgency: 2.0, debt: 1.0, vulnerability: 0.5, travel: 20.0 },
        updated_at: "2026-10-10T09:00:00Z",
        updated_by: "officer_01",
        flags: { fairness_weights: true, vulnerability: true, replan: true },
        defaults: { urgency: 1.0, debt: 1.0, vulnerability: 0.5, travel: 20.0 },
        debt_policy: { decay: 0.3, cap_litres: null },
      });

      render(
        <MemoryRouter>
          <ShellProvider>
            <FairnessDebtScreen />
          </ShellProvider>
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText(/Save weights/i)).toBeDefined();
      });

      const urgencySlider = screen.getByLabelText("Urgency weight slider");
      fireEvent.change(urgencySlider, { target: { value: "2.0" } });

      const saveBtn = screen.getByText("Save weights");
      fireEvent.click(saveBtn);

      await waitFor(() => {
        expect(updateSpy).toHaveBeenCalledWith(
          "test-token",
          "sa_01",
          expect.objectContaining({ urgency: 2.0 })
        );
      });
    });
  });

  describe("Screen 4: Compare", () => {
    it("runs comparison and displays 7-metric table and starvation callout", async () => {
      const runCompSpy = vi.spyOn(bClient, "runComparison").mockResolvedValueOnce({
        schema_version: "1.0",
        scenario_id: "scn_sa01_w06",
        weights: { urgency: 1.0, debt: 1.0, vulnerability: 0.5, travel: 20.0 },
        generated_at: "2026-10-10T09:15:00Z",
        is_simulation: true,
        plan_id: "pln_test_123",
        results: {
          jalpriority: mockPlan.result,
          fcfs: {
            ...mockPlan.result,
            strategy: "fcfs",
            allocations: [
              {
                ...mockPlan.result.allocations[0],
                area_id: "ar_01",
                coverage_ratio: 1.0,
              },
              {
                ...mockPlan.result.allocations[1],
                area_id: "ar_02",
                coverage_ratio: 0.0, // Starved!
              },
            ],
          },
          nearest_first: {
            ...mockPlan.result,
            strategy: "nearest_first",
            allocations: [
              {
                ...mockPlan.result.allocations[0],
                area_id: "ar_01",
                coverage_ratio: 1.0,
              },
              {
                ...mockPlan.result.allocations[1],
                area_id: "ar_02",
                coverage_ratio: 0.0, // Starved!
              },
            ],
          },
        },
        metrics: {
          jalpriority: mockPlan.metrics,
          fcfs: {
            schema_version: "1.0",
            priority_weighted_unmet: 36043.8,
            deprivation_debt_remaining_litres: 20600,
            total_unmet_litres: 15000,
            min_coverage_ratio: 0.0,
            total_travel_km: 22.0,
            constraint_violations: 0,
            replanning_time_ms: null,
          },
          nearest_first: {
            schema_version: "1.0",
            priority_weighted_unmet: 36043.8,
            deprivation_debt_remaining_litres: 20600,
            total_unmet_litres: 15000,
            min_coverage_ratio: 0.0,
            total_travel_km: 22.0,
            constraint_violations: 0,
            replanning_time_ms: null,
          },
        },
        best_by_metric: {
          priority_weighted_unmet: "jalpriority",
          deprivation_debt_remaining_litres: "jalpriority",
          total_unmet_litres: "jalpriority",
          min_coverage_ratio: "jalpriority",
          total_travel_km: "nearest_first",
          constraint_violations: "jalpriority",
        },
      });

      render(
        <MemoryRouter>
          <ShellProvider>
            <CompareScreen />
          </ShellProvider>
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(runCompSpy).toHaveBeenCalled();
      });

      // Check metrics scorecard rows
      expect(screen.getByText("Priority-Weighted Unmet (PWU)")).toBeDefined();
      expect(screen.getByText("Deprivation Debt Remaining")).toBeDefined();
      expect(screen.getByText("Minimum Ward Coverage")).toBeDefined();
      expect(screen.getByText("Constraint Violations")).toBeDefined();

      // Check starvation callout for FCFS and Nearest-First
      expect(screen.getByText(/Critical Equity Impact: Areas left with almost nothing/i)).toBeDefined();
      expect(screen.getAllByText(/1 areas starved/i).length).toBe(2);

      // Check footer disclaimer
      expect(screen.getByText(/Synthetic scenario, simulation results, same resources and same yardstick for all strategies/i)).toBeDefined();
    });
  });

  describe("Screen 5: Replan", () => {
    it("renders empty state when no plan is present in shell", () => {
      render(
        <MemoryRouter>
          <ShellProvider>
            <ReplanScreen />
          </ShellProvider>
        </MemoryRouter>
      );

      expect(screen.getByText(/No Active Dispatch Plan/i)).toBeDefined();
      expect(screen.getByText(/Go to Dispatch Plan tab/i)).toBeDefined();
    });

    it("applies disruption and displays replanning latency and diff table when plan exists", async () => {
      vi.spyOn(bClient, "getLatestPlan").mockResolvedValueOnce(mockPlan);
      vi.spyOn(bClient, "getScenario").mockResolvedValueOnce({
        scenario_id: "scn_sa01_w06",
        service_area_id: "sa_01",
        service_area_name: "Service Area 01",
        window_index: 6,
        window_start: "2026-10-10T09:00:00Z",
        window_hours: 12,
        is_synthetic: true,
        seed: 42,
        supply_litres: 20000,
        heat: { alert_level: 2, max_temp_c: 40.0, heat_index_c: 45.0 },
        tankers: [
          { tanker_id: "tk_01", capacity_litres: 10000, max_route_km: 50, is_available: true },
          { tanker_id: "tk_02", capacity_litres: 10000, max_route_km: 50, is_available: true },
        ],
        areas: [],
      });

      const replanSpy = vi.spyOn(bClient, "replan").mockResolvedValueOnce({
        schema_version: "1.0",
        previous_plan_id: "pln_test_123",
        derived_scenario_id: "scn_sa01_w06__d1_3f9a2c",
        disruption: { type: "tanker_down", params: { tanker_id: "tk_01" } },
        replanning_time_ms: 1830,
        new_plan: {
          ...mockPlan,
          plan_id: "pln_revised_456",
          replan_of_plan_id: "pln_test_123",
          status: "recommended",
        },
        diff: {
          areas: [
            {
              area_id: "ar_01",
              litres_before: 5000,
              litres_after: 0,
              delta: -5000,
              tankers_before: ["tk_01"],
              tankers_after: [],
              order_before: [1],
              order_after: [],
              change: "dropped",
            },
          ],
          summary: {
            areas_changed: 1,
            litres_moved: 5000,
            metrics_before: mockPlan.metrics,
            metrics_after: {
              ...mockPlan.metrics,
              total_unmet_litres: 10000,
            },
          },
        },
      });

      render(
        <MemoryRouter>
          <ShellProvider>
            <ReplanScreen />
          </ShellProvider>
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText("pln_test_123")).toBeDefined();
      });

      const applyBtn = screen.getByText("Apply & replan");
      fireEvent.click(applyBtn);

      await waitFor(() => {
        expect(replanSpy).toHaveBeenCalledWith(
          "test-token",
          "pln_test_123",
          expect.objectContaining({
            type: "tanker_down",
          })
        );
      });

      // Latency readout < 5s target
      expect(screen.getByText("1,830 ms")).toBeDefined();
      expect(screen.getByText(/Replanning Latency \(< 5s Target Met\)/i)).toBeDefined();

      // Diff table
      expect(screen.getByText("Ward Allocation Diff Breakdown")).toBeDefined();
      expect(screen.getByText("Dropped")).toBeDefined();

      // Action button
      expect(screen.getByText("Open revised plan in Dispatch tab")).toBeDefined();
    });
  });
});
