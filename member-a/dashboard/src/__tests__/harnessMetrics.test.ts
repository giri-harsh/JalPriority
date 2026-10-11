import { describe, expect, it } from "vitest";
import {
  computeMetrics,
  countViolations,
  debtByAreaFromScenario,
  debtFromHistory,
  DEBT_DECAY,
  nextDebt,
  roundHalfEven,
} from "../harness/metrics";
import { clone, metricsFixture, planRecordFixture, scenarioFixture } from "../test/support";

const SEED_DEBT = { ar_01: 0, ar_02: 8000, ar_03: 12000 };

describe("debt from history (F-10, lambda 0.3)", () => {
  it("uses the contract decay", () => {
    expect(DEBT_DECAY).toBe(0.3);
  });

  it("reproduces the ledger seeding check: {0, 8000, 12000} after window 5", () => {
    expect(debtByAreaFromScenario(scenarioFixture)).toEqual(SEED_DEBT);
  });

  it("follows ar_03 through windows 4 and 5 (6000, then 0.7 * 6000 + 7800)", () => {
    const history = scenarioFixture.areas[2]!.history;
    expect(debtFromHistory(history.slice(0, 1))).toBe(6000);
    expect(debtFromHistory(history)).toBe(12000);
  });

  it("replays history in window order, whatever the input order", () => {
    const history = scenarioFixture.areas[2]!.history;
    expect(debtFromHistory([...history].reverse())).toBe(12000);
  });

  it("starts from zero and treats over-delivery as no unmet need", () => {
    expect(debtFromHistory([])).toBe(0);
    expect(debtFromHistory([{ window_index: 1, need_litres: 100, received_litres: 900, max_temp_c: 40, alert_level: 1 }])).toBe(0);
  });

  it("decays old debt: round(0.7 * D + unmet)", () => {
    expect(nextDebt(12000, 0)).toBe(8400);
    expect(nextDebt(0, 9000)).toBe(9000);
    expect(nextDebt(8000, 0)).toBe(5600);
  });

  it("rounds half to even like Python's round()", () => {
    expect(roundHalfEven(2.5)).toBe(2);
    expect(roundHalfEven(3.5)).toBe(4);
    expect(roundHalfEven(0.5)).toBe(0);
    expect(roundHalfEven(1.4999)).toBe(1);
    expect(roundHalfEven(1.5001)).toBe(2);
  });
});

describe("computeMetrics on the contract worked example", () => {
  const metrics = computeMetrics(planRecordFixture.result, SEED_DEBT);

  it("gives priority-weighted unmet 12683.7 (F-9)", () => {
    expect(metrics.priority_weighted_unmet).toBe(12683.7);
  });

  it("gives debt remaining 23000 (F-11)", () => {
    expect(metrics.deprivation_debt_remaining_litres).toBe(23000);
  });

  it("gives total unmet 9000 (F-12), min coverage 0.3571 (F-13), travel 37.2 km (F-14), 0 violations (F-15)", () => {
    expect(metrics.total_unmet_litres).toBe(9000);
    expect(metrics.min_coverage_ratio).toBe(0.3571);
    expect(metrics.total_travel_km).toBe(37.2);
    expect(metrics.constraint_violations).toBe(0);
    expect(metrics.replanning_time_ms).toBeNull();
  });

  it("equals the CONTRACT 5.7 metrics example exactly", () => {
    expect(metrics).toEqual(metricsFixture);
    expect(metrics).toEqual(planRecordFixture.metrics);
  });

  it("treats areas missing from debt_by_area as zero debt", () => {
    expect(computeMetrics(planRecordFixture.result, {}).deprivation_debt_remaining_litres).toBe(9000);
  });
});

describe("computeMetrics edge cases", () => {
  it("reports min coverage 1.0 when no area has a shortfall", () => {
    const result = clone(planRecordFixture.result);
    for (const allocation of result.allocations) {
      allocation.predicted_shortfall_litres = 0;
      allocation.allocated_litres = 0;
      allocation.unmet_litres = 0;
      allocation.coverage_ratio = 1;
    }
    const metrics = computeMetrics(result, SEED_DEBT);
    expect(metrics.min_coverage_ratio).toBe(1.0);
    expect(metrics.priority_weighted_unmet).toBe(0);
    expect(metrics.total_unmet_litres).toBe(0);
  });

  it("counts each kind of constraint violation (F-15)", () => {
    const base = clone(planRecordFixture.result);
    expect(countViolations(base)).toBe(0);

    const overSupply = clone(base);
    overSupply.totals.supply_available_litres = 19000;
    expect(countViolations(overSupply)).toBe(1);

    const overCapacity = clone(base);
    overCapacity.routes[0]!.load_litres = 13000;
    expect(countViolations(overCapacity)).toBe(1);

    const overRoute = clone(base);
    overRoute.routes[1]!.route_km = 60.02;
    expect(countViolations(overRoute)).toBe(1);

    const withinTolerance = clone(base);
    withinTolerance.routes[1]!.route_km = 60.01;
    expect(countViolations(withinTolerance)).toBe(0);

    const unavailable = clone(base);
    unavailable.allocations[0]!.deliveries.push({ tanker_id: "tk_03", stop_order: 1, litres: 100 });
    unavailable.allocations[1]!.deliveries.push({ tanker_id: "tk_03", stop_order: 2, litres: 100 });
    expect(countViolations(unavailable)).toBe(2);
  });
});
