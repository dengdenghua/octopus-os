import { beforeEach, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  fetchCloudInstalled: vi.fn(),
  fetchRuntimePluginStatuses: vi.fn(),
}));
const setAvailability = vi.hoisted(() => vi.fn());

vi.mock("@/core/agents/agent-world-api", () => apiMocks);
vi.mock("@/core/modules/enabled-modules", () => ({
  setModuleAvailabilitySnapshot: setAvailability,
}));

import {
  loadWorkbenchAvailabilitySnapshot,
  syncWorkbenchAvailability,
} from "./availability";

beforeEach(() => {
  apiMocks.fetchCloudInstalled.mockReset().mockResolvedValue({
    skills: [],
    plugins: [],
    plugin_states: {},
  });
  apiMocks.fetchRuntimePluginStatuses.mockReset().mockResolvedValue(new Map());
  setAvailability.mockReset();
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
