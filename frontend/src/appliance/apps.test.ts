import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  appOpenUrl,
  applianceAppsForDock,
  applianceAppsForLibrary,
  MAX_DOCK_APPLIANCE_APPS,
  startApplianceApp,
  stopApplianceApp,
  type ApplianceApp,
} from "./apps";

vi.mock("@/appliance/auth", () => ({
  authHeader: () => ({ Authorization: "Bearer browser-session" }),
}));

beforeEach(() => {
  vi.unstubAllGlobals();
});

function app(id: string, overrides: Partial<ApplianceApp> = {}): ApplianceApp {
  return {
    id,
    name: `App ${id}`,
    description: "Hub application",
    icon: "",
    state: "running",
    status: "Up",
    image: `example/${id}@sha256:digest`,
    web_port: 8000,
    web_url: null,
    ports: [8000],
    ...overrides,
  };
}

function currentDisplayHost(): string {
  const hostname = window.location.hostname.replace(/^\[|\]$/g, "");
  return hostname.includes(":") ? `[${hostname}]` : hostname;
}

describe("appliance app launch targets", () => {
  it("rebinds a trusted LAN label to the NAS address visible to this browser", () => {
    expect(
      appOpenUrl(
        app("jellyfin", {
          web_url: "https://nas.local:8443/web?source=hub#library",
        }),
      ),
    ).toBe(`https://${currentDisplayHost()}:8443/web?source=hub#library`);
  });

  it.each([
    "https://public.example/app",
    "https://fc-public.example/app",
    "http://user:password@nas.local:8096/app",
    "javascript:alert(1)",
  ])(
    "rejects an unsafe label and falls back to its published port: %s",
    (webUrl) => {
      expect(
        appOpenUrl(app("unsafe", { web_url: webUrl, web_port: 8096 })),
      ).toBe(`http://${currentDisplayHost()}:8096`);
    },
  );

  it("does not expose an app that has neither a safe label nor a published port", () => {
    expect(
      appOpenUrl(
        app("hidden", {
          web_url: "https://public.example/app",
          web_port: null,
          ports: [],
        }),
      ),
    ).toBeNull();
  });

  it("keeps every launchable Hub app in the library while bounding the Dock", () => {
    const apps = Array.from({ length: 9 }, (_, index) =>
      app(String(index + 1)),
    );

    expect(applianceAppsForLibrary(apps).map(({ id }) => id)).toEqual([
      "1",
      "2",
      "3",
      "4",
      "5",
      "6",
      "7",
      "8",
      "9",
    ]);
    expect(applianceAppsForDock(apps).map(({ id }) => id)).toEqual([
      "1",
      "2",
      "3",
      "4",
      "5",
      "6",
    ]);
    expect(MAX_DOCK_APPLIANCE_APPS).toBe(6);
  });
});

describe("appliance app control", () => {
  it.each(["start", "stop"] as const)(
    "%s consumes a dedicated high-risk approval token",
    async (action) => {
      const fetchMock = vi
        .fn()
        .mockResolvedValue(new Response("{}", { status: 200 }));
      vi.stubGlobal("fetch", fetchMock);

      const call = action === "start" ? startApplianceApp : stopApplianceApp;
      await call("a".repeat(12), "one-shot.signature");

      expect(fetchMock).toHaveBeenCalledWith(
        `/api/appliance/apps/${"a".repeat(12)}/${action}`,
        {
          method: "POST",
          headers: {
            Authorization: "Bearer browser-session",
            "X-Echo-Approval": "one-shot.signature",
          },
        },
      );
    },
  );
});
