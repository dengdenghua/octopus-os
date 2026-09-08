import { afterEach, describe, expect, it, vi } from "vitest";

import { updateNASPolicy } from "@/core/storage/api";
import { AI_MODE_CHANGED, setSystemAiMode } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("system privacy policy", () => {
  it("publishes a change only after the server confirms it", async () => {
    const announce = vi.spyOn(window, "dispatchEvent");
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ mode: "privacy" })));
    vi.stubGlobal("fetch", fetchMock);
    await expect(setSystemAiMode("privacy")).resolves.toEqual({
      mode: "privacy",
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/ai-mode",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ mode: "privacy" }),
      }),
    );
    expect(announce).toHaveBeenCalledWith(
      expect.objectContaining({ type: AI_MODE_CHANGED }),
    );
  });

  it("does not claim privacy is enabled when persistence fails", async () => {
    const announce = vi.spyOn(window, "dispatchEvent");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "隐私设置保存失败" }), {
          status: 503,
        }),
      ),
    );
    await expect(setSystemAiMode("privacy")).rejects.toThrow(
      "隐私设置保存失败",
    );
    expect(announce).not.toHaveBeenCalled();
  });

  it("rejects malformed success responses", async () => {
    const announce = vi.spyOn(window, "dispatchEvent");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}")));
    await expect(setSystemAiMode("privacy")).rejects.toThrow();
    expect(announce).not.toHaveBeenCalled();
  });

  it("the database switch writes the same system endpoint and reads effective policy", async () => {
    const policy = {
      mode: "privacy" as const,
      allow_cloud_answering: false,
      allow_snippet_export: false,
      max_exported_snippets: 8,
      max_snippet_chars: 600,
      redact_file_paths_for_cloud: true,
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ mode: "privacy" })))
      .mockResolvedValueOnce(new Response(JSON.stringify(policy)));
    vi.stubGlobal("fetch", fetchMock);
    await expect(updateNASPolicy(policy)).resolves.toEqual(policy);
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/ai-mode",
      "/api/storage/v1/policy",
    ]);
    expect(fetchMock.mock.calls[1]?.[1]?.method).toBeUndefined();
  });
});
