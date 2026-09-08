import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { WORKBENCH_BUILTIN_APPS } from "@/core/workbench/apps";
import {
  isModuleEnabled,
  setModuleEnabled,
  resetModuleStateCache,
} from "@/core/modules/enabled-modules";
import { renderWithProviders } from "@/test/harness";
import { CoreAppCard } from "./core-app-card";
import { LocalDatabaseApp } from "./local-database-app";
import { useLocation } from "react-router-dom";

vi.mock("./local-database-content", () => ({ default: () => <div>内容</div> }));

it("shares the sidebar switch between the app center and independent database", async () => {
  resetModuleStateCache();
  setModuleEnabled("local-database", false, "general");
  const onOpen = vi.fn();
  const app = WORKBENCH_BUILTIN_APPS.find(
    (entry) => entry.id === "local-database",
  )!;
  renderWithProviders(
    <>
      <CoreAppCard app={app} onOpen={onOpen} />
      <LocalDatabaseApp />
    </>,
    { locale: "zh-CN" },
  );
  await userEvent.click(
    screen.getByRole("button", { name: "添加到侧栏", exact: true }),
  );
  expect(isModuleEnabled("local-database", "general")).toBe(true);
  await userEvent.click(
    screen.getByRole("button", { name: "从工作台侧边栏移除" }),
  );
  expect(
    screen.getByRole("button", { name: "添加到侧栏", exact: true }),
  ).toHaveAttribute("aria-pressed", "false");
  await userEvent.click(
    screen.getByRole("button", { name: "独立窗口打开", exact: true }),
  );
  expect(onOpen).toHaveBeenCalledWith(app.workspaceRoute);
});

it("opens the same app in either host without changing its sidebar preference", async () => {
  resetModuleStateCache();
  setModuleEnabled("local-database", false, "general");
  const onOpen = vi.fn();
  const onOpenWorkbench = vi.fn();
  const app = WORKBENCH_BUILTIN_APPS.find(
    (entry) => entry.id === "local-database",
  )!;
  renderWithProviders(
    <CoreAppCard app={app} onOpen={onOpen} onOpenWorkbench={onOpenWorkbench} />,
    { locale: "zh-CN" },
  );
  await userEvent.click(screen.getByRole("button", { name: "独立窗口打开" }));
  await userEvent.click(screen.getByRole("button", { name: "在工作台中打开" }));
  expect(onOpen).toHaveBeenCalledWith(app.workspaceRoute);
  expect(onOpenWorkbench).toHaveBeenCalledWith(app.workspaceRoute);
  expect(isModuleEnabled("local-database", "general")).toBe(false);
});

it("keeps the workbench presentation when used without a launcher callback", async () => {
  resetModuleStateCache();
  const app = WORKBENCH_BUILTIN_APPS.find(
    (entry) => entry.id === "local-database",
  )!;
  function LocationProbe() {
    return <output data-testid="location">{useLocation().search}</output>;
  }
  renderWithProviders(
    <>
      <CoreAppCard app={app} />
      <LocationProbe />
    </>,
    { initialRoute: "/workspace/hub?presentation=workbench", locale: "zh-CN" },
  );
  await userEvent.click(screen.getByRole("button", { name: "在工作台中打开" }));
  expect(screen.getByTestId("location")).toHaveTextContent(
    "presentation=workbench",
  );
});
