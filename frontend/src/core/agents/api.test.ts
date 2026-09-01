import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/core/auth/api", () => ({
  authHeaders: () => ({ Authorization: "Bearer test" }),
  jsonAuthHeaders: () => ({ "Content-Type": "application/json" }),
}));

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "https://backend.example.test",
  getControlPlaneBaseURL: () => "https://backend.example.test",
}));

import { listAgents } from "./api";

describe("listAgents", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("uses the compact roster endpoint and hides system personas", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify([
          { name: "general", display_name: "Echo" },
          { name: "admin", display_name: "Admin" },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const agents = await listAgents();

    expect(fetchMock).toHaveBeenCalledWith(
      "https://backend.example.test/api/agents?include_visuals=false",
      expect.objectContaining({ cache: "no-store" }),
    );
    expect(agents.map((agent) => agent.name)).toEqual(["general"]);
  });

  it("aborts a roster request that stalls for five seconds", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn((_url: string, init?: RequestInit) => {
        return new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener(
            "abort",
            () => reject(new DOMException("Aborted", "AbortError")),
            { once: true },
          );
        });
      }),
    );

    const request = listAgents();
    const rejection = expect(request).rejects.toMatchObject({
      name: "AbortError",
    });
    await vi.advanceTimersByTimeAsync(5_000);

    await rejection;
  });
});
