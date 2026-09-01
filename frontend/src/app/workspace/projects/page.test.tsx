import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

const toastMocks = vi.hoisted(() => ({
  error: vi.fn(),
  success: vi.fn(),
}));

vi.mock("sonner", () => ({ toast: toastMocks }));
vi.mock("@/core/auth/api", () => ({
  authHeaders: () => ({ Authorization: "Bearer test-token" }),
  getToken: () => "test-token",
  jsonAuthHeaders: () => ({
    Authorization: "Bearer test-token",
    "Content-Type": "application/json",
  }),
}));
vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
  getEchoBaseURL: () => "",
}));
vi.mock("@/components/workspace/create-project-dialog", () => ({
  CreateProjectDialog: () => null,
}));

import ProjectsPage from "./page";

const SECRET_RUNTIME_ERROR =
  "RuntimeError: sub-agent runner not configured; call set_sub_agent_runner(fn) during bootstrap";

function jsonResponse(
  value: unknown,
  init: ResponseInit = { status: 200 },
): Response {
  return new Response(JSON.stringify(value), {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...Object.fromEntries(new Headers(init.headers).entries()),
    },
  });
}

function projectDetail(options?: {
  risks?: Array<{
    type: "milestone" | "task";
    task?: string;
    milestone?: string;
    health: string;
    detail: string;
  }>;
  actions?: Array<{
    action: string;
    label: string;
    api: { method: string; path: string };
  }>;
}) {
  return {
    project: {
      id: "project-1",
      name: "Release hardening",
      goal: "Ship safely",
      status: "running",
      owner: "Eve",
      created_at: "2026-08-20T10:00:00Z",
      started_at: "2026-08-20T11:00:00Z",
      finished_at: "",
    },
    milestones: [],
    tasks: {},
    pm: {
      project_id: "project-1",
      name: "Release hardening",
      status: "running",
      overall_progress: 0,
      done_tasks: 0,
      total_tasks: 0,
      total_estimate: 0,
      remaining_estimate: 0,
      milestones: [],
      burndown: [],
      risks: options?.risks ?? [],
      blockers: [],
      overdue: [],
      next_actions: [],
      assignments: {},
    },
    retro: null,
    available_actions: [],
    action_specs: options?.actions ?? [],
  };
}

describe("ProjectsPage production error states", () => {
  const originalFetch = globalThis.fetch;
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    fetchMock.mockReset();
    toastMocks.error.mockReset();
    toastMocks.success.mockReset();
    globalThis.fetch = fetchMock;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("keeps the loading state separate from empty content", () => {
    fetchMock.mockReturnValue(new Promise<Response>(() => {}));

    renderWithProviders(<ProjectsPage />, { locale: "zh-CN" });

    expect(screen.getByText("加载中…")).toBeInTheDocument();
    expect(screen.queryByText(/还没有项目/)).not.toBeInTheDocument();
  });

  it("shows a friendly list error and only exposes a safe trace id", async () => {
    fetchMock.mockResolvedValue(
      new Response(SECRET_RUNTIME_ERROR, {
        status: 500,
        headers: { "X-Request-Id": "request-safe-123" },
      }),
    );

    renderWithProviders(<ProjectsPage />, { locale: "zh-CN" });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("项目加载失败，请稍后重试。");
    expect(alert).toHaveTextContent("追踪 ID：request-safe-123");
    expect(alert).not.toHaveTextContent(SECRET_RUNTIME_ERROR);
    expect(screen.queryByText(/还没有项目/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  });

  it("uses the genuine empty state only after a successful empty response", async () => {
    fetchMock.mockResolvedValue(jsonResponse([]));

    renderWithProviders(<ProjectsPage />, { locale: "zh-CN" });

    expect(
      screen.getByRole("heading", { level: 1, name: "🗂️ 项目管理" }),
    ).toBeInTheDocument();
    expect(await screen.findByText(/还没有项目/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("sanitizes detail request failures", async () => {
    fetchMock.mockImplementation((input) => {
      const url = String(input);
      if (url === "/api/projects") {
        return Promise.resolve(
          jsonResponse([
            { id: "project-1", name: "Release hardening", status: "running" },
          ]),
        );
      }
      return Promise.resolve(
        new Response(SECRET_RUNTIME_ERROR, {
          status: 500,
          headers: { "X-Trace-Id": "trace-safe-456" },
        }),
      );
    });

    renderWithProviders(<ProjectsPage />, { locale: "zh-CN" });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("项目加载失败，请稍后重试。");
    expect(alert).toHaveTextContent("追踪 ID：trace-safe-456");
    expect(alert).not.toHaveTextContent(SECRET_RUNTIME_ERROR);
  });

  it("replaces backend risk detail with stable user-facing copy", async () => {
    fetchMock.mockImplementation((input) => {
      const url = String(input);
      if (url === "/api/projects") {
        return Promise.resolve(
          jsonResponse([
            { id: "project-1", name: "Release hardening", status: "running" },
          ]),
        );
      }
      return Promise.resolve(
        jsonResponse(
          projectDetail({
            risks: [
              {
                type: "task",
                task: "Run delegated task",
                health: "failed",
                detail: `${SECRET_RUNTIME_ERROR}; trace_id=risk-safe-789`,
              },
            ],
          }),
        ),
      );
    });

    const { container } = renderWithProviders(<ProjectsPage />, {
      locale: "zh-CN",
    });

    expect(await screen.findByText("Run delegated task")).toBeInTheDocument();
    expect(
      screen.getByText("任务执行失败或受阻，请检查配置后重试。"),
    ).toBeInTheDocument();
    expect(screen.getByText(/追踪 ID：/)).toHaveTextContent("risk-safe-789");
    expect(container).not.toHaveTextContent(SECRET_RUNTIME_ERROR);
    expect(container.querySelector("main")).toBeNull();
  });

  it("does not put an action response body into the toast", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation((input) => {
      const url = String(input);
      if (url === "/api/projects") {
        return Promise.resolve(
          jsonResponse([
            { id: "project-1", name: "Release hardening", status: "running" },
          ]),
        );
      }
      if (url === "/api/projects/project-1/run") {
        return Promise.resolve(
          new Response(SECRET_RUNTIME_ERROR, {
            status: 500,
            headers: { "X-Trace-Id": "action-safe-321" },
          }),
        );
      }
      return Promise.resolve(
        jsonResponse(
          projectDetail({
            actions: [
              {
                action: "run",
                label: "Run project",
                api: { method: "POST", path: "/api/projects/project-1/run" },
              },
            ],
          }),
        ),
      );
    });

    renderWithProviders(<ProjectsPage />, { locale: "zh-CN" });
    await user.click(
      await screen.findByRole("button", { name: "Run project" }),
    );

    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith(
        "操作失败，请稍后重试。 追踪 ID：action-safe-321",
      ),
    );
    expect(toastMocks.error).not.toHaveBeenCalledWith(
      expect.stringContaining(SECRET_RUNTIME_ERROR),
    );
  });
});
