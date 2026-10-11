import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { SCREEN_CONTRACT_VERSION } from "../contract/types";
import * as screens from "../screens";
import { contractMarkdownPath } from "../test/support";

const normalise = (text: string) => text.replace(/\s+/g, " ").trim();

function contractSection94(): string {
  const markdown = readFileSync(contractMarkdownPath, "utf-8");
  const match = /### 9\.4 Props contract.*?```ts\n(.*?)```/s.exec(markdown);
  if (!match?.[1]) throw new Error("Section 9.4 code block not found in CONTRACT.md");
  return match[1];
}

describe("contract/types.ts", () => {
  it("equals the CONTRACT.md section 9.4 block after whitespace normalisation", () => {
    const here = dirname(fileURLToPath(import.meta.url));
    const ours = readFileSync(resolve(here, "../contract/types.ts"), "utf-8");
    expect(normalise(ours)).toBe(normalise(contractSection94()));
  });
});

describe("screens/index.ts", () => {
  it("exports exactly SnapshotScreen, DispatchPlanScreen and A_SCREENS_CONTRACT_VERSION", () => {
    expect(Object.keys(screens).sort()).toEqual(["A_SCREENS_CONTRACT_VERSION", "DispatchPlanScreen", "SnapshotScreen"]);
  });

  it("has no default export and the contract version of the props contract", () => {
    expect("default" in screens).toBe(false);
    expect(screens.A_SCREENS_CONTRACT_VERSION).toBe("1.0.0");
    expect(screens.A_SCREENS_CONTRACT_VERSION).toBe(SCREEN_CONTRACT_VERSION);
    expect(typeof screens.SnapshotScreen).toBe("function");
    expect(typeof screens.DispatchPlanScreen).toBe("function");
  });
});
