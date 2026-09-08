import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import {
  isModuleEnabled,
  setModuleStateProvider,
} from "@/core/modules/enabled-modules";
import { LocalDatabaseApp, LocalDatabaseSurface } from "./local-database-app";

vi.mock("./local-database-content", () => ({
  default: function Content() {
    const location = useLocation();
    return <div data-testid="database-content">{location.search}</div>;
  },
}));
vi.mock("@/core/agents/active", () => ({ useActiveAgentId: () => "general" }));

it("returns to the original artifact reference without guessing its workspace area", () => {
  const artifact = "workspace-output:final:报告 #1.txt";
  render(
    <MemoryRouter
      initialEntries={[
        `/workspace/storage?${new URLSearchParams({ sourceThread: "task", sourceArtifact: artifact })}`,
      ]}
    >
      <LocalDatabaseSurface />
    </MemoryRouter>,
  );
  const href = screen
    .getByRole("link", { name: "返回原产物" })
    .getAttribute("href")!;
  const route = new URL(href.slice(1), "https://echo.test");
  expect(route.pathname).toBe("/workspace/realtime/task");
  expect(route.searchParams.get("artifact")).toBe(artifact);
});

it("keeps a return link to the original task when locating a file", async () => {
  render(
    <MemoryRouter
      initialEntries={[
        "/workspace/storage?library=computer&file=%2Fhome%2Freport.txt&sourceThread=original-task",
      ]}
    >
      <LocalDatabaseSurface />
    </MemoryRouter>,
  );
  expect(screen.getByRole("link", { name: "返回原任务" })).toHaveAttribute(
    "href",
    "#/workspace/realtime/original-task",
  );
  expect(await screen.findByTestId("database-content")).toHaveTextContent(
    "sourceThread=original-task",
  );
});

it("keeps the workbench presentation on source return links", () => {
  render(
    <MemoryRouter
      initialEntries={[
        "/workspace/storage?library=computer&sourceThread=original-task&presentation=workbench",
      ]}
    >
      <LocalDatabaseSurface />
    </MemoryRouter>,
  );
  expect(screen.getByRole("link", { name: "返回原任务" })).toHaveAttribute(
    "href",
    "#/workspace/realtime/original-task?presentation=workbench",
  );
});

beforeEach(() => {
  let disabled: string[] = ["local-database"];
  let overrides: Record<string, Record<string, boolean>> = {};
  setModuleStateProvider({
    readDisabled: () => disabled,
    writeDisabled: (ids) => {
      disabled = ids;
    },
    readOverrides: () => overrides,
    writeOverrides: (value) => {
      overrides = value;
    },
  });
});

it("opens a standalone database and navigates without replacing the desktop route", async () => {
  function OuterLocation() {
    return <div data-testid="outer-route">{useLocation().pathname}</div>;
  }
  render(
    <MemoryRouter initialEntries={["/desktop"]}>
      <OuterLocation />
      <LocalDatabaseApp />
    </MemoryRouter>,
  );
  expect(await screen.findByTestId("database-content")).toHaveTextContent(
    "library=overview",
  );
  expect(
    screen.getByRole("heading", { name: "本地数据库" }),
  ).toBeInTheDocument();
  await userEvent.click(
    screen.getByRole("button", { name: "视频", exact: true }),
  );
  expect(screen.getByTestId("database-content")).toHaveTextContent(
    "library=videos",
  );
  expect(screen.getByTestId("outer-route")).toHaveTextContent("/desktop");
  expect(
    screen.queryByTestId("embedded-agent-workspace"),
  ).not.toBeInTheDocument();
});

it("adds and removes the workbench shortcut without closing the system app", async () => {
  render(<LocalDatabaseApp />);
  await userEvent.click(
    screen.getByRole("button", { name: "添加到工作台侧边栏" }),
  );
  expect(isModuleEnabled("local-database", "general")).toBe(true);
  expect(isModuleEnabled("local-database", "coder")).toBe(true);
  await userEvent.click(
    screen.getByRole("button", { name: "从工作台侧边栏移除" }),
  );
  expect(isModuleEnabled("local-database", "general")).toBe(false);
  expect(await screen.findByTestId("database-content")).toBeInTheDocument();
});

it("uses the same database surface for workbench deep links", async () => {
  render(
    <MemoryRouter initialEntries={["/workspace/storage?library=docs"]}>
      <LocalDatabaseSurface />
    </MemoryRouter>,
  );
  expect(await screen.findByTestId("database-content")).toHaveTextContent(
    "library=docs",
  );
  await userEvent.click(
    screen.getByRole("button", { name: "图片", exact: true }),
  );
  expect(screen.getByTestId("database-content")).toHaveTextContent(
    "library=images",
  );
});

it("offers a workbench switch when opened without a desktop callback", async () => {
  render(
    <MemoryRouter initialEntries={["/workspace/storage?library=images"]}>
      <LocalDatabaseSurface />
    </MemoryRouter>,
  );

  await userEvent.click(screen.getByRole("button", { name: "在工作台中打开" }));

  expect(await screen.findByTestId("database-content")).toHaveTextContent(
    "library=images",
  );
  expect(screen.getByTestId("database-content")).toHaveTextContent(
    "presentation=workbench",
  );
});

it("carries the current database category into the workbench even when unpinned", async () => {
  const openWorkspace = vi.fn();
  function Location() {
    const location = useLocation();
    return (
      <output data-testid="outer-location">
        {location.pathname}
        {location.search}
      </output>
    );
  }
  function Host() {
    return <LocalDatabaseApp onOpenWorkbench={openWorkspace} />;
  }
  render(
    <MemoryRouter initialEntries={["/desktop"]}>
      <Location />
      <Host />
    </MemoryRouter>,
  );
  await userEvent.click(
    screen.getByRole("button", { name: "图片", exact: true }),
  );
  expect(isModuleEnabled("local-database", "general")).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "在工作台中打开" }));
  expect(openWorkspace).toHaveBeenCalledWith(
    "/workspace/storage?surface=company&library=images",
  );
  expect(screen.getByTestId("outer-location")).toHaveTextContent(/^\/desktop$/);
});

it("returns to the source task through the desktop window host", async () => {
  const openWorkspace = vi.fn();
  render(
    <MemoryRouter initialEntries={["/desktop"]}>
      <LocalDatabaseApp
        initialRoute="/workspace/storage?library=images&sourceThread=original-task"
        onOpenWorkbench={openWorkspace}
      />
    </MemoryRouter>,
  );
  await userEvent.click(screen.getByRole("link", { name: "返回原任务" }));
  expect(openWorkspace).toHaveBeenCalledWith(
    "/workspace/realtime/original-task",
  );
  expect(await screen.findByTestId("database-content")).toHaveTextContent(
    "library=images",
  );
});
