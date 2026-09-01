import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TaskBoardStats, UnifiedTask } from "@/core/task-board/types";
import { renderWithProviders } from "@/test/harness";

import { TaskBoard } from "./index";
import { TaskCard } from "./task-card";
import { TimelineView } from "./timeline-view";

const hookMocks = vi.hoisted(() => ({
  useTaskBoardTasks: vi.fn(),
  useTaskBoardStats: vi.fn(),
  useTaskBoardTimeline: vi.fn(),
}));

vi.mock("@/core/task-board/hooks", () => hookMocks);

const refresh = vi.fn();

const EMPTY_STATS: TaskBoardStats = {
  total: 0,
  by_status: {},
  by_type: {},
  avg_duration_ms: 0,
  success_rate: 0,
  running_count: 0,
  queued_count: 0,
};

function task(overrides: Partial<UnifiedTask> = {}): UnifiedTask {
  return {
    id: "task-1",
    type: "background",
    name: "整理周报",
    status: "running",
    phase: "汇总",
    progress_pct: 40,
    created_at: "2026-07-20T01:00:00.000Z",
    updated_at: "2026-07-20T01:01:00.000Z",
    started_at: "2026-07-20T01:00:10.000Z",
    finished_at: null,
    duration_ms: 50_000,
    error: null,
    extra: {},
    ...overrides,
  };
}

function mockBoard(tasks: UnifiedTask[], error: string | null = null) {
  hookMocks.useTaskBoardTasks.mockReturnValue({
    data: { tasks, total: tasks.length },
    loading: false,
    error,
    refresh,
  });
  hookMocks.useTaskBoardStats.mockReturnValue({
    stats: EMPTY_STATS,
    loading: false,
    refresh: vi.fn(),
  });
  hookMocks.useTaskBoardTimeline.mockReturnValue({
    data: { tasks: [], earliest_ms: 0, latest_ms: 0 },
    loading: false,
    refresh: vi.fn(),
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  mockBoard([]);
});

describe("TaskBoard", () => {
  it("offers keyboard-operable sortable headers with exposed sort direction", async () => {
    const user = userEvent.setup();
    mockBoard([
      task({ id: "alpha", name: "Alpha", created_at: "2026-07-19T00:00:00Z" }),
      task({ id: "beta", name: "Beta", created_at: "2026-07-20T00:00:00Z" }),
    ]);

    renderWithProviders(<TaskBoard initialViewMode="list" />, {
      locale: "zh-CN",
    });

    expect(screen.getByRole("group", { name: "按任务类型筛选" })).toBeVisible();
    expect(screen.getByRole("button", { name: "全部" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(
      screen.getByRole("columnheader", { name: "创建时间" }),
    ).toHaveAttribute("aria-sort", "descending");

    await user.click(screen.getByRole("button", { name: "名称" }));
    expect(screen.getByRole("columnheader", { name: "名称" })).toHaveAttribute(
      "aria-sort",
      "descending",
    );
    let rows = screen.getAllByRole("row");
    expect(within(rows[1]!).getByText("Beta")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "名称" }));
    expect(screen.getByRole("columnheader", { name: "名称" })).toHaveAttribute(
      "aria-sort",
      "ascending",
    );
    rows = screen.getAllByRole("row");
    expect(within(rows[1]!).getByText("Alpha")).toBeInTheDocument();
  });

  it("replaces raw task API failures with a localized retry state", async () => {
    const user = userEvent.setup();
    mockBoard([], "Failed to fetch tasks: Not Found");

    renderWithProviders(<TaskBoard />, { locale: "zh-CN" });

    expect(screen.getByRole("alert")).toHaveTextContent(
      "任务数据暂时无法加载，请稍后重试。",
    );
    expect(
      screen.queryByText("Failed to fetch tasks: Not Found"),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重试" }));
    expect(refresh).toHaveBeenCalledOnce();
  });
});

describe("TaskCard", () => {
  it("expands and collapses details from the keyboard", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TaskCard task={task()} />, { locale: "zh-CN" });

    const card = screen.getByRole("button", {
      name: "查看任务详情：整理周报",
    });
    expect(card).toHaveAttribute("aria-expanded", "false");

    card.focus();
    await user.keyboard("{Enter}");
    expect(card).toHaveAttribute("aria-expanded", "true");
    expect(screen.getAllByText("汇总")).toHaveLength(2);

    await user.keyboard(" ");
    expect(card).toHaveAttribute("aria-expanded", "false");
  });
});

describe("TimelineView", () => {
  it("exposes localized chart, zoom controls, and a screen-reader task summary", () => {
    renderWithProviders(
      <TimelineView
        data={{
          tasks: [
            {
              id: "timeline-1",
              type: "scheduled",
              name: "每日同步",
              status: "completed",
              start_ms: Date.parse("2026-07-20T01:00:00Z"),
              end_ms: Date.parse("2026-07-20T01:01:00Z"),
              duration_ms: 60_000,
              is_running: false,
            },
          ],
          earliest_ms: Date.parse("2026-07-20T01:00:00Z"),
          latest_ms: Date.parse("2026-07-20T01:01:00Z"),
        }}
      />,
      { locale: "zh-CN" },
    );

    expect(screen.getByRole("img", { name: "任务执行时间线" })).toBeVisible();
    expect(screen.getByRole("button", { name: "缩小" })).toBeVisible();
    expect(
      screen.getByRole("button", { name: "重置时间线缩放，当前 100%" }),
    ).toBeVisible();
    expect(
      screen.getByText(/每日同步, 定时, 已完成, 1m 0s/),
    ).toBeInTheDocument();
  });
});
