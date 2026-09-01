import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Agent } from "@/core/agents";
import { ACTIVE_AGENT_KEY } from "@/core/agents/active";
import { consumeTaskCollaboratorPreset } from "@/core/collaboration/task-collaborator-preset";
import { renderWithProviders } from "@/test/harness";

import { AgentCard } from "./agent-card";

const deleteAgentMock = vi.hoisted(() => vi.fn());

vi.mock("@/core/agents", () => ({
  useDeleteAgent: () => ({
    mutateAsync: deleteAgentMock,
    isPending: false,
  }),
}));

const agent: Agent = {
  name: "custom-role",
  display_name: "自定义角色",
  description: "负责专项协作。",
  icon: "🤖",
  model: null,
  tool_groups: ["web_read", "fs_writer", "git", "不应显示"],
};

describe("AgentCard", () => {
  beforeEach(() => {
    deleteAgentMock.mockReset();
    deleteAgentMock.mockResolvedValue(undefined);
    window.sessionStorage.clear();
    window.localStorage.setItem(ACTIVE_AGENT_KEY, "coder");
  });

  it("presents a concise talent profile with independent primary and detail actions", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    renderWithProviders(
      <AgentCard agent={agent} onSelect={onSelect} isDefault={false} />,
      { locale: "zh-CN" },
    );

    const profileAction = screen.getByRole("button", {
      name: "自定义角色 角色档案",
    });
    expect(
      screen.getByRole("button", { name: "将 自定义角色 按需加入对话" }),
    ).toHaveAttribute("data-agent-entry", "on-demand");
    expect(screen.queryByText("已加入")).not.toBeInTheDocument();
    expect(screen.getByText("网页研究")).toBeInTheDocument();
    expect(screen.getByText("文档交付")).toBeInTheDocument();
    expect(screen.getByText("代码协作")).toBeInTheDocument();
    expect(screen.queryByText("web_read")).not.toBeInTheDocument();
    expect(screen.queryByText("不应显示")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("menuitem", { name: "删除角色 自定义角色" }),
    ).not.toBeInTheDocument();

    profileAction.focus();
    await user.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledWith(agent);
  });

  it("adds a non-squad talent to the active conversation without switching identity", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AgentCard agent={agent} isDefault={false} />, {
      locale: "zh-CN",
    });

    await user.click(
      screen.getByRole("button", { name: "将 自定义角色 按需加入对话" }),
    );

    expect(window.localStorage.getItem(ACTIVE_AGENT_KEY)).toBe("coder");
    expect(consumeTaskCollaboratorPreset()).toEqual({
      leaderId: "coder",
      collaboratorIds: ["custom-role"],
      mode: "cluster",
      label: "自定义角色",
      openPicker: true,
    });
  });

  it("keeps a White Ghost member as a direct conversation identity", () => {
    renderWithProviders(
      <AgentCard
        agent={{ ...agent, name: "coder", display_name: "Kane" }}
        isDefault
        isPrimaryIdentity
      />,
      { locale: "zh-CN" },
    );

    expect(
      screen.getByRole("button", { name: "与 Kane 开聊" }),
    ).toHaveAttribute("data-agent-entry", "identity");
    expect(consumeTaskCollaboratorPreset()).toBeNull();
  });

  it("keeps the role HUB menu available for a default squad member", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    const kane = { ...agent, name: "coder", display_name: "Kane" };
    renderWithProviders(
      <AgentCard
        agent={kane}
        isDefault
        isPrimaryIdentity
        onSelect={onSelect}
      />,
      { locale: "zh-CN" },
    );

    await user.click(screen.getByRole("button", { name: "更多：Kane" }));
    await user.click(screen.getByRole("menuitem", { name: "Kane 角色档案" }));

    expect(onSelect).toHaveBeenCalledWith(kane);
    expect(
      screen.queryByRole("menuitem", { name: "删除角色 Kane" }),
    ).toBeNull();
  });

  it("names the destructive confirmation and deletes the selected role", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AgentCard agent={agent} isDefault={false} />, {
      locale: "zh-CN",
    });

    await user.click(screen.getByRole("button", { name: "更多：自定义角色" }));
    await user.click(
      screen.getByRole("menuitem", { name: "删除角色 自定义角色" }),
    );
    const dialog = screen.getByRole("dialog", {
      name: "删除角色“自定义角色”",
    });
    expect(
      within(dialog).getByText(
        "角色“自定义角色”将被永久删除，此操作无法撤销。",
      ),
    ).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "删除" }));
    expect(deleteAgentMock).toHaveBeenCalledWith("custom-role");
  });
});
