import { afterEach, describe, expect, it, vi } from "vitest";

import { enableMarketSkill } from "./api";

describe("enableMarketSkill", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads and enables a bundled marketplace skill", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await enableMarketSkill("creative-3d-animation");

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(
        "/api/skills-market/creative-3d-animation/enable",
      ),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("surfaces the backend error when installation fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "skill not found" }), {
          status: 404,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(enableMarketSkill("missing-skill")).rejects.toThrow(
      "skill not found",
    );
  });
});
