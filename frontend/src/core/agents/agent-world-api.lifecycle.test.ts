import { beforeEach, expect, it, vi } from "vitest";

vi.mock("@/core/config", () => ({ getBackendBaseURL: () => "" }));
vi.mock("@/core/auth/api", () => ({
  authHeaders: () => ({ Authorization: "Bearer test" }),
  jsonAuthHeaders: () => ({
    Authorization: "Bearer test",
    "Content-Type": "application/json",
  }),
}));

import {
  fetchRuntimePluginStatuses,
  getCapabilityInstallPlan,
  installCapability,
  loadCapabilityIcon,
  setCapabilityEnabled,
  uninstallCapability,
} from "./agent-world-api";

beforeEach(() => {
  vi.restoreAllMocks();
});

it("surfaces the backend reason when capability uninstall is forbidden", async () => {
  vi.spyOn(window, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ detail: "admin/operator role required" }), {
      status: 403,
      headers: { "Content-Type": "application/json" },
    }),
  );

  await expect(uninstallCapability("browser")).rejects.toThrow(
    "Capability uninstall failed: HTTP 403 admin/operator role required",
  );
});

it("loads protected plugin icons with the signed-in user's auth header", async () => {
  const fetchSpy = vi.spyOn(window, "fetch").mockResolvedValue({
    ok: true,
    status: 200,
    blob: async () => new Blob(["<svg></svg>"], { type: "image/svg+xml" }),
  } as Response);

  const icon = await loadCapabilityIcon("/api/capabilities/browser/icon");

  expect(fetchSpy).toHaveBeenCalledWith(
    "/api/capabilities/browser/icon",
    expect.objectContaining({
      headers: { Authorization: "Bearer test" },
    }),
  );
  expect(icon).toMatch(/^data:image\/svg\+xml;base64,/);
});

it("binds install and permission activation to the reviewed plan", async () => {
  const fetchSpy = vi
    .spyOn(window, "fetch")
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          schema: "echo.capability_install_plan.v1",
          capability_id: "browser",
          plan_id: "plan:browser",
          can_install: true,
        }),
        { status: 200 },
      ),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ installed: true, enabled: false }), {
        status: 200,
      }),
    )
    .mockResolvedValueOnce(new Response("{}", { status: 200 }));

  const plan = await getCapabilityInstallPlan("browser");
  await installCapability("browser", plan.plan_id);
  await setCapabilityEnabled(
    "browser",
    true,
    ["content.read"],
    plan.plan_id,
  );

  expect(fetchSpy.mock.calls[1]).toEqual([
    "/api/capabilities/browser/install",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ plan_id: "plan:browser" }),
    }),
  ]);
  expect(fetchSpy.mock.calls[2]).toEqual([
    "/api/capabilities/browser/enable",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({
        grant_permissions: ["content.read"],
        plan_id: "plan:browser",
      }),
    }),
  ]);
});

it("reads optional runtime plugin states from one inventory request", async () => {
  const fetchSpy = vi.spyOn(window, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify([
        {
          id: "paper_trading",
          installed: true,
          enabled: false,
          lifecycle_state: "disabled",
        },
        {
          name: "narrative_studio",
          installed: true,
          enabled: true,
          lifecycle_state: "enabled",
        },
      ]),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ),
  );

  const statuses = await fetchRuntimePluginStatuses();

  expect(fetchSpy).toHaveBeenCalledTimes(1);
  expect(fetchSpy).toHaveBeenCalledWith(
    "/api/plugin-hub/plugins",
    expect.objectContaining({
      headers: { Authorization: "Bearer test" },
    }),
  );
  expect(statuses.get("paper_trading")?.enabled).toBe(false);
  expect(statuses.get("narrative_studio")?.enabled).toBe(true);
});
