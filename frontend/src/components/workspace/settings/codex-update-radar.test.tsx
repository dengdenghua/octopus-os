import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type * as CoderApiModule from "@/core/coder/api";
import { renderWithProviders } from "@/test/harness";

import { CodexUpdateRadar } from "./codex-update-radar";

const api = vi.hoisted(() => ({
  get: vi.fn(),
  check: vi.fn(),
  approve: vi.fn(),
}));

vi.mock("@/core/coder/api", async (importOriginal) => {
  const original = await importOriginal<typeof CoderApiModule>();
  return {
    ...original,
    getCoderUpstreamUpdate: api.get,
    checkCoderUpstreamUpdate: api.check,
    approveCoderUpstreamUpdate: api.approve,
  };
});

vi.mock("@/providers/AuthProvider", () => ({
  useAuth: () => ({ user: { roles: ["admin"] } }),
}));

const pending = {
  package: "@openai/codex",
  current_version: "0.149.0",
  latest_version: "0.150.0",
  update_available: true,
  checked_at: "2026-08-23T00:00:00Z",
  source_url: "https://registry.npmjs.org/@openai%2Fcodex/latest",
  release_url: "https://github.com/openai/codex/releases",
  integrity: "sha512-safe",
  tarball_url: "https://registry.npmjs.org/codex.tgz",
  approval_status: "pending" as const,
  approved_version: null,
  approved_at: null,
  error: null,
};

describe("CodexUpdateRadar", () => {
  beforeEach(() => {
    api.get.mockReset().mockResolvedValue(pending);
    api.check.mockReset().mockResolvedValue(pending);
    api.approve.mockReset().mockResolvedValue({
      ...pending,
      approval_status: "approved_for_next_release",
      approved_version: "0.150.0",
      approved_at: "2026-08-23T01:00:00Z",
    });
  });

  it("shows the pinned and upstream versions and safely approves the candidate", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CodexUpdateRadar />, { locale: "zh-CN" });

    expect(await screen.findByText("0.149.0")).toBeInTheDocument();
    expect(screen.getByText("0.150.0")).toBeInTheDocument();
    expect(screen.getByText(/不会热替换当前引擎/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "批准纳入下一版本" }));

    expect(api.approve.mock.calls[0]?.[0]).toBe("0.150.0");
    expect(await screen.findByText(/已批准，等待随下一版/)).toBeInTheDocument();
  });

  it("allows a manual metadata refresh", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CodexUpdateRadar />, { locale: "zh-CN" });
    await screen.findByText("0.149.0");

    await user.click(
      screen.getByRole("button", { name: "检查 Codex 引擎更新" }),
    );

    expect(api.check).toHaveBeenCalledTimes(1);
  });
});
