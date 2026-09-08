import { beforeEach, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  fetchCloudInstalled: vi.fn(),
  fetchRuntimePluginStatuses: vi.fn(),
}));
const setAvailability = vi.hoisted(() => vi.fn());
const identity = vi.hoisted(() => ({ actor: "one" }));
vi.mock("@/core/auth/api", () => ({ currentActorId: () => identity.actor }));

vi.mock("@/core/agents/agent-world-api", () => apiMocks);
vi.mock("@/core/modules/enabled-modules", () => ({
  setModuleAvailabilitySnapshot: setAvailability,
}));

import {
  loadWorkbenchAvailabilitySnapshot,
  syncWorkbenchAvailability,
} from "./availability";

beforeEach(() => {
  identity.actor = "one";
  apiMocks.fetchCloudInstalled.mockReset().mockResolvedValue({
    skills: [],
    plugins: [],
    plugin_states: {},
  });
  apiMocks.fetchRuntimePluginStatuses.mockReset().mockResolvedValue(new Map());
  setAvailability.mockReset();
});

it("never reuses or publishes an old account request after identity changes", async () => {
  let finish!: (value: { skills: string[]; plugins: string[] }) => void;
  apiMocks.fetchCloudInstalled.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const old = syncWorkbenchAvailability();
  identity.actor = "two";
  await syncWorkbenchAvailability();
  expect(apiMocks.fetchCloudInstalled).toHaveBeenCalledTimes(2);
  expect(setAvailability.mock.lastCall?.[0].design).toBe(false);
  const writes = setAvailability.mock.calls.length;
  finish({ skills: [], plugins: ["design"] });
  await old;
  expect(setAvailability).toHaveBeenCalledTimes(writes);
  expect(setAvailability.mock.lastCall?.[0].design).toBe(false);
});

it("does not apply an inventory snapshot captured for a previous account", async () => {
  const snapshot = await loadWorkbenchAvailabilitySnapshot();
  identity.actor = "two";
  await syncWorkbenchAvailability(snapshot);
  expect(setAvailability).not.toHaveBeenCalled();
});

it("shares one boot inventory across concurrent shell and app-center reads", async () => {
  const [snapshot, availability] = await Promise.all([
    loadWorkbenchAvailabilitySnapshot(),
    syncWorkbenchAvailability(),
  ]);

  expect(snapshot.installed.plugins).toEqual([]);
  expect(availability.projects).toBe(true);
  expect(apiMocks.fetchCloudInstalled).toHaveBeenCalledTimes(1);
  expect(apiMocks.fetchRuntimePluginStatuses).toHaveBeenCalledTimes(1);
  expect(setAvailability).toHaveBeenCalledTimes(1);
});

it("does not resurrect an uninstalled package from a stale runtime process", async () => {
  const availability = await syncWorkbenchAvailability({
    installed: {
      skills: [],
      plugins: [],
      plugin_states: {
        paper_trading: { installed: false, enabled: false },
        "paper-trading": { installed: false, enabled: false },
      },
    },
    runtimeStatuses: new Map([
      ["paper_trading", { installed: true, enabled: true }],
    ]),
  });
  expect(availability["paper.trading"]).toBe(false);
});

it("does not overwrite a fresh installed snapshot when an older boot request finishes", async () => {
  let finish!: (value: { skills: string[]; plugins: string[] }) => void;
  apiMocks.fetchCloudInstalled.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const boot = syncWorkbenchAvailability();
  await syncWorkbenchAvailability({
    installed: { skills: [], plugins: ["design"] },
    runtimeStatuses: new Map(),
  });
  expect(setAvailability.mock.lastCall?.[0].design).toBe(true);
  finish({ skills: [], plugins: [] });
  await boot;
  expect(setAvailability).toHaveBeenCalledTimes(1);
  expect(setAvailability.mock.lastCall?.[0].design).toBe(true);
});

it("removes stale remote availability when inventory verification fails", async () => {
  apiMocks.fetchCloudInstalled.mockRejectedValueOnce(new Error("offline"));
  await expect(syncWorkbenchAvailability()).rejects.toThrow("offline");
  expect(setAvailability.mock.lastCall?.[0]).toMatchObject({
    projects: true,
    "local-database": true,
    design: false,
  });
});
