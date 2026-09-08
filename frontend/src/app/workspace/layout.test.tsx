import { act, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import type * as ReactRouterDom from "react-router-dom";
import type * as AgentModule from "@/core/agents";
import type * as ActiveModule from "@/core/agents/active";
import type * as TaskSpaceModule from "@/appliance/task-space";

import { renderWithProviders } from "@/test/harness";
import { STUB_RESPONSE_EVENT } from "@/core/api/client";
import { eventBus } from "@/core/events";

import WorkspaceLayout from "./layout";

const activeAgentMock = vi.hoisted(() => vi.fn(() => null));

vi.mock("@/core/agents", async (importOriginal) => ({
  ...(await importOriginal<typeof AgentModule>()),
  useActiveAgentId: activeAgentMock,
  useAgents: () => ({
    agents: [{ name: "general" }, { name: "market_researcher" }],
    isLoading: false,
    error: null,
  }),
}));

vi.mock("@/core/agents/active", async (importOriginal) => ({
  ...(await importOriginal<typeof ActiveModule>()),
  useActiveAgentId: activeAgentMock,
}));

vi.mock("react-router-dom", async () => {
  const actual =
    await vi.importActual<typeof ReactRouterDom>("react-router-dom");
  return {
    ...actual,
    Outlet: () => {
      const location = actual.useLocation();
      return (
        <div data-testid="workspace-location">
          {location.pathname}
          {location.search}
        </div>
      );
    },
  };
});

vi.mock("@/components/workspace/workspace-sidebar", () => ({
  WorkspaceSidebar: () => <aside>sidebar</aside>,
}));

vi.mock("@/appliance/system-model-status", () => ({
  SystemModelStatus: ({ onOpenSettings }: { onOpenSettings: () => void }) => (
    <button onClick={onOpenSettings}>模型与用量</button>
  ),
}));

vi.mock("@/appliance/task-space", async (importOriginal) => {
  const actual = await importOriginal<typeof TaskSpaceModule>();
  return {
    ...actual,
    useEchoTaskProjection: () => ({
      projection: null,
      loading: false,
      error: null,
      refresh: vi.fn(),
      takeover: vi.fn(),
      resumeExecution: vi.fn(),
      decideApproval: vi.fn(),
    }),
  };
});

describe("<WorkspaceLayout /> stub response banner", () => {
  afterEach(() => {
    eventBus.clear();
    vi.unstubAllGlobals();
    window.localStorage.clear();
    activeAgentMock.mockReturnValue(null);
  });

  test("does not show stub response banners by default", () => {
    vi.stubGlobal("localStorage", {
      getItem: vi.fn(() => null),
    });
    renderWithProviders(<WorkspaceLayout />, { locale: "zh-CN" });

    act(() => {
      window.dispatchEvent(
        new CustomEvent(STUB_RESPONSE_EVENT, {
          detail: { method: "GET", path: "/api/account/usage" },
        }),
      );
    });

    expect(screen.queryByText("模拟后端响应")).not.toBeInTheDocument();
  });

  test("applies the active persona's illustration palette to the workspace", () => {
    activeAgentMock.mockReturnValue("market_researcher");

    renderWithProviders(<WorkspaceLayout />, { locale: "zh-CN" });

    expect(screen.getByText("sidebar").parentElement).toHaveAttribute(
      "data-persona-theme",
      "noah",
    );
  });

  test("renders design chat as an embedded surface without duplicating the sidebar", () => {
    renderWithProviders(<WorkspaceLayout />, {
      initialRoute: "/workspace/realtime/new?embedded=design",
      locale: "zh-CN",
    });

    expect(screen.queryByText("sidebar")).not.toBeInTheDocument();
    expect(screen.getByTestId("workspace-location").textContent).toBe(
      "/workspace/realtime/new?embedded=design",
    );
    expect(
      screen
        .getByTestId("workspace-location")
        .closest('[data-slot="sidebar-wrapper"]'),
    ).not.toBeNull();
  });

  test("renders desktop apps as embedded workspaces without duplicating the sidebar", () => {
    renderWithProviders(<WorkspaceLayout />, {
      initialRoute: "/workspace/storage?library=images&embedded=app",
      locale: "zh-CN",
    });

    expect(screen.queryByText("sidebar")).not.toBeInTheDocument();
    expect(screen.getByTestId("workspace-location").textContent).toBe(
      "/workspace/storage?library=images&embedded=app",
    );
  });

  test("shows stub response banners when debug flag is enabled", () => {
    vi.stubGlobal("localStorage", {
      getItem: vi.fn((key: string) =>
        key === "echo.debug.showStubResponses" ? "true" : null,
      ),
    });
    renderWithProviders(<WorkspaceLayout />, { locale: "zh-CN" });

    act(() => {
      window.dispatchEvent(
        new CustomEvent(STUB_RESPONSE_EVENT, {
          detail: { method: "GET", path: "/api/account/usage" },
        }),
      );
    });

    expect(screen.getByText("模拟后端响应")).toBeInTheDocument();
  });

  test("preserves agent and project identity when starting a fresh task", () => {
    renderWithProviders(<WorkspaceLayout />, {
      initialRoute: "/workspace/realtime/existing",
      locale: "zh-CN",
    });

    act(() => {
      eventBus.emit("task:new", {
        agentId: "coder",
        workspacePath: "/Users/example/Public/echo-agent",
      });
    });

    expect(screen.getByTestId("workspace-location").textContent).toBe(
      "/workspace/realtime/new?agent=coder&workspace_path=%2FUsers%2Fexample%2FPublic%2Fecho-agent",
    );
  });
});

test("leaves system navigation and model controls to the desktop host", () => {
  renderWithProviders(<WorkspaceLayout />);
  expect(
    screen.queryByRole("banner", { name: "系统模型状态" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "切换到桌面" }),
  ).not.toBeInTheDocument();
});

test("does not duplicate desktop model controls inside an application window", () => {
  renderWithProviders(<WorkspaceLayout embeddedInWindow />);
  expect(
    screen.queryByRole("button", { name: "模型与用量" }),
  ).not.toBeInTheDocument();
});

test("keeps task space visible in the workbench system status bar", () => {
  renderWithProviders(<WorkspaceLayout />, {
    initialRoute: "/workspace/projects?presentation=workbench",
  });
  expect(
    screen.getByRole("banner", { name: "工作台系统状态" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "任务空间" })).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "模型与用量" }),
  ).toBeInTheDocument();
});
