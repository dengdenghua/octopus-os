import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import {
  DesktopStartGuide,
  OPEN_DESKTOP_START_GUIDE_EVENT,
  STARTER_TASK_PROMPT,
} from "./desktop-start-guide";

beforeEach(() => localStorage.clear());
const props = () => ({
  identity: "tester",
  completedTasks: 0,
  onStorage: vi.fn(),
  onModel: vi.fn(),
  onPermissions: vi.fn(),
  onStart: vi.fn(),
  onResults: vi.fn(),
  onDatabase: vi.fn(),
  onApps: vi.fn(),
  children: <div>桌面小组件</div>,
});

it("opens every required first-use setting and drafts a task without sending it", () => {
  const actions = props();
  render(<DesktopStartGuide {...actions} />);
  expect(actions.onStart).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "1. 数据目录与共享" }));
  expect(actions.onStorage).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole("button", { name: "2. 模型与连接" }));
  expect(actions.onModel).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole("button", { name: "3. 执行与安全权限" }));
  expect(actions.onPermissions).toHaveBeenCalledOnce();
  fireEvent.click(
    screen.getByRole("button", { name: "4. 在 Agent 工作台准备示例任务" }),
  );
  expect(actions.onStart).toHaveBeenCalledWith(STARTER_TASK_PROMPT);
});

it("introduces result and application entry points after a task finishes", () => {
  const actions = props();
  const view = render(<DesktopStartGuide {...actions} />);
  fireEvent.click(
    screen.getByRole("button", { name: "4. 在 Agent 工作台准备示例任务" }),
  );
  view.rerender(<DesktopStartGuide {...actions} completedTasks={1} />);
  fireEvent.click(screen.getByRole("button", { name: "查看任务结果" }));
  expect(actions.onResults).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole("button", { name: "添加常用应用" }));
  expect(actions.onApps).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole("button", { name: "知道了" }));
  expect(screen.getByText("桌面小组件")).toBeInTheDocument();
});

it("allows skipping and keeps the preference scoped to the account", () => {
  const actions = props();
  const view = render(<DesktopStartGuide {...actions} />);
  fireEvent.click(screen.getByRole("button", { name: "跳过引导" }));
  view.rerender(<DesktopStartGuide {...actions} identity="another-user" />);
  expect(screen.getByRole("button", { name: "跳过引导" })).toBeInTheDocument();
  view.rerender(<DesktopStartGuide {...actions} />);
  expect(screen.getByText("桌面小组件")).toBeInTheDocument();
});

it("does not interrupt existing users with completed tasks", () => {
  render(<DesktopStartGuide {...props()} completedTasks={2} />);
  expect(
    screen.queryByRole("button", {
      name: "4. 在 Agent 工作台准备示例任务",
    }),
  ).not.toBeInTheDocument();
});

it("lets an existing user reopen and close the guide on demand", () => {
  render(<DesktopStartGuide {...props()} completedTasks={2} />);
  expect(screen.getByText("桌面小组件")).toBeInTheDocument();

  fireEvent(window, new Event(OPEN_DESKTOP_START_GUIDE_EVENT));
  expect(
    screen.getByRole("button", { name: "1. 数据目录与共享" }),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "关闭引导" }));
  expect(screen.getByText("桌面小组件")).toBeInTheDocument();
});
