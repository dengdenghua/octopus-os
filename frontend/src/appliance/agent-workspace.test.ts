import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  delete window.__ECHO_STORAGE_URL__;
  vi.restoreAllMocks();
});

describe("embedded Agent workspace routes", () => {
  it("keeps Agent application routes inside the OS frontend", async () => {
    const { resolveAgentAppUrl } = await import("./agent-workspace");
    expect(resolveAgentAppUrl("workspace/realtime/new")).toBe(
      "/workspace/realtime/new",
    );
    expect(resolveAgentAppUrl("/workspace/storage?surface=company")).toBe(
      "/workspace/storage?surface=company",
    );
  });

  it("loads storage config without accepting a second UI location", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          storage_url: "http://127.0.0.1:8767",
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );
    const { loadAgentWorkspaceConfig, resolveAgentAppUrl } =
      await import("./agent-workspace");
    await loadAgentWorkspaceConfig();
    expect(resolveAgentAppUrl("/workspace/observability")).toBe(
      "/workspace/observability",
    );
    expect(window.__ECHO_STORAGE_URL__).toBe("http://127.0.0.1:8767");
  });
});
