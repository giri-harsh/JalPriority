import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient, ApiError, describeError, isSessionExpired, SESSION_EXPIRED_MESSAGE } from "../screens/api";
import { installFetchMock, scenarioFixture } from "../test/support";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ApiClient", () => {
  it("builds URLs from the base prop, tolerating a trailing slash, and sends the bearer token", async () => {
    const mock = installFetchMock();
    const client = new ApiClient("/a/v1/", "abc");
    await client.getModel();
    await client.listScenarios();
    await client.getScenario("scn_sa01_w06__d1_3f9a2c");
    await client.getPredictions("scn_sa01_w06");
    expect(mock.mock.calls.map(([url]) => String(url))).toEqual([
      "/a/v1/model",
      "/a/v1/scenarios",
      "/a/v1/scenarios/scn_sa01_w06__d1_3f9a2c",
      "/a/v1/scenarios/scn_sa01_w06/predictions",
    ]);
    for (const [, init] of mock.mock.calls) {
      expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer abc");
    }
  });

  it("omits the Authorization header when there is no token", async () => {
    const mock = installFetchMock();
    await new ApiClient("/a/v1", "").getModel();
    expect(mock.mock.calls[0]![1]?.headers).not.toHaveProperty("Authorization");
  });

  it("returns parsed payloads", async () => {
    installFetchMock();
    const scenario = await new ApiClient("/a/v1", "t").getScenario("scn_sa01_w06");
    expect(scenario.areas).toHaveLength(scenarioFixture.areas.length);
    expect(await new ApiClient("/a/v1", "t").listScenarios()).toHaveLength(1);
  });

  it("turns the error envelope into an ApiError with status, code and message", async () => {
    installFetchMock({ failing: { "/model": 503 } });
    const error = await new ApiClient("/a/v1", "t").getModel().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 503, code: "INTERNAL_ERROR", message: "Something broke on the server." });
    expect(describeError(error)).toBe("Something broke on the server.");
    expect(isSessionExpired(error)).toBe(false);
  });

  it("maps 401 to the session-expired wording", async () => {
    installFetchMock({ unauthorized: true });
    const error = await new ApiClient("/a/v1", "t").getModel().catch((e: unknown) => e);
    expect(isSessionExpired(error)).toBe(true);
    expect(describeError(error)).toBe(SESSION_EXPIRED_MESSAGE);
    expect(SESSION_EXPIRED_MESSAGE).toBe("Session expired, please log in again");
  });

  it("copes with non-JSON error bodies and unreachable servers", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>bad gateway</html>", { status: 502 })));
    const bad = await new ApiClient("/a/v1", "t").getModel().catch((e: unknown) => e);
    expect(bad).toMatchObject({ status: 502, message: "Request failed (HTTP 502)", code: null });

    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    const down = await new ApiClient("/a/v1", "t").getModel().catch((e: unknown) => e);
    expect(down).toMatchObject({ status: 0 });
    expect(describeError(down)).toMatch(/Cannot reach/);
    expect(describeError("boom")).toBe("Unexpected error");
  });

  it("posts JSON bodies", async () => {
    const mock = installFetchMock();
    await new ApiClient("/a/v1", "t").post("/plans/solve", { scenario_id: "scn_sa01_w06" });
    const [, init] = mock.mock.calls[0]!;
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ scenario_id: "scn_sa01_w06" });
    expect((init?.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });
});
