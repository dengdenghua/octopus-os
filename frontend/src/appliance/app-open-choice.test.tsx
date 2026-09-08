import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { AppOpenChoice } from "./app-open-choice";

it("lets the user select a presentation without launching both", async () => {
  const standalone = vi.fn();
  const workbench = vi.fn();
  render(
    <AppOpenChoice
      name="本地数据库"
      onClose={vi.fn()}
      onStandalone={standalone}
      onWorkbench={workbench}
    />,
  );
  const dialog = screen.getByRole("dialog", { name: "打开本地数据库" });
  expect(dialog).toBeInTheDocument();
  expect(dialog.closest("[data-app-open-choice-host]")).not.toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "在工作台中打开" }));
  expect(workbench).toHaveBeenCalledOnce();
  expect(standalone).not.toHaveBeenCalled();
});

it("supports the independent window and cancellation", async () => {
  const standalone = vi.fn();
  const close = vi.fn();
  render(
    <AppOpenChoice
      name="项目管理"
      onClose={close}
      onStandalone={standalone}
      onWorkbench={vi.fn()}
    />,
  );
  await userEvent.click(screen.getByRole("button", { name: "独立窗口打开" }));
  expect(standalone).toHaveBeenCalledOnce();
  await userEvent.keyboard("{Escape}");
  expect(close).toHaveBeenCalledOnce();
});

it("only shows presentations declared by the app", () => {
  render(
    <AppOpenChoice
      name="工作台应用"
      supportedPresentations={["workbench"]}
      onClose={vi.fn()}
      onStandalone={vi.fn()}
      onWorkbench={vi.fn()}
    />,
  );
  expect(screen.queryByRole("button", { name: "独立窗口打开" })).toBeNull();
  expect(
    screen.getByRole("button", { name: "在工作台中打开" }),
  ).toBeInTheDocument();
  expect(
    screen.getByText("应用会嵌入工作台，并复用当前任务与上下文。"),
  ).toBeInTheDocument();
});

it("uses the primary presentation for legacy registrations", () => {
  render(
    <AppOpenChoice
      name="旧工作台应用"
      presentation="workbench"
      onClose={vi.fn()}
      onStandalone={vi.fn()}
      onWorkbench={vi.fn()}
    />,
  );
  expect(screen.queryByRole("button", { name: "独立窗口打开" })).toBeNull();
  expect(
    screen.getByRole("button", { name: "在工作台中打开" }),
  ).toBeInTheDocument();
});

it("explains when an app has no usable presentation", () => {
  render(
    <AppOpenChoice
      name="暂不可用的应用"
      supportedPresentations={[]}
      onClose={vi.fn()}
      onStandalone={vi.fn()}
      onWorkbench={vi.fn()}
    />,
  );
  expect(
    screen.getByText("当前应用暂未提供可用的打开方式，请稍后重试。"),
  ).toHaveAttribute("role", "status");
  expect(screen.queryByRole("button", { name: "独立窗口打开" })).toBeNull();
  expect(screen.queryByRole("button", { name: "在工作台中打开" })).toBeNull();
});
