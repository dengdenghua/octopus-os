import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { CloudSkillsPanel } from "./cloud-skills-panel";

const mocks = vi.hoisted(() => ({
  fetchCloudSkills: vi.fn(),
  fetchCloudInstalled: vi.fn(),
  fetchUnifiedAssets: vi.fn(),
  streamInstallCloudSkill: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/core/agents/agent-world-api", () => ({
  fetchCloudSkills: mocks.fetchCloudSkills,
  fetchCloudInstalled: mocks.fetchCloudInstalled,
  fetchUnifiedAssets: mocks.fetchUnifiedAssets,
  streamInstallCloudSkill: mocks.streamInstallCloudSkill,
}));

vi.mock("sonner", () => ({ toast: mocks.toast }));

describe("CloudSkillsPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.fetchCloudSkills.mockResolvedValue({
      total: 2,
      items: [
        { name: "writing", description: "云端写作技能", version: "1.0.0" },
        { name: "research", description: "云端研究技能", version: "1.0.0" },
      ],
    });
    mocks.fetchCloudInstalled.mockResolvedValue({ skills: [], plugins: [] });
    mocks.fetchUnifiedAssets.mockResolvedValue({
      total: 2,
      summary: { counts: { skill: 2 } },
      items: [
        {
          id: "writing",
          name: "writing",
          kind: "skill",
          source: "local",
          description: "本地写作技能",
        },
        {
          id: "local-only",
          name: "local-only",
          kind: "skill",
          source: "codex",
          description: "仅存在于本地",
        },
      ],
    });
  });

  it("merges cloud and local skills and marks local matches as installed", async () => {
    renderWithProviders(<CloudSkillsPanel />, { locale: "zh-CN" });

    const writingCard = (await screen.findByText("writing")).closest(
      '[data-slot="card"]',
    );
    const localOnlyCard = screen
      .getByText("local-only")
      .closest('[data-slot="card"]');
    expect(writingCard).not.toBeNull();
    expect(localOnlyCard).not.toBeNull();
    expect(
      within(writingCard!).getByRole("button", { name: "已安装" }),
    ).toBeDisabled();
    expect(
      within(localOnlyCard!).getByRole("button", { name: "已安装" }),
    ).toBeDisabled();
    expect(screen.getByText("2/3 已安装")).toBeVisible();
  });

  it("streams installation progress and commits the installed state", async () => {
    mocks.streamInstallCloudSkill.mockImplementation(
      async (
        name: string,
        onProgress: (event: Record<string, unknown>) => void,
      ) => {
        onProgress({ phase: "installing", progress: 45, message: "正在下载" });
        onProgress({
          phase: "completed",
          progress: 100,
          message: "安装完成",
          result: { installed: true, name, path: `/skills/${name}` },
        });
        return { installed: true, name, path: `/skills/${name}` };
      },
    );
    const user = userEvent.setup();
    renderWithProviders(<CloudSkillsPanel />, { locale: "zh-CN" });

    const researchCard = (await screen.findByText("research")).closest(
      '[data-slot="card"]',
    );
    await user.click(
      within(researchCard!).getByRole("button", { name: "安装" }),
    );

    await waitFor(() => {
      expect(mocks.streamInstallCloudSkill).toHaveBeenCalledWith(
        "research",
        expect.any(Function),
      );
      expect(
        within(researchCard!).getByRole("button", { name: "已安装" }),
      ).toBeDisabled();
    });
    expect(mocks.toast.success).toHaveBeenCalledWith("技能「research」已安装");
  });
});
