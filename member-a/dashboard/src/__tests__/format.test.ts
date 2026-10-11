import { describe, expect, it } from "vitest";
import {
  alertTone,
  coverablePercent,
  featureLabel,
  formatDuration,
  formatKm,
  formatLitres,
  formatPercent,
  formatPriority,
  formatSignedLitres,
  formatSpanBefore,
  formatUtc,
  reasonChipText,
  reasonTone,
  strategyLabel,
  unservedReasonText,
} from "../screens/format";

describe("number formatting", () => {
  it("groups litres and appends the unit", () => {
    expect(formatLitres(12000)).toBe("12,000 L");
    expect(formatLitres(0)).toBe("0 L");
  });

  it("signs contributions", () => {
    expect(formatSignedLitres(2300)).toBe("+2,300 L");
    expect(formatSignedLitres(-950.4)).toBe("-950 L");
    expect(formatSignedLitres(0)).toBe("0 L");
  });

  it("formats percentages, priorities, distances and durations", () => {
    expect(formatPercent(0.3571)).toBe("36%");
    expect(formatPercent(1)).toBe("100%");
    expect(formatPriority(2.6857)).toBe("2.69");
    expect(formatKm(24.4)).toBe("24.4 km");
    expect(formatKm(60)).toBe("60.0 km");
    expect(formatKm(12.85)).toBe("12.85 km");
    expect(formatDuration(412)).toBe("412 ms");
    expect(formatDuration(8400)).toBe("8.4 s");
  });

  it("computes the coverable share of the shortfall", () => {
    expect(coverablePercent(20000, 29000)).toBeCloseTo(68.97, 2);
    expect(coverablePercent(50000, 29000)).toBe(100);
    expect(coverablePercent(100, 0)).toBe(100);
  });
});

describe("time formatting", () => {
  it("renders UTC timestamps without milliseconds", () => {
    expect(formatUtc("2026-10-10T09:15:02.481Z")).toBe("2026-10-10 09:15 UTC");
    expect(formatUtc("not a date")).toBe("not a date");
  });

  it("describes how long before a reference instant something happened", () => {
    const reference = "2026-05-21T06:00:00.000Z";
    expect(formatSpanBefore("2026-05-21T05:25:00.000Z", reference)).toBe("35 min");
    expect(formatSpanBefore("2026-05-20T14:10:00.000Z", reference)).toBe("16 h");
    expect(formatSpanBefore("2026-05-17T06:00:00.000Z", reference)).toBe("4 d");
    expect(formatSpanBefore("nope", reference)).toBe("unknown");
  });
});

describe("labels", () => {
  it("humanises feature names and falls back to the raw name", () => {
    expect(featureLabel("heat_index_c")).toBe("heat index");
    expect(featureLabel("mean_need_last3_litres")).toBe("3-window mean need");
    expect(featureLabel("some_new_feature")).toBe("some new feature");
  });

  it("labels strategies and alert levels", () => {
    expect(strategyLabel("jalpriority")).toBe("JalPriority");
    expect(alertTone(0)).toBe("ok");
    expect(alertTone(2)).toBe("warn");
    expect(alertTone(3)).toBe("danger");
    expect(alertTone(9)).toBe("neutral");
  });

  it("turns reasons into chips with their key parameter", () => {
    expect(reasonChipText({ code: "HIGH_DEBT", params: { debt_litres: 12000 } })).toBe("Accumulated debt · 12,000 L");
    expect(reasonChipText({ code: "HIGH_VULNERABILITY", params: { vulnerability_index: 0.8 } })).toBe("High vulnerability · 0.80");
    expect(reasonChipText({ code: "PARTIALLY_SERVED", params: { allocated_litres: 5000, predicted_shortfall_litres: 14000 } })).toBe(
      "Partially served · 5,000 of 14,000 L",
    );
    expect(reasonChipText({ code: "SUPPLY_EXHAUSTED", params: { supply_available_litres: 20000, unmet_litres: 9000 } })).toBe(
      "Water supply exhausted · 9,000 L unmet",
    );
    expect(reasonChipText({ code: "TIME_LIMITED_SOLUTION", params: { time_limit_s: 8 } })).toBe("Time-limited solution · 8 s limit");
    expect(reasonChipText({ code: "NO_SHORTFALL", params: {} })).toBe("No shortfall");
    expect(reasonChipText({ code: "HIGH_DEBT", params: {} })).toBe("Accumulated debt");
  });

  it("colours outcomes: served is ok, partial is warn, shortage causes are danger", () => {
    expect(reasonTone("FULLY_SERVED")).toBe("ok");
    expect(reasonTone("PARTIALLY_SERVED")).toBe("warn");
    expect(reasonTone("NOT_SERVED")).toBe("danger");
    expect(reasonTone("HIGH_DEBT")).toBe("accent");
  });

  it("explains every reason code allowed on unserved entries", () => {
    for (const code of ["SUPPLY_EXHAUSTED", "TANKER_CAPACITY_LIMIT", "ROUTE_LIMIT", "TRAVEL_COST_TRADEOFF", "LOWER_RELATIVE_PRIORITY"] as const) {
      expect(unservedReasonText(code).length).toBeGreaterThan(20);
    }
  });
});
