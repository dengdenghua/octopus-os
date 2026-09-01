import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  applyCoderModelProfileBoundary,
  approveCoderUpstreamUpdate,
  cancelCoderLogin,
  checkCoderUpstreamUpdate,
  coderQueryKeys,
  getCoderModelProfile,
  getCoderApps,
  getCoderRateLimits,
  getCoderUpstreamUpdate,
  getCoderUsage,
  startCoderLogin,
  updateCoderModelProfile,
  updateCoderApps,
} from "./api";

const fetchMock = vi.fn();

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

beforeEach(() => {
  localStorage.clear();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Coder Codex API", () => {
  it("maps the wire chatgpt mode to the product-level Codex account source", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        mode: "chatgpt",
        effective_model: "gpt-5.6-codex",
        system_model: "gpt-5.6",
        reasoning_effort: "high",
        compatible: true,
        compatibility_reason: null,
        provider: "openai",
      }),
    );

    await expect(getCoderModelProfile()).resolves.toMatchObject({
      source: "codex_account",
      effective_model: "gpt-5.6-codex",
      compatible: true,
    });
  });

  it("maps the Codex account source back to the existing backend wire contract", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        mode: "chatgpt",
        effective_model: "gpt-5.6-codex",
        system_model: "gpt-5.6",
        reasoning_effort: "xhigh",
        compatible: true,
        compatibility_reason: null,
        provider: "openai",
      }),
    );

    await updateCoderModelProfile({
      source: "codex_account",
      model: "gpt-5.6-codex",
      reasoning_effort: "xhigh",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/coder/codex/model-profile"),
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({
          mode: "chatgpt",
          model: "gpt-5.6-codex",
          reasoning_effort: "xhigh",
        }),
      }),
    );
  });

  it("submits an API key only in the request body and never browser storage or URL", async () => {
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    fetchMock.mockResolvedValue(jsonResponse({ type: "apiKey" }));

    await startCoderLogin("apiKey", "sk-sensitive-value");

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/api\/coder\/codex\/login$/);
    expect(url).not.toContain("sk-sensitive-value");
    expect(JSON.parse(String(init.body))).toEqual({
      type: "apiKey",
      api_key: "sk-sensitive-value",
    });
    expect(storageWrite).not.toHaveBeenCalled();
    expect(JSON.stringify(localStorage)).not.toContain("sk-sensitive-value");
  });

  it("uses the explicit non-destructive cancellation endpoint", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ cancelled: true }));

    await expect(cancelCoderLogin("login/id")).resolves.toBe(true);

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/coder/codex/login/login%2Fid/cancel"),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("reads account quota and usage from separate credential-free endpoints", async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({ buckets: [], reset_credits_available: null }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          summary: { lifetime_tokens: 42 },
          daily_usage_buckets: [],
        }),
      );

    await expect(getCoderRateLimits()).resolves.toMatchObject({ buckets: [] });
    await expect(getCoderUsage()).resolves.toMatchObject({
      summary: { lifetime_tokens: 42 },
    });
    expect(fetchMock.mock.calls[0]?.[0]).toContain("/rate-limits");
    expect(fetchMock.mock.calls[1]?.[0]).toContain("/usage");
    expect(fetchMock.mock.calls.flat().join(" ")).not.toContain("sk-");
  });

  it("reads and updates only explicit connector ids", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ apps: [] }))
      .mockResolvedValueOnce(jsonResponse({ apps: [] }));

    await getCoderApps();
    await updateCoderApps(["google_drive"]);

    expect(fetchMock.mock.calls[0]?.[0]).toContain("/apps");
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({
      method: "PUT",
      body: JSON.stringify({ app_ids: ["google_drive"] }),
    });
  });

  it("checks and approves Codex updates without installing them", async () => {
    const candidate = {
      package: "@openai/codex",
      current_version: "0.149.0",
      latest_version: "0.150.0",
      update_available: true,
      checked_at: "2026-08-23T00:00:00Z",
      source_url: "https://registry.npmjs.org/@openai%2Fcodex/latest",
      release_url: "https://github.com/openai/codex/releases",
      integrity: "sha512-safe",
      tarball_url: "https://registry.npmjs.org/codex.tgz",
      approval_status: "pending",
      approved_version: null,
      approved_at: null,
      error: null,
    } as const;
    fetchMock.mockResolvedValue(jsonResponse(candidate));

    await getCoderUpstreamUpdate();
    await checkCoderUpstreamUpdate();
    await approveCoderUpstreamUpdate("0.150.0");

    expect(fetchMock.mock.calls[0]?.[0]).toContain("/upstream-update");
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({ method: "POST" });
    expect(fetchMock.mock.calls[2]?.[1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({ version: "0.150.0" }),
    });
    expect(fetchMock.mock.calls.flat().join(" ")).not.toContain("install");
  });

  it("partitions cached account state and strips ordinary model overrides for Coder", () => {
    expect(coderQueryKeys("actor-a").account).not.toEqual(
      coderQueryKeys("actor-b").account,
    );
    expect(
      applyCoderModelProfileBoundary("coder", {
        agent_name: "coder",
        model_name: "deepseek-v4",
        reasoning_effort: "xhigh",
        partner_model: "legacy-model",
        mode: "code",
      }),
    ).toEqual({ agent_name: "coder", mode: "code" });
    const ordinary = { model_name: "deepseek-v4" };
    expect(applyCoderModelProfileBoundary("researcher", ordinary)).toBe(
      ordinary,
    );
    expect(
      applyCoderModelProfileBoundary(
        "custom-coder",
        { model_name: "chatgpt/gpt-5.6-sol", reasoning_effort: "high" },
        "codex",
      ),
    ).toEqual({});
  });
});
