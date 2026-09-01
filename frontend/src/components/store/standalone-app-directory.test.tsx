import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OPEN_ECHO_HUB_EVENT } from "@/core/apps/app-presentation";

import { StandaloneAppDirectory } from "./standalone-app-directory";

describe("StandaloneAppDirectory", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("projects the Echo Hub catalog as standalone apps and opens the shared manager", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              schema: "echo.hub.catalog-response.v1",
              version: "test",
              digest: "digest",
              publisher: { id: "echo", name: "Echo" },
              architecture: "arm64",
              runtime: { available: true, error: null },
              total: 1,
              apps: [
                {
                  id: "immich",
                  name: "Immich",
                  nameZh: "智能相册",
                  version: "3.1.0",
                  summary: "家庭照片与视频入口",
                  category: "photos",
                  icon: "photos",
                  sourceUrl: "https://immich.app/",
                  featured: true,
                  imageStorage: null,
                  package: null,
                  bundle: null,
                  integrationStatus: "available",
                  integrationNote: "ready",
                  installation: {
                    installed: false,
                    containerId: null,
                    state: "not-installed",
                    status: "",
                    image: null,
                    version: null,
                  },
                  installable: true,
                  installBlockers: [],
                  updateAvailable: false,
                },
              ],
            }),
            { status: 200 },
          ),
      ),
    );
    const listener = vi.fn();
    window.addEventListener(OPEN_ECHO_HUB_EVENT, listener);

    render(<StandaloneAppDirectory />);
    expect(await screen.findByText("智能相册")).toBeInTheDocument();
    expect(screen.getByText("家庭照片与视频入口")).toBeInTheDocument();
    expect(screen.getByText("可安装")).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: "在 Echo Hub 中管理智能相册" }),
    );
    await waitFor(() => expect(listener).toHaveBeenCalledOnce());
    expect((listener.mock.calls[0]?.[0] as CustomEvent).detail).toEqual({
      appId: "immich",
    });
    window.removeEventListener(OPEN_ECHO_HUB_EVENT, listener);
  });
});
