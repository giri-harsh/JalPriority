import type { FC } from "react";
import { describe, expect, it } from "vitest";
import type { DispatchPlanScreenProps, SnapshotScreenProps } from "../contract/types";
import { DispatchPlanScreen, SnapshotScreen } from "../screens";

// Compile-time checks (enforced by `npm run typecheck`): the screens must be usable
// wherever the shell expects a component taking the contract props.
const snapshot: FC<SnapshotScreenProps> = SnapshotScreen;
const plan: FC<DispatchPlanScreenProps> = DispatchPlanScreen;

describe("screen component types", () => {
  it("assigns SnapshotScreen to FC<SnapshotScreenProps> and DispatchPlanScreen to FC<DispatchPlanScreenProps>", () => {
    expect(snapshot).toBe(SnapshotScreen);
    expect(plan).toBe(DispatchPlanScreen);
  });
});
