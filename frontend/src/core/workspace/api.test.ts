import { beforeEach, expect, it, vi } from "vitest";

vi.mock("@/core/config", () => ({ getBackendBaseURL: () => "" }));
vi.mock("@/core/auth/api", () => ({
  authHeaders: () => ({ Authorization: "Bearer session" }),
  jsonAuthHeaders: () => ({
    Authorization: "Bearer session",
    "Content-Type": "application/json",
  }),
}));

import { listWorkspaces } from "./api";

beforeEach(() => {
  vi.restoreAllMocks();
});

it("lets the backend derive the workspace actor from the authenticated session", async () => {
  const fetchSpy = vi.spyOn(window, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ workspaces: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );

  await expect(listWorkspaces()).resolves.toEqual([]);
  expect(fetchSpy).toHaveBeenCalledWith("/api/workspaces", {
    headers: { Authorization: "Bearer session" },
  });
});
