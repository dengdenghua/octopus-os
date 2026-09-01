import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAmbientSuggestions } from "./use-ambient-suggestions";

const _BUCKET_DISABLED = {
  project_root: "/p",
  generated_at: "",
  suggestions: [],
  enabled: false,
};

const _BUCKET_TWO = {
  project_root: "/p",
  generated_at: "2026-05-08T10:00:00Z",
  suggestions: [
    {
      id: "abc",
      project_root: "/p",
      title: "Fix something",
      description: "",
      prompt: "do it",
      locale: "en-US",
      status: "pending",
      source_turn_ids: [],
      created_at: "",
      updated_at: "",
      model: null,
      experimental: true,
    },
    {
      id: "def",
      project_root: "/p",
      title: "Other",
      description: "",
      prompt: "x",
      locale: "en-US",
      status: "dismissed",
      source_turn_ids: [],
      created_at: "",
      updated_at: "",
      model: null,
      experimental: true,
    },
  ],
  enabled: true,
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

describe("useAmbientSuggestions", () => {
  it("fetches on mount with project query param", async () => {
    mockOnce(_BUCKET_DISABLED);
    renderHook(() => useAmbientSuggestions("/some/project"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toContain("/api/ambient-suggestions?project=");
    expect(url).toContain(encodeURIComponent("/some/project"));
  });

  it("scopes reads and generation to the global locale", async () => {
    mockOnce(_BUCKET_DISABLED);
    const { result } = renderHook(() =>
      useAmbientSuggestions("/p", { locale: "zh-CN" }),
    );

    await waitFor(() => expect(result.current.bucket).not.toBe(null));
    expect(fetchMock.mock.calls[0][0]).toContain("locale=zh-CN");

    mockOnce({ added: 1, generated: 1, error: null });
    mockOnce(_BUCKET_TWO);
    await act(async () => {
      await result.current.generate("coder");
    });

    const runCall = fetchMock.mock.calls.find(
      (call) => (call[1] as RequestInit | undefined)?.method === "POST",
    );
    const body = JSON.parse(String((runCall?.[1] as RequestInit)?.body));
    expect(body.locale).toBe("zh-CN");
  });

  it("returns empty bucket when project is null", async () => {
    const { result } = renderHook(() => useAmbientSuggestions(null));
    await new Promise((r) => setTimeout(r, 10));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(result.current.bucket).toBe(null);
  });

  it("captures bucket on success", async () => {
    mockOnce(_BUCKET_TWO);
    const { result } = renderHook(() => useAmbientSuggestions("/p"));
    await waitFor(() => expect(result.current.bucket).not.toBe(null));
    expect(result.current.bucket?.suggestions).toHaveLength(2);
    expect(result.current.bucket?.enabled).toBe(true);
  });

  it("auto: false skips initial fetch", async () => {
    const { result } = renderHook(() =>
      useAmbientSuggestions("/p", { auto: false }),
    );
    await new Promise((r) => setTimeout(r, 10));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(result.current.bucket).toBe(null);
  });

  it("generate() POSTs and refreshes", async () => {
    mockOnce(_BUCKET_DISABLED); // initial
    const { result } = renderHook(() => useAmbientSuggestions("/p"));
    await waitFor(() => expect(result.current.bucket).not.toBe(null));

    mockOnce({ added: 3, generated: 3, error: null }); // /run
    mockOnce(_BUCKET_TWO); // refresh
    await act(async () => {
      const out = await result.current.generate("coder");
      expect(out.added).toBe(3);
    });

    const calls = fetchMock.mock.calls.map((c) => c[0] as string);
    expect(calls.some((u) => u.endsWith("/api/ambient-suggestions/run"))).toBe(
      true,
    );
    expect(result.current.bucket?.suggestions).toHaveLength(2);
  });

  it("generate() returns error from non-2xx", async () => {
    mockOnce(_BUCKET_DISABLED);
    const { result } = renderHook(() => useAmbientSuggestions("/p"));
    await waitFor(() => expect(result.current.bucket).not.toBe(null));

    mockOnce({ detail: "blocked" }, { ok: false, status: 403 });
    let out: { added: number; generated: number; error: string | null };
    await act(async () => {
      out = await result.current.generate("coder");
    });
    expect(out!.error).toBeTruthy();
  });

  it("setStatus() PATCHes the suggestion", async () => {
    mockOnce(_BUCKET_TWO); // initial
    const { result } = renderHook(() => useAmbientSuggestions("/p"));
    await waitFor(() => expect(result.current.bucket).not.toBe(null));

    mockOnce({ ok: true }); // PATCH
    mockOnce(_BUCKET_TWO); // refresh

    await act(async () => {
      await result.current.setStatus("abc", "accepted");
    });

    const patchCall = fetchMock.mock.calls.find(
      (c) => (c[1] as RequestInit | undefined)?.method === "PATCH",
    );
    expect(patchCall).toBeDefined();
    expect(patchCall![0]).toContain("/api/ambient-suggestions/abc");
  });

  it("clear() DELETEs with optional status filter", async () => {
    mockOnce(_BUCKET_TWO);
    const { result } = renderHook(() => useAmbientSuggestions("/p"));
    await waitFor(() => expect(result.current.bucket).not.toBe(null));

    mockOnce({ removed: 1 }); // DELETE
    mockOnce(_BUCKET_TWO); // refresh

    await act(async () => {
      await result.current.clear("dismissed");
    });

    const deleteCall = fetchMock.mock.calls.find(
      (c) => (c[1] as RequestInit | undefined)?.method === "DELETE",
    );
    expect(deleteCall).toBeDefined();
    expect(deleteCall![0]).toContain("status=dismissed");
  });
});
