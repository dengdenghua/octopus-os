import { useState } from "react";
import { act, fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

const mocks = vi.hoisted(() => ({
  createProject: vi.fn(),
  navigate: vi.fn(),
  agentState: {
    agents: [
      {
        name: "general",
        display_name: "通用助手",
        description: "处理通用项目工作",
        icon: "🐙",
        avatar_url: null,
      },
      {
        name: "planner",
        display_name: "规划师",
        description: "拆解里程碑和事项",
        icon: "📋",
        avatar_url: "/api/agents/planner/avatar",
      },
    ],
    isLoading: false,
    error: null,
  },
}));

vi.mock("react-router-dom", async () => {
  const actual =
    await vi.importActual<Record<string, unknown>>("react-router-dom");
  return { ...actual, useNavigate: () => mocks.navigate };
});

vi.mock("@/core/projects/hooks", () => ({
  DEFAULT_PROJECT_AGENT_ID: "general",
  useCreateProject: () => ({
    mutate: mocks.createProject,
    isPending: false,
  }),
}));

vi.mock("@/core/agents", () => ({
  useAgents: () => mocks.agentState,
}));

vi.mock("@/core/agents/active", () => ({
  useActiveAgentId: () => "general",
}));

vi.mock("@/components/workspace/sidebar-footer", () => ({
  AgentAvatar: ({ agent }: { agent?: { display_name?: string } }) => (
    <span aria-hidden="true">{agent?.display_name?.slice(0, 1)}</span>
  ),
}));

import { CreateProjectDialog } from "./create-project-dialog";

function DialogHarness() {
  const [open, setOpen] = useState(true);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        reopen
      </button>
      <CreateProjectDialog open={open} onOpenChange={setOpen} />
    </>
  );
}

describe("CreateProjectDialog", () => {
  beforeEach(() => {
    mocks.createProject.mockReset();
    mocks.navigate.mockReset();
  });

  it("clears an abandoned draft before the dialog is reopened", async () => {
    const user = userEvent.setup();
    renderWithProviders(<DialogHarness />, { locale: "zh-CN" });

    const name = screen.getByPlaceholderText("项目名称");
    await user.type(name, "不会创建的项目");
    await user.click(screen.getByRole("button", { name: "规划师" }));
    await user.click(
      screen.getByRole("switch", { name: "进入工作群后立即邀请" }),
    );
    await user.click(screen.getByRole("button", { name: "取消" }));
    await user.click(screen.getByRole("button", { name: "reopen" }));

    expect(screen.getByPlaceholderText("项目名称")).toHaveValue("");
    expect(screen.getByRole("button", { name: "通用助手" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "规划师" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(
      screen.getByRole("switch", { name: "进入工作群后立即邀请" }),
    ).not.toBeChecked();
  });

  it("does not submit an empty project from the Enter key", () => {
    renderWithProviders(<CreateProjectDialog open onOpenChange={vi.fn()} />, {
      locale: "zh-CN",
    });

    fireEvent.keyDown(screen.getByPlaceholderText("项目名称"), {
      key: "Enter",
    });

    expect(mocks.createProject).not.toHaveBeenCalled();
  });

  it("creates immediately with the default AI collaborator", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CreateProjectDialog open onOpenChange={vi.fn()} />, {
      locale: "zh-CN",
    });

    await user.type(screen.getByPlaceholderText("项目名称"), "发布新版");
    await user.click(screen.getByRole("button", { name: "创建项目" }));

    expect(mocks.createProject).toHaveBeenCalledWith(
      {
        name: "发布新版",
        icon: "📁",
        category: undefined,
        initialAgents: [
          {
            id: "general",
            displayName: "通用助手",
            description: "处理通用项目工作",
            avatarUrl: null,
            icon: "🐙",
          },
        ],
      },
      expect.objectContaining({
        onSuccess: expect.any(Function),
        onError: expect.any(Function),
      }),
    );
  });

  it("keeps the White Ghost leader first and adds other roles as members", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CreateProjectDialog open onOpenChange={vi.fn()} />, {
      locale: "zh-CN",
    });

    expect(screen.getByRole("button", { name: "通用助手" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await user.click(screen.getByRole("button", { name: "规划师" }));
    expect(screen.getByRole("button", { name: "通用助手" })).toBeDisabled();
    await user.type(screen.getByPlaceholderText("项目名称"), "增长实验");
    await user.click(screen.getByRole("button", { name: "创建项目" }));

    expect(mocks.createProject).toHaveBeenCalledWith(
      expect.objectContaining({
        initialAgents: [
          {
            id: "general",
            displayName: "通用助手",
            description: "处理通用项目工作",
            avatarUrl: null,
            icon: "🐙",
          },
          {
            id: "planner",
            displayName: "规划师",
            description: "拆解里程碑和事项",
            avatarUrl: "/api/agents/planner/avatar",
            icon: "📋",
          },
        ],
      }),
      expect.any(Object),
    );
  });

  it("states that people join after creation and the creator is owner", () => {
    renderWithProviders(<CreateProjectDialog open onOpenChange={vi.fn()} />, {
      locale: "zh-CN",
    });

    expect(screen.getByText("真人成员")).toBeInTheDocument();
    expect(screen.getByText("创建后邀请")).toBeInTheDocument();
    expect(screen.getByText("项目负责人 · 群主")).toBeInTheDocument();
    expect(
      screen.getByText(/再通过安全邀请链接选择成员或访客/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("switch", { name: "进入工作群后立即邀请" }),
    ).not.toBeChecked();
  });

  it("requests the human invite dialog through navigation state when opted in", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CreateProjectDialog open onOpenChange={vi.fn()} />, {
      locale: "zh-CN",
    });

    await user.click(
      screen.getByRole("switch", { name: "进入工作群后立即邀请" }),
    );
    await user.type(screen.getByPlaceholderText("项目名称"), "协作项目");
    await user.click(screen.getByRole("button", { name: "创建项目" }));
    const callbacks = mocks.createProject.mock.calls[0]?.[1] as {
      onSuccess: (home: { threadId: string }) => void;
    };
    act(() => callbacks.onSuccess({ threadId: "thread/project" }));

    expect(mocks.navigate).toHaveBeenCalledWith(
      "/workspace/realtime/thread%2Fproject",
      {
        state: {
          openProjectWorkbench: true,
          openHumanInviteAfterCreate: true,
        },
      },
    );
  });
});
