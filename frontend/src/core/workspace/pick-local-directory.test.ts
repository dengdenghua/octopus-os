import { afterEach, describe, expect, it, vi } from "vitest";

import { pickLocalDirectory } from "./pick-local-directory";

describe("pickLocalDirectory", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses the desktop bridge when available", async () => {
    const open = vi.fn().mockResolvedValue({
      canceled: false,
      filePaths: ["/Users/example/Project"],
    });
    vi.stubGlobal("echo", { dialog: { open } });

    await expect(pickLocalDirectory("/Users/example")).resolves.toBe(
      "/Users/example/Project",
    );
    expect(open).toHaveBeenCalledWith({
      title: "选择工作区文件夹",
      buttonLabel: "选取",
      message: "请选择一个文件夹作为工作区",
      properties: ["openDirectory", "createDirectory"],
      defaultPath: "/Users/example",
    });
  });

  it("uses the local backend system picker in browser mode", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({
        success: true,
        path: "/Users/example/Project",
        canceled: false,
        error: null,
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(pickLocalDirectory("/Users/example")).resolves.toBe(
      "/Users/example/Project",
    );
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(
        "/api/fs/pick-directory?default_path=%2FUsers%2Fexample",
      ),
      expect.objectContaining({ headers: expect.any(Object) }),
    );
  });

  it("returns null when the user cancels", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: vi.fn().mockResolvedValue({
          success: false,
          path: null,
          canceled: true,
          error: null,
        }),
      }),
    );

    await expect(pickLocalDirectory()).resolves.toBeNull();
  });
});
