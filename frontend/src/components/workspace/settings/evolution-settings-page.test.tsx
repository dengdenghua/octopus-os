import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { RegenerationStatus } from "@/core/regeneration/api";
import { renderWithProviders } from "@/test/harness";

import EvolutionSettingsPage from "./evolution-settings-page";

const getStatusMock = vi.hoisted(() => vi.fn());

vi.mock("@/core/regeneration/api", () => ({
  getRegenerationStatus: getStatusMock,
}));

const status: RegenerationStatus = {
  scheduler: {
    running: true,
    tick_count: 4,
    last_summary: {},
    interval_sec: 600,
  },
  learned_rules: null,
  learned_memories: null,
  workflow_proposals: null,
  recipe_scores: null,
  gepa_proposals: null,
  forged_skills: null,
  camouflage: {
    enabled: true,
    running: true,
    variants: [],
  },
};

describe("EvolutionSettingsPage", () => {
  beforeEach(() => {
    getStatusMock.mockReset();
  });

  it("recovers from an initial loading failure", async () => {
    const user = userEvent.setup();
    getStatusMock
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValue(status);
    renderWithProviders(<EvolutionSettingsPage />, { locale: "zh-CN" });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "加载失败: offline",
    );
    await user.click(screen.getByRole("button", { name: "刷新" }));

    expect(await screen.findByText("调度器状态")).toBeInTheDocument();
    expect(screen.getByText("提示词进化（模型驱动）")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeps the last good snapshot visible when refresh fails", async () => {
    const user = userEvent.setup();
    getStatusMock
      .mockResolvedValueOnce(status)
      .mockRejectedValueOnce(new Error("temporary failure"));
    renderWithProviders(<EvolutionSettingsPage />, { locale: "zh-CN" });

    await screen.findByText("调度器状态");
    await user.click(screen.getByRole("button", { name: "刷新" }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("temporary failure"),
    );
    expect(screen.getByText("调度器状态")).toBeInTheDocument();
  });
});
