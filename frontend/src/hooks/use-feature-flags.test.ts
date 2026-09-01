import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useFeatureFlags } from "./use-feature-flags";

const _SAMPLE = {
  flags: [
    {
      name: "ui.ambient_suggestions",
      value: true,
      source: "env",
      default: false,
      description: "Ambient suggestions panel",
      experimental: true,
      primary_env: "ECHO_FF_UI_AMBIENT_SUGGESTIONS",
      legacy_env: [],
    },
    {
      name: "regeneration.enabled",
      value: false,
      source: "default",
      default: true,
      description: "Self-repair scheduler",
      experimental: false,
      primary_env: "ECHO_FF_REGENERATION_ENABLED",
      legacy_env: ["ECHO_REGEN_ENABLED"],
    },
  ],
};

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.restoreAllMocks();
});

function mockOnce(
  body: unknown,
  init: Partial<{ ok: boolean; status: number }> = {},
) {
  fetchMock.mockResolvedValueOnce({
    ok: init.ok ?? true,
    status: init.status ?? 200,
    json: async () => body,
    text: async () => JSON.stringify(body),
  });
}

describe("useFeatureFlags", () => {
  it("fetches on mount and exposes flags", async () => {
    mockOnce(_SAMPLE);
    const { result } = renderHook(() => useFeatureFlags());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.flags).toHaveLength(2);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/feature-flags",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("isOn returns the flag value", async () => {
    mockOnce(_SAMPLE);
    const { result } = renderHook(() => useFeatureFlags());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.isOn("ui.ambient_suggestions")).toBe(true);
    expect(result.current.isOn("regeneration.enabled")).toBe(false);
  });

  it("isOn returns false for unknown flag", async () => {
    mockOnce(_SAMPLE);
    const { result } = renderHook(() => useFeatureFlags());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.isOn("does.not.exist")).toBe(false);
  });

  it("value returns fallback for unknown flag", async () => {
    mockOnce(_SAMPLE);
    const { result } = renderHook(() => useFeatureFlags());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.value("nope", "fallback")).toBe("fallback");
  });

  it("captures error when fetch returns non-2xx", async () => {
    mockOnce({}, { ok: false, status: 500 });
    const { result } = renderHook(() => useFeatureFlags());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toContain("500");
    expect(result.current.flags).toEqual([]);
  });

  it("manual mode does not auto-fetch", async () => {
    const { result } = renderHook(() => useFeatureFlags({ manual: true }));
    // Let the effect settle.
    await new Promise((r) => setTimeout(r, 10));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(result.current.loading).toBe(false);
  });

  it("reload() POSTs to /reload and refreshes flags", async () => {
    mockOnce(_SAMPLE);
    const { result } = renderHook(() => useFeatureFlags());
    await waitFor(() => expect(result.current.loading).toBe(false));

    const updated = {
      flags: [
        { ..._SAMPLE.flags[0], value: false, source: "file" },
        _SAMPLE.flags[1],
      ],
    };
    mockOnce(updated);

    await act(async () => {
      await result.current.reload();
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/feature-flags/reload",
      expect.objectContaining({ method: "POST" }),
    );
    expect(result.current.isOn("ui.ambient_suggestions")).toBe(false);
  });

  it("respects baseUrl override", async () => {
    mockOnce(_SAMPLE);
    renderHook(() => useFeatureFlags({ baseUrl: "http://remote:9000" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(fetchMock).toHaveBeenCalledWith(
      "http://remote:9000/api/feature-flags",
      expect.any(Object),
    );
  });

  it("shares one in-flight catalog request across simultaneous consumers", async () => {
    let resolveResponse!: (value: unknown) => void;
    fetchMock.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveResponse = resolve;
      }),
    );

    const first = renderHook(() => useFeatureFlags());
    const second = renderHook(() => useFeatureFlags());

    expect(fetchMock).toHaveBeenCalledTimes(1);
    resolveResponse({
      ok: true,
      status: 200,
      json: async () => _SAMPLE,
    });

    await waitFor(() => expect(first.result.current.loading).toBe(false));
    await waitFor(() => expect(second.result.current.loading).toBe(false));
    expect(first.result.current.flags).toHaveLength(2);
    expect(second.result.current.flags).toHaveLength(2);
  });
});
