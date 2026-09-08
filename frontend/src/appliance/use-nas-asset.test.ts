import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { loadNASAssetURL } from "@/core/storage/api";

import { useNASAsset, useNASAssetState } from "./use-nas-asset";

vi.mock("@/core/storage/api", () => ({
  loadNASAssetURL: vi.fn(),
}));

const loadAsset = vi.mocked(loadNASAssetURL);

describe("useNASAsset", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    loadAsset.mockReset();
  });

  it("clears and revokes the previous preview when the path changes", async () => {
    loadAsset.mockResolvedValue("blob:first");
    const revoke = vi
      .spyOn(URL, "revokeObjectURL")
      .mockImplementation(() => undefined);
    const { result, rerender } = renderHook(
      ({ path }: { path: string | undefined }) => useNASAsset(path),
      { initialProps: { path: "/first.png" } },
    );

    await waitFor(() => expect(result.current).toBe("blob:first"));
    rerender({ path: undefined });

    await waitFor(() => expect(result.current).toBeNull());
    expect(revoke).toHaveBeenCalledWith("blob:first");
  });

  it("does not update state after an unmounted request resolves", async () => {
    let resolve: (value: string) => void = () => undefined;
    loadAsset.mockImplementation(
      () =>
        new Promise<string>((nextResolve) => {
          resolve = nextResolve;
        }),
    );
    const revoke = vi
      .spyOn(URL, "revokeObjectURL")
      .mockImplementation(() => undefined);
    const { result, unmount } = renderHook(() => useNASAsset("/slow.png"));
    unmount();
    resolve("blob:late");
    await Promise.resolve();
    expect(result.current).toBeNull();
    expect(revoke).toHaveBeenCalledWith("blob:late");
  });

  it("revokes a late URL from a superseded path", async () => {
    let resolveFirst: (value: string) => void = () => undefined;
    loadAsset.mockImplementation((path) => {
      if (path === "/first.png") {
        return new Promise<string>((resolve) => {
          resolveFirst = resolve;
        });
      }
      return Promise.resolve("blob:second");
    });
    const revoke = vi
      .spyOn(URL, "revokeObjectURL")
      .mockImplementation(() => undefined);
    const { result, rerender } = renderHook(
      ({ path }: { path: string | undefined }) => useNASAsset(path),
      { initialProps: { path: "/first.png" } },
    );
    rerender({ path: "/second.png" });
    expect(result.current).toBeNull();
    await waitFor(() => expect(result.current).toBe("blob:second"));
    resolveFirst("blob:late-first");
    await waitFor(() => expect(revoke).toHaveBeenCalledWith("blob:late-first"));
  });

  it("exposes loading and failure state without changing URL ownership", async () => {
    loadAsset.mockRejectedValue(new Error("服务暂不可用"));
    const { result } = renderHook(() => useNASAssetState("/broken.png"));
    await waitFor(() =>
      expect(result.current).toEqual({
        url: null,
        loading: false,
        error: "服务暂不可用",
      }),
    );
  });
});
